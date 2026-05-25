import json
import logging
import os
import threading
import time
import uuid
import requests
from typing import Dict, List, Any, Optional

from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home

logger = logging.getLogger("hermes.plugins.d1_mem")

class _D1Client:
    """Minimal client for Cloudflare D1 REST API."""
    def __init__(self, account_id: str, api_token: str, database_id: str):
        self.account_id = account_id
        self.api_token = api_token
        self.database_id = database_id
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    def _execute(self, sql: str, params: list = None) -> list:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        
        try:
            resp = requests.post(self.base_url, headers=self.headers, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data.get("result", [{}])[0].get("results", [])
            else:
                logger.error(f"D1 error: {data.get('errors')}")
                return []
        except Exception as e:
            logger.error(f"D1 API Exception: {e}")
            return []

    def ensure_table(self):
        """Create the table with FTS5 virtual table for searching."""
        self._execute("""
        CREATE TABLE IF NOT EXISTS hermes_memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            metadata TEXT,
            scope TEXT,
            created_at INTEGER
        )
        """)
        
        self._execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS hermes_memories_fts USING fts5(
            content,
            id UNINDEXED,
            content='hermes_memories',
            content_rowid='rowid'
        )
        """)
        
        # Trigger to keep FTS table in sync
        self._execute("""
        CREATE TRIGGER IF NOT EXISTS hermes_memories_ai AFTER INSERT ON hermes_memories BEGIN
            INSERT INTO hermes_memories_fts(rowid, id, content) VALUES (new.rowid, new.id, new.content);
        END;
        """)
        
        self._execute("""
        CREATE TRIGGER IF NOT EXISTS hermes_memories_ad AFTER DELETE ON hermes_memories BEGIN
            INSERT INTO hermes_memories_fts(hermes_memories_fts, rowid, id, content) VALUES('delete', old.rowid, old.id, old.content);
        END;
        """)

    def insert(self, memory_id: str, content: str, metadata: dict, scope: str):
        self._execute(
            "INSERT INTO hermes_memories (id, content, metadata, scope, created_at) VALUES (?, ?, ?, ?, ?)",
            [memory_id, content, json.dumps(metadata), scope, int(time.time())]
        )

    def search(self, query: str, scope: str, limit: int = 5) -> list:
        # FTS5 search
        sql = """
        SELECT m.id, m.content, m.metadata, m.scope, m.created_at, bm25(f) as score
        FROM hermes_memories_fts f
        JOIN hermes_memories m ON f.id = m.id
        WHERE f.content MATCH ? AND m.scope LIKE ?
        ORDER BY score
        LIMIT ?
        """
        # Append % for a prefix match on the scope (tenant scope search)
        scope_pattern = scope + "%" if not scope.endswith("%") else scope
        
        # Simple FTS5 syntax escaping (basic)
        safe_query = '"' + query.replace('"', '""') + '"'
        
        return self._execute(sql, [safe_query, scope_pattern, limit])
        
    def delete(self, memory_id: str):
        self._execute("DELETE FROM hermes_memories WHERE id = ?", [memory_id])


class D1MemoryProvider(MemoryProvider):
    """Cloudflare D1 backed external memory provider."""
    
    @property
    def name(self) -> str:
        return "d1-mem"

    def is_available(self) -> bool:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID")
        return bool(account_id and api_token and database_id)

    def initialize(self):
        self.account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        self.api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        self.database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID")
        
        if not self.is_available():
            logger.warning("d1-mem credentials missing in env")
            return
            
        self.client = _D1Client(self.account_id, self.api_token, self.database_id)
        
        # Fire-and-forget init to not block startup
        def _init_db():
            try:
                self.client.ensure_table()
                logger.info("d1-mem table initialized")
            except Exception as e:
                logger.error(f"Failed to initialize D1 tables: {e}")
                
        threading.Thread(target=_init_db, daemon=True).start()

    def _get_scope(self, session: Optional[Any] = None) -> str:
        # simple scope: default to hermes/default
        if not session:
            return "hermes/__default__"
        tenant = getattr(session, 'user_id', '__default__')
        agent = getattr(session, 'agent_identity', 'hermes')
        return f"hermes/{tenant}/{agent}"

    def system_prompt_block(self, session: Any) -> Optional[str]:
        # Return pre-fetched memories if they exist
        if hasattr(session, 'd1_mem_prefetch') and session.d1_mem_prefetch:
            memories = session.d1_mem_prefetch
            out = "Relevant past memories (from D1 Cloudflare):\n"
            for m in memories:
                out += f"- [{m['id'][:8]}] {m['content']}\n"
            return out
        return None

    def queue_prefetch(self, session: Any, last_user_message: str):
        """Asynchronously prefetch relevant memories."""
        if not self.is_available():
            return
            
        def _fetch():
            try:
                scope = self._get_scope(session)
                # To search across agents for the tenant, we just use tenant scope
                tenant_scope = "/".join(scope.split("/")[:2]) + "/%"
                results = self.client.search(last_user_message, tenant_scope, limit=3)
                session.d1_mem_prefetch = results
            except Exception as e:
                logger.error(f"D1 prefetch failed: {e}")
                
        threading.Thread(target=_fetch, daemon=True).start()

    def sync_turn(self, session: Any, user_text: str, assistant_text: str):
        """Asynchronously store the turn to D1 if significant."""
        if not self.is_available():
            return
            
        if len(user_text) < 10:
            return # Too short
            
        def _sync():
            try:
                scope = self._get_scope(session)
                mem_id = str(uuid.uuid4())
                content = f"User said: {user_text}\nAssistant replied: {assistant_text}"
                metadata = {"source": "sync_turn", "session_id": getattr(session, 'id', 'unknown')}
                self.client.insert(mem_id, content, metadata, scope)
            except Exception as e:
                logger.error(f"D1 sync_turn failed: {e}")
                
        threading.Thread(target=_sync, daemon=True).start()

    def get_tool_schemas(self, session: Any) -> List[Dict]:
        if not self.is_available():
            return []
            
        return [
            {
                "type": "function",
                "function": {
                    "name": "d1_remember",
                    "description": "Store a specific fact or detail into Cloudflare D1 long-term memory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "The fact to remember"},
                            "topic": {"type": "string", "description": "Optional category or topic"}
                        },
                        "required": ["content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "d1_search",
                    "description": "Search Cloudflare D1 long-term memory for relevant facts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search keywords"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

    def handle_tool_call(self, session: Any, name: str, args: Dict) -> Any:
        if name == "d1_remember":
            mem_id = str(uuid.uuid4())
            content = args.get("content")
            metadata = {"source": "d1_remember", "topic": args.get("topic", "")}
            scope = self._get_scope(session)
            self.client.insert(mem_id, content, metadata, scope)
            return {"status": "success", "id": mem_id, "message": "Saved to D1 memory"}
            
        elif name == "d1_search":
            query = args.get("query")
            scope = "/".join(self._get_scope(session).split("/")[:2]) + "/%"
            results = self.client.search(query, scope, limit=5)
            
            out = []
            for r in results:
                try:
                    meta = json.loads(r.get("metadata", "{}"))
                except:
                    meta = {}
                out.append({
                    "id": r.get("id"),
                    "content": r.get("content"),
                    "topic": meta.get("topic", "")
                })
            return {"results": out}

    def on_memory_write(self, session: Any, content: str, target: str):
        """Mirror standard hermes memory writes to D1."""
        if not self.is_available():
            return
            
        def _write():
            mem_id = str(uuid.uuid4())
            metadata = {"source": "hermes_builtin", "target": target}
            scope = self._get_scope(session)
            self.client.insert(mem_id, content, metadata, scope)
            
        threading.Thread(target=_write, daemon=True).start()

    def run_doctor(self) -> Dict:
        return {
            "status": "available" if self.is_available() else "unavailable",
            "provider": self.name,
            "account_id": bool(self.account_id),
            "api_token": bool(self.api_token),
            "database_id": bool(self.database_id)
        }
