import textwrap


D1_MAIN_TABLE_SQL = textwrap.dedent(
    """
    CREATE TABLE IF NOT EXISTS hermes_memories_v2 (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        content TEXT NOT NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        target TEXT,
        topic TEXT,
        fingerprint TEXT NOT NULL,
        importance INTEGER NOT NULL DEFAULT 3,
        session_id TEXT,
        user_id TEXT,
        agent_id TEXT,
        entity_id TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        last_seen_at INTEGER
    )
    """
).strip()


D1_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_hm_v2_scope_created ON hermes_memories_v2(scope, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_hm_v2_scope_kind_created ON hermes_memories_v2(scope, kind, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_hm_v2_scope_source_created ON hermes_memories_v2(scope, source, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_hm_v2_scope_fingerprint ON hermes_memories_v2(scope, fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_hm_v2_scope_status ON hermes_memories_v2(scope, status)",
    "CREATE INDEX IF NOT EXISTS idx_hm_v2_scope_entity ON hermes_memories_v2(scope, entity_id)",
]


D1_FTS_SQL = textwrap.dedent(
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS hermes_memories_v2_fts USING fts5(
        content,
        topic,
        id UNINDEXED,
        content='hermes_memories_v2',
        content_rowid='rowid'
    )
    """
).strip()


D1_TRIGGER_SQL = [
    textwrap.dedent(
        """
        CREATE TRIGGER IF NOT EXISTS hermes_memories_v2_ai AFTER INSERT ON hermes_memories_v2 BEGIN
            INSERT INTO hermes_memories_v2_fts(rowid, id, content, topic)
            VALUES (new.rowid, new.id, new.content, COALESCE(new.topic, ''));
        END;
        """
    ).strip(),
    textwrap.dedent(
        """
        CREATE TRIGGER IF NOT EXISTS hermes_memories_v2_ad AFTER DELETE ON hermes_memories_v2 BEGIN
            INSERT INTO hermes_memories_v2_fts(hermes_memories_v2_fts, rowid, id, content, topic)
            VALUES('delete', old.rowid, old.id, old.content, COALESCE(old.topic, ''));
        END;
        """
    ).strip(),
    textwrap.dedent(
        """
        CREATE TRIGGER IF NOT EXISTS hermes_memories_v2_au AFTER UPDATE ON hermes_memories_v2 BEGIN
            INSERT INTO hermes_memories_v2_fts(hermes_memories_v2_fts, rowid, id, content, topic)
            VALUES('delete', old.rowid, old.id, old.content, COALESCE(old.topic, ''));
            INSERT INTO hermes_memories_v2_fts(rowid, id, content, topic)
            VALUES (new.rowid, new.id, new.content, COALESCE(new.topic, ''));
        END;
        """
    ).strip(),
]


QUEUE_SCHEMA_SQL = [
    textwrap.dedent(
        """
        CREATE TABLE IF NOT EXISTS memory_write_queue (
            job_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL,
            last_error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    ).strip(),
    "CREATE INDEX IF NOT EXISTS idx_mwq_state_next_attempt ON memory_write_queue(state, next_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_mwq_fingerprint ON memory_write_queue(fingerprint)",
    textwrap.dedent(
        """
        CREATE TABLE IF NOT EXISTS memory_write_deadletter (
            job_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            last_error TEXT,
            attempt_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            dead_at INTEGER NOT NULL
        )
        """
    ).strip(),
]


def d1_schema_statements() -> list[str]:
    return [D1_MAIN_TABLE_SQL, D1_FTS_SQL, *D1_INDEX_SQL, *D1_TRIGGER_SQL]


def queue_schema_statements() -> list[str]:
    return list(QUEUE_SCHEMA_SQL)
