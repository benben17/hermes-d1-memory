from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from .client import D1Client
from .flusher import QueueFlusher
from .normalize import make_memory_record
from .queue_store import QueueStore
from .recall import DEFAULT_ALLOWED_KINDS, build_prompt_block, rank_results

logger = logging.getLogger("hermes.plugins.d1_mem")


D1_REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "d1_remember",
        "description": "Store a durable fact into Cloudflare D1 long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The durable fact to remember"},
                "topic": {"type": "string", "description": "Optional topic/category"},
                "kind": {
                    "type": "string",
                    "description": "Optional kind: user_preference, environment_fact, project_convention, correction, stable_fact, manual_note",
                },
                "importance": {
                    "type": "integer",
                    "description": "Importance from 1-5 (default 3)",
                },
                "entity_id": {"type": "string", "description": "Optional entity identifier for grouping related facts"},
            },
            "required": ["content"],
        },
    },
}

D1_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "d1_search",
        "description": "Search Cloudflare D1 durable memory for relevant long-term facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords or short phrase"},
                "limit": {"type": "integer", "description": "Maximum results to return (default 5)"},
            },
            "required": ["query"],
        },
    },
}


class D1MemoryProvider(MemoryProvider):
    """Cloudflare D1 backed external memory provider with local durable queue."""

    def __init__(self):
        self._session_id = ""
        self._user_id = ""
        self._agent_identity = "hermes"
        self._agent_context = "primary"
        self._hermes_home = os.path.expanduser("~/.hermes")

        self._account_id = ""
        self._api_token = ""
        self._database_id = ""

        self._client: Optional[D1Client] = None
        self._queue_store: Optional[QueueStore] = None
        self._flusher: Optional[QueueFlusher] = None

        self._prefetch_results = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None

        self._initialized = False
        self._schema_ready = False
        self._schema_lock = threading.Lock()

        self._queue_db_path = ""
        self._batch_size = 25
        self._flush_interval_seconds = 3
        self._max_attempts = 6
        self._prefetch_limit = 4
        self._prefetch_max_chars = 800
        self._allowed_kinds = list(DEFAULT_ALLOWED_KINDS)
        self._raw_sync_turn = False

    @property
    def name(self) -> str:
        return "d1-mem"

    def is_available(self) -> bool:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID")
        return bool(account_id and api_token and database_id)

    def initialize(self, session_id: str = "", **kwargs) -> None:
        self._session_id = session_id or ""
        self._user_id = kwargs.get("user_id", "") or kwargs.get("user_id_alt", "") or ""
        self._agent_identity = kwargs.get("agent_identity", "hermes") or "hermes"
        self._agent_context = kwargs.get("agent_context", "primary") or "primary"
        self._hermes_home = kwargs.get("hermes_home") or os.path.expanduser("~/.hermes")

        self._account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        self._api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        self._database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID", "")

        self._queue_db_path = os.environ.get(
            "D1_MEM_QUEUE_DB_PATH",
            os.path.join(self._hermes_home, "memory", "d1_queue.db"),
        )
        self._batch_size = self._safe_int(os.environ.get("D1_MEM_BATCH_SIZE"), default=25, minimum=1)
        self._flush_interval_seconds = self._safe_int(os.environ.get("D1_MEM_FLUSH_INTERVAL"), default=3, minimum=1)
        self._max_attempts = self._safe_int(os.environ.get("D1_MEM_MAX_ATTEMPTS"), default=6, minimum=1)
        self._prefetch_limit = self._safe_int(os.environ.get("D1_MEM_PREFETCH_LIMIT"), default=4, minimum=1)
        self._prefetch_max_chars = self._safe_int(os.environ.get("D1_MEM_PREFETCH_MAX_CHARS"), default=800, minimum=200)
        self._raw_sync_turn = self._env_bool("D1_MEM_ENABLE_RAW_SYNC_TURN", default=False)

        if not self.is_available():
            logger.warning("d1-mem credentials missing in env")
            return

        self._client = D1Client(self._account_id, self._api_token, self._database_id)
        self._queue_store = QueueStore(self._queue_db_path)
        self._flusher = QueueFlusher(
            self._queue_store,
            self._client,
            batch_size=self._batch_size,
            interval_seconds=self._flush_interval_seconds,
            max_attempts=self._max_attempts,
        )
        self._queue_store.release_sending()
        self._ensure_schema_async()
        self._flusher.start()
        self._initialized = True

    def _ensure_schema_async(self) -> None:
        client = self._client
        if not client:
            return

        def _ensure() -> None:
            with self._schema_lock:
                if self._schema_ready:
                    return
                try:
                    client.ensure_schema()
                    self._schema_ready = True
                    logger.info("d1-mem schema initialized")
                except Exception as e:
                    logger.error("Failed to initialize D1 schema: %s", e)

        threading.Thread(target=_ensure, name="d1-mem-schema", daemon=True).start()

    def _writes_enabled(self) -> bool:
        return self._initialized and self._agent_context == "primary"

    def _get_scope(self) -> str:
        tenant = self._user_id or "__default__"
        agent = self._agent_identity or "hermes"
        return f"hermes/{tenant}/{agent}"

    def _tenant_scope(self) -> str:
        parts = self._get_scope().split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}/%"
        return self._get_scope() + "%"

    def system_prompt_block(self) -> str:
        with self._prefetch_lock:
            return self._prefetch_results or ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._prefetch_lock:
            return self._prefetch_results or ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._client or not self._initialized:
            return

        query = (query or "").strip()
        if not query:
            return

        def _fetch() -> None:
            try:
                rows = self._client.search_memories(
                    query=query,
                    scope_prefix=self._tenant_scope(),
                    limit=self._prefetch_limit,
                    allowed_kinds=self._allowed_kinds,
                )
                ranked = rank_results(rows)
                block = build_prompt_block(
                    ranked,
                    max_items=self._prefetch_limit,
                    max_chars=self._prefetch_max_chars,
                )
                with self._prefetch_lock:
                    self._prefetch_results = block
            except Exception as e:
                logger.error("D1 prefetch failed: %s", e)
                with self._prefetch_lock:
                    self._prefetch_results = ""

        self._prefetch_thread = threading.Thread(target=_fetch, name="d1-mem-prefetch", daemon=True)
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._writes_enabled():
            return
        if not self._raw_sync_turn:
            return
        if len((user_content or "").strip()) < 20:
            return

        content = f"User said: {user_content}\nAssistant replied: {assistant_content}"
        metadata = {
            "source": "sync_turn",
            "raw_turn": True,
            "session_id": self._session_id or session_id,
        }
        self._enqueue_record(
            content=content,
            source="selected_turn_fact",
            kind="stable_fact",
            topic="conversation_turn",
            importance=1,
            metadata=metadata,
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        return [D1_REMEMBER_SCHEMA, D1_SEARCH_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "d1_remember":
            content = (args.get("content") or "").strip()
            if not content:
                return json.dumps({"status": "error", "message": "content is required"}, ensure_ascii=False)
            record = self._enqueue_record(
                content=content,
                source="d1_remember",
                topic=args.get("topic", ""),
                kind=args.get("kind"),
                importance=args.get("importance", 3),
                entity_id=args.get("entity_id", ""),
                metadata={"source": "d1_remember"},
            )
            return json.dumps(
                {
                    "status": "success",
                    "id": record.get("id"),
                    "fingerprint": record.get("fingerprint"),
                    "message": "queued for D1 durable memory",
                },
                ensure_ascii=False,
            )

        if tool_name == "d1_search":
            client = self._client
            if not client:
                return json.dumps({"results": [], "message": "provider unavailable"}, ensure_ascii=False)
            query = (args.get("query") or "").strip()
            limit = self._safe_int(args.get("limit"), default=5, minimum=1)
            rows = client.search_memories(
                query=query,
                scope_prefix=self._tenant_scope(),
                limit=limit,
                allowed_kinds=self._allowed_kinds,
            )
            ranked = rank_results(rows)
            out = []
            for row in ranked[:limit]:
                out.append(
                    {
                        "id": row.get("id"),
                        "content": row.get("content"),
                        "kind": row.get("kind"),
                        "topic": row.get("topic") or "",
                        "importance": row.get("importance"),
                        "source": row.get("source"),
                    }
                )
            return json.dumps({"results": out}, ensure_ascii=False)

        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if action == "remove":
            return
        if not self._writes_enabled():
            return
        self._enqueue_record(
            content=content,
            source="hermes_builtin",
            target=target,
            kind=(metadata or {}).get("kind"),
            topic=(metadata or {}).get("topic", ""),
            importance=(metadata or {}).get("importance", 4 if target == "user" else 3),
            entity_id=(metadata or {}).get("entity_id", ""),
            metadata={
                "source": "hermes_builtin",
                "target": target,
                "action": action,
                **(metadata or {}),
            },
        )

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = new_session_id or ""
        with self._prefetch_lock:
            self._prefetch_results = ""

    def shutdown(self) -> None:
        try:
            if self._flusher:
                self._flusher.shutdown(timeout=10)
        finally:
            with self._prefetch_lock:
                self._prefetch_results = ""

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "account_id",
                "description": "Cloudflare Account ID",
                "secret": True,
                "required": True,
                "env_var": "CLOUDFLARE_ACCOUNT_ID",
            },
            {
                "key": "api_token",
                "description": "Cloudflare API Token",
                "secret": True,
                "required": True,
                "env_var": "CLOUDFLARE_API_TOKEN",
            },
            {
                "key": "database_id",
                "description": "Cloudflare D1 Database ID",
                "secret": True,
                "required": True,
                "env_var": "CLOUDFLARE_D1_DATABASE_ID",
            },
        ]

    def run_doctor(self) -> Dict[str, Any]:
        stats = {"pending": 0, "failed": 0, "sending": 0, "dead": 0}
        if self._queue_store:
            try:
                stats = self._queue_store.stats()
            except Exception as e:
                stats = {"queue_error": str(e)}
        return {
            "status": "available" if self.is_available() else "unavailable",
            "provider": self.name,
            "account_id": bool(self._account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")),
            "api_token": bool(self._api_token or os.environ.get("CLOUDFLARE_API_TOKEN")),
            "database_id": bool(self._database_id or os.environ.get("CLOUDFLARE_D1_DATABASE_ID")),
            "queue_db_path": self._queue_db_path,
            "queue": stats,
            "raw_sync_turn": self._raw_sync_turn,
            "agent_context": self._agent_context,
            "allowed_kinds": list(self._allowed_kinds),
        }

    def _enqueue_record(
        self,
        *,
        content: str,
        source: str,
        target: str = "",
        topic: str = "",
        kind: Optional[str] = None,
        importance: int = 3,
        entity_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._queue_store:
            raise RuntimeError("d1-mem queue is not initialized")
        record = make_memory_record(
            scope=self._get_scope(),
            content=content,
            source=source,
            session_id=self._session_id,
            user_id=self._user_id,
            agent_id=self._agent_identity,
            target=target,
            topic=topic,
            kind=kind,
            entity_id=entity_id,
            metadata=metadata or {},
            importance=importance,
        )
        self._queue_store.enqueue(record, record["fingerprint"])
        return record

    @staticmethod
    def _env_bool(name: str, default: bool = False) -> bool:
        value = os.environ.get(name)
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _safe_int(value: Any, *, default: int, minimum: int = 1) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return max(minimum, parsed)
