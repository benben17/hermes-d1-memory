import json
import logging
import os
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from .client import D1Client
from .flusher import QueueFlusher
from .normalize import make_memory_record
from .queue_store import QueueStore
from .recall import DEFAULT_ALLOWED_KINDS, build_prompt_block, rank_results

logger = logging.getLogger("hermes.plugins.d1_mem")


class D1MemoryProvider(MemoryProvider):
    """Cloudflare D1 backed external memory provider with durable local queue."""

    def __init__(self):
        self._session_id = ""
        self._user_id = ""
        self._user_id_alt = ""
        self._agent_identity = "hermes"
        self._agent_context = "primary"
        self._prefetch_results: str = ""
        self._hermes_home = os.path.expanduser("~/.hermes")
        self.account_id = ""
        self.api_token = ""
        self.database_id = ""
        self.client: Optional[D1Client] = None
        self.queue_store: Optional[QueueStore] = None
        self.flusher: Optional[QueueFlusher] = None
        self._raw_sync_turn = False
        self._batch_size = 25
        self._flush_interval = 3
        self._max_attempts = 6
        self._prefetch_limit = 4
        self._prefetch_max_chars = 800

    @property
    def name(self) -> str:
        return "d1-mem"

    def is_available(self) -> bool:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID")
        return bool(account_id and api_token and database_id)

    def initialize(self, session_id: str = "", **kwargs) -> None:
        self._session_id = session_id
        self._user_id = kwargs.get("user_id", "") or ""
        self._user_id_alt = kwargs.get("user_id_alt", "") or ""
        self._agent_identity = kwargs.get("agent_identity", "hermes") or "hermes"
        self._agent_context = kwargs.get("agent_context", "primary") or "primary"
        self._hermes_home = kwargs.get("hermes_home") or self._hermes_home
        self.account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        self.api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        self.database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID", "")
        self._raw_sync_turn = os.environ.get("D1_MEM_ENABLE_RAW_SYNC_TURN", "false").lower() in {"1", "true", "yes", "on"}
        self._batch_size = int(os.environ.get("D1_MEM_BATCH_SIZE", "25") or "25")
        self._flush_interval = int(os.environ.get("D1_MEM_FLUSH_INTERVAL", "3") or "3")
        self._max_attempts = int(os.environ.get("D1_MEM_MAX_ATTEMPTS", "6") or "6")
        self._prefetch_limit = int(os.environ.get("D1_MEM_PREFETCH_LIMIT", "4") or "4")
        self._prefetch_max_chars = int(os.environ.get("D1_MEM_PREFETCH_MAX_CHARS", "800") or "800")

        if not self.is_available():
            logger.warning("d1-mem credentials missing in env")
            return

        queue_db_path = os.path.join(self._hermes_home, "memory", "d1_queue.db")
        self.client = D1Client(self.account_id, self.api_token, self.database_id)
        self.client.ensure_schema()
        self.queue_store = QueueStore(queue_db_path)
        self.queue_store.release_sending()
        self.flusher = QueueFlusher(
            self.queue_store,
            self.client,
            batch_size=self._batch_size,
            interval_seconds=self._flush_interval,
            max_attempts=self._max_attempts,
        )
        self.flusher.start()
        logger.info("d1-mem initialized with durable queue at %s", queue_db_path)

    def _writes_enabled(self) -> bool:
        return self.is_available() and self.client is not None and self.queue_store is not None and self._agent_context == "primary"

    def _get_scope(self) -> str:
        tenant = self._user_id or self._user_id_alt or "__default__"
        agent = self._agent_identity or "hermes"
        return f"hermes/{tenant}/{agent}"

    def _tenant_scope(self) -> str:
        scope = self._get_scope()
        return "/".join(scope.split("/")[:2]) + "/%"

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return self._prefetch_results or ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self.is_available() or not self.client or not query.strip():
            self._prefetch_results = ""
            return
        try:
            rows = self.client.search_memories(
                query=query,
                scope_prefix=self._tenant_scope(),
                limit=self._prefetch_limit,
                allowed_kinds=DEFAULT_ALLOWED_KINDS,
            )
            ranked = rank_results(rows)
            self._prefetch_results = build_prompt_block(
                ranked,
                max_items=self._prefetch_limit,
                max_chars=self._prefetch_max_chars,
            )
        except Exception as e:
            logger.error("D1 prefetch failed: %s", e)
            self._prefetch_results = ""

    def _enqueue_record(self, record: Dict[str, Any]) -> Optional[str]:
        if not self._writes_enabled():
            return None
        assert self.queue_store is not None
        return self.queue_store.enqueue(record, record["fingerprint"])

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._writes_enabled():
            return
        if self._raw_sync_turn:
            content = f"User said: {user_content}\nAssistant replied: {assistant_content}"
            record = make_memory_record(
                scope=self._get_scope(),
                content=content,
                source="selected_turn_fact",
                session_id=self._session_id or session_id,
                user_id=self._user_id or self._user_id_alt,
                agent_id=self._agent_identity,
                topic="conversation-turn",
                kind="stable_fact",
                metadata={"raw_sync_turn": True},
                importance=1,
            )
            self._enqueue_record(record)
            return

        # Deliberately disabled by default: D1 should not become a full conversation ledger.
        return

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        return [
            {
                "name": "d1_remember",
                "description": "Store a durable fact into Cloudflare D1 long-term memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The durable fact to remember"},
                        "topic": {"type": "string", "description": "Optional topic/category"},
                        "kind": {"type": "string", "description": "Optional kind: user_preference, environment_fact, project_convention, correction, stable_fact, manual_note"},
                        "importance": {"type": "integer", "description": "1-5 priority, default 3"}
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "d1_search",
                "description": "Search Cloudflare D1 durable memory for relevant facts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keywords"},
                        "limit": {"type": "integer", "description": "Maximum results to return (default 5)"}
                    },
                    "required": ["query"]
                }
            }
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "d1_remember":
            record = make_memory_record(
                scope=self._get_scope(),
                content=args.get("content", ""),
                source="d1_remember",
                session_id=self._session_id,
                user_id=self._user_id or self._user_id_alt,
                agent_id=self._agent_identity,
                topic=args.get("topic", ""),
                kind=args.get("kind"),
                importance=args.get("importance", 3),
                metadata={"source_tool": "d1_remember"},
            )
            job_id = self._enqueue_record(record)
            return json.dumps({"status": "queued" if job_id else "skipped", "job_id": job_id, "id": record["id"], "fingerprint": record["fingerprint"]}, ensure_ascii=False)

        if tool_name == "d1_search":
            if not self.client:
                return json.dumps({"results": [], "error": "D1 client unavailable"}, ensure_ascii=False)
            limit = max(1, min(int(args.get("limit", 5) or 5), 10))
            rows = self.client.search_memories(
                query=args.get("query", ""),
                scope_prefix=self._tenant_scope(),
                limit=limit,
                allowed_kinds=DEFAULT_ALLOWED_KINDS,
            )
            ranked = rank_results(rows)
            out = []
            for r in ranked[:limit]:
                out.append({
                    "id": r.get("id"),
                    "content": r.get("content"),
                    "kind": r.get("kind"),
                    "topic": r.get("topic") or "",
                    "importance": r.get("importance"),
                    "source": r.get("source"),
                })
            return json.dumps({"results": out}, ensure_ascii=False)

        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._writes_enabled() or action == "remove" or not content:
            return
        md = dict(metadata or {})
        record = make_memory_record(
            scope=self._get_scope(),
            content=content,
            source="hermes_builtin",
            session_id=self._session_id,
            user_id=self._user_id or self._user_id_alt,
            agent_id=self._agent_identity,
            target=target,
            topic=md.get("topic", ""),
            kind=md.get("kind"),
            entity_id=md.get("entity_id", ""),
            metadata={"source": "hermes_builtin", "target": target, "action": action, **md},
            importance=md.get("importance", 4 if target == "user" else 3),
        )
        self._enqueue_record(record)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, **kwargs) -> None:
        self._session_id = new_session_id or self._session_id
        if reset:
            self._prefetch_results = ""

    def shutdown(self) -> None:
        if self.flusher:
            self.flusher.shutdown(timeout=10)
        if self.queue_store:
            self.queue_store.release_sending()

    def run_doctor(self) -> Dict:
        queue_stats = self.queue_store.stats() if self.queue_store else {"pending": 0, "failed": 0, "sending": 0, "dead": 0}
        return {
            "status": "available" if self.is_available() else "unavailable",
            "provider": self.name,
            "account_id": bool(self.account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")),
            "api_token": bool(self.api_token or os.environ.get("CLOUDFLARE_API_TOKEN")),
            "database_id": bool(self.database_id or os.environ.get("CLOUDFLARE_D1_DATABASE_ID")),
            "queue": queue_stats,
            "raw_sync_turn_enabled": self._raw_sync_turn,
            "batch_size": self._batch_size,
            "flush_interval_seconds": self._flush_interval,
            "max_attempts": self._max_attempts,
        }
