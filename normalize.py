import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, Optional


ALLOWED_KINDS = {
    "user_preference",
    "environment_fact",
    "project_convention",
    "correction",
    "stable_fact",
    "manual_note",
}


def _clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_content(text: str) -> str:
    return _clean_ws(text)


def normalize_topic(topic: str) -> str:
    return _clean_ws(topic)[:120]


def normalize_kind(kind: Optional[str], source: str, target: str = "") -> str:
    value = (kind or "").strip().lower()
    if value in ALLOWED_KINDS:
        return value
    if source == "hermes_builtin":
        if target == "user":
            return "user_preference"
        return "stable_fact"
    if source == "selected_turn_fact":
        return "stable_fact"
    if source == "d1_remember":
        return "manual_note"
    return "stable_fact"


def normalize_importance(value: Any, default: int = 3) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(5, parsed))


def build_fingerprint(scope: str, kind: str, content: str, topic: str = "", entity_id: str = "") -> str:
    seed = "|".join([
        scope.strip(),
        kind.strip(),
        normalize_content(content).lower(),
        normalize_topic(topic).lower(),
        (entity_id or "").strip().lower(),
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def make_memory_record(
    *,
    scope: str,
    content: str,
    source: str,
    session_id: str = "",
    user_id: str = "",
    agent_id: str = "",
    target: str = "",
    topic: str = "",
    kind: Optional[str] = None,
    entity_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    importance: int = 3,
) -> Dict[str, Any]:
    now = int(time.time())
    clean_content = normalize_content(content)
    clean_topic = normalize_topic(topic)
    clean_kind = normalize_kind(kind, source, target)
    fingerprint = build_fingerprint(scope, clean_kind, clean_content, clean_topic, entity_id)
    record = {
        "id": str(uuid.uuid4()),
        "scope": scope,
        "content": clean_content,
        "kind": clean_kind,
        "source": source,
        "target": target or None,
        "topic": clean_topic or None,
        "fingerprint": fingerprint,
        "importance": normalize_importance(importance),
        "session_id": session_id or None,
        "user_id": user_id or None,
        "agent_id": agent_id or None,
        "entity_id": entity_id or None,
        "status": "active",
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now,
        "last_seen_at": now,
    }
    return record


def record_to_json(record: Dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)
