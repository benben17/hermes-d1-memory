import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from .schema import d1_schema_statements

logger = logging.getLogger("hermes.plugins.d1_mem")


def _normalize_match_query(query: str) -> str:
    tokens = re.findall(r"[\w\-\u4e00-\u9fff]+", query or "", flags=re.UNICODE)
    if not tokens:
        return '"memory"'
    return " OR ".join(f'"{token}"' for token in tokens[:8])


class D1Client:
    """Small Cloudflare D1 REST client with schema bootstrap + UPSERT helpers."""

    def __init__(self, account_id: str, api_token: str, database_id: str, timeout: int = 15):
        self.account_id = account_id
        self.api_token = api_token
        self.database_id = database_id
        self.timeout = timeout
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
        )
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    def _execute_raw(self, sql: str, params: Optional[list] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(self.base_url, headers=self.headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"D1 error: {data.get('errors')}")
        return data

    def execute(self, sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
        data = self._execute_raw(sql, params=params)
        return data.get("result", [{}])[0].get("results", [])

    def ensure_schema(self) -> None:
        for stmt in d1_schema_statements():
            self.execute(stmt)

    def upsert_memory(self, record: Dict[str, Any]) -> None:
        existing = self.execute(
            "SELECT id, created_at, importance FROM hermes_memories_v2 WHERE scope = ? AND fingerprint = ? LIMIT 1",
            [record["scope"], record["fingerprint"]],
        )
        if existing:
            existing_row = existing[0]
            self.execute(
                """
                UPDATE hermes_memories_v2
                SET content = ?,
                    kind = ?,
                    source = ?,
                    target = ?,
                    topic = ?,
                    importance = ?,
                    session_id = ?,
                    user_id = ?,
                    agent_id = ?,
                    entity_id = ?,
                    status = ?,
                    metadata = ?,
                    updated_at = ?,
                    last_seen_at = ?
                WHERE id = ?
                """,
                [
                    record["content"],
                    record["kind"],
                    record["source"],
                    record.get("target"),
                    record.get("topic"),
                    record["importance"],
                    record.get("session_id"),
                    record.get("user_id"),
                    record.get("agent_id"),
                    record.get("entity_id"),
                    record["status"],
                    json.dumps(record.get("metadata") or {}, ensure_ascii=False),
                    record["updated_at"],
                    record["last_seen_at"],
                    existing_row["id"],
                ],
            )
            return

        self.execute(
            """
            INSERT INTO hermes_memories_v2 (
                id, scope, content, kind, source, target, topic, fingerprint,
                importance, session_id, user_id, agent_id, entity_id, status,
                metadata, created_at, updated_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record["id"],
                record["scope"],
                record["content"],
                record["kind"],
                record["source"],
                record.get("target"),
                record.get("topic"),
                record["fingerprint"],
                record["importance"],
                record.get("session_id"),
                record.get("user_id"),
                record.get("agent_id"),
                record.get("entity_id"),
                record["status"],
                json.dumps(record.get("metadata") or {}, ensure_ascii=False),
                record["created_at"],
                record["updated_at"],
                record["last_seen_at"],
            ],
        )

    def search_memories(
        self,
        *,
        query: str,
        scope_prefix: str,
        limit: int = 5,
        allowed_kinds: Optional[list[str]] = None,
    ) -> List[Dict[str, Any]]:
        safe_query = _normalize_match_query(query)
        sql = """
            SELECT
                m.id,
                m.content,
                m.kind,
                m.source,
                m.target,
                m.topic,
                m.importance,
                m.scope,
                m.session_id,
                m.user_id,
                m.agent_id,
                m.entity_id,
                m.status,
                m.metadata,
                m.created_at,
                m.updated_at,
                m.last_seen_at,
                bm25(hermes_memories_v2_fts) AS score
            FROM hermes_memories_v2_fts f
            JOIN hermes_memories_v2 m ON m.id = f.id
            WHERE f.content MATCH ?
              AND m.scope LIKE ?
              AND m.status = 'active'
        """
        params: list[Any] = [safe_query, scope_prefix]
        if allowed_kinds:
            placeholders = ", ".join(["?"] * len(allowed_kinds))
            sql += f" AND m.kind IN ({placeholders})"
            params.extend(allowed_kinds)
        sql += " ORDER BY score ASC, m.importance DESC, m.updated_at DESC LIMIT ?"
        params.append(limit)
        return self.execute(sql, params)
