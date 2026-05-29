import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Dict, List, Optional

from .schema import queue_schema_statements


class QueueStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            for stmt in queue_schema_statements():
                conn.execute(stmt)
            conn.commit()

    def enqueue(self, payload: Dict, fingerprint: str) -> str:
        now = int(time.time())
        job_id = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_write_queue
                    (job_id, payload_json, fingerprint, state, attempt_count, next_attempt_at, last_error, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', 0, ?, NULL, ?, ?)
                """,
                (job_id, json.dumps(payload, ensure_ascii=False), fingerprint, now, now, now),
            )
            conn.commit()
        return job_id

    def claim_batch(self, limit: int = 25) -> List[Dict]:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, payload_json, fingerprint, attempt_count, created_at
                FROM memory_write_queue
                WHERE state IN ('pending', 'failed') AND next_attempt_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                conn.execute(
                    "UPDATE memory_write_queue SET state='sending', updated_at=? WHERE job_id=?",
                    (now, row["job_id"]),
                )
                claimed.append({
                    "job_id": row["job_id"],
                    "payload": json.loads(row["payload_json"]),
                    "fingerprint": row["fingerprint"],
                    "attempt_count": row["attempt_count"],
                    "created_at": row["created_at"],
                })
            conn.commit()
        return claimed

    def ack(self, job_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memory_write_queue WHERE job_id=?", (job_id,))
            conn.commit()

    def fail(self, job: Dict, error: str, max_attempts: int) -> str:
        attempts = int(job.get("attempt_count", 0)) + 1
        now = int(time.time())
        if attempts >= max_attempts:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_write_deadletter
                        (job_id, payload_json, last_error, attempt_count, created_at, dead_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job["job_id"],
                        json.dumps(job["payload"], ensure_ascii=False),
                        error[:2000],
                        attempts,
                        job.get("created_at", now),
                        now,
                    ),
                )
                conn.execute("DELETE FROM memory_write_queue WHERE job_id=?", (job["job_id"],))
                conn.commit()
            return "dead"

        backoff = self._backoff_seconds(attempts)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_write_queue
                SET state='failed',
                    attempt_count=?,
                    next_attempt_at=?,
                    last_error=?,
                    updated_at=?
                WHERE job_id=?
                """,
                (attempts, now + backoff, error[:2000], now, job["job_id"]),
            )
            conn.commit()
        return "retry"

    def release_sending(self) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE memory_write_queue SET state='pending', updated_at=? WHERE state='sending'",
                (now,),
            )
            conn.commit()

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            queue_counts = dict(
                conn.execute(
                    "SELECT state, COUNT(*) AS count FROM memory_write_queue GROUP BY state"
                ).fetchall()
            )
            dead_count = conn.execute(
                "SELECT COUNT(*) FROM memory_write_deadletter"
            ).fetchone()[0]
        return {
            "pending": int(queue_counts.get("pending", 0)),
            "failed": int(queue_counts.get("failed", 0)),
            "sending": int(queue_counts.get("sending", 0)),
            "dead": int(dead_count),
        }

    @staticmethod
    def _backoff_seconds(attempt: int) -> int:
        schedule = {1: 60, 2: 300, 3: 900, 4: 3600, 5: 14400}
        return schedule.get(attempt, 21600)
