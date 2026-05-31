# Hermes D1 Memory Provider

[**English**](./README.md) | [**中文**](./README.zh-CN.md)

A Cloudflare D1-backed external memory provider for Hermes Agent. It adds semantic-like (Full Text Search - FTS5) long-term memory using Cloudflare's serverless SQLite database, providing zero-maintenance, edge-deployed memory persistence.

This plugin uses pure HTTP REST calls to Cloudflare's API, meaning zero complex SDK dependencies. It is extremely lightweight and fast.

## Features

- **D1 Backed**: Uses Cloudflare D1 (SQLite) with FTS5 virtual tables for keyword search.
- **Zero Config SDK**: Uses `requests` to call the Cloudflare API directly. No huge cloud SDKs.
- **Durable Local Queue**: Memory writes are first persisted to a local SQLite queue (`~/.hermes/memory/d1_queue.db`), then flushed to D1 in the background. **Your agent never hangs waiting for Cloudflare's API.**
- **Thread-Safe Prefetch**: Context is fetched asynchronously in a background thread. Zero latency impact on agent startup.
- **Retry + Dead-Letter**: Failed writes are retried with backoff and tracked instead of being silently lost.
- **Structured Schema v2**: Durable memory now lands in `hermes_memories_v2` with kind/source/fingerprint/importance fields.
- **Context Prefetch**: Automatically retrieves relevant durable memories before the agent replies.
- **Explicit Memory Tools**: Provides `d1_remember` and `d1_search` tools to the agent.
- **Built-in Fallback**: Hermes `MEMORY.md` and `USER.md` remain active. This provider extends them with remote durable memory.
- **Scope Isolation**: Memories are isolated by user and agent but can be retrieved cross-agent for the same user.
- **Raw Turn Sync Disabled by Default**: `sync_turn` no longer mirrors whole conversations to D1 unless explicitly re-enabled via env.

## Requirements

- Hermes Agent installed
- A Cloudflare Account
- A Cloudflare API Token (with D1 `Edit` permissions)
- A created D1 Database ID

## Setup your Cloudflare D1

1. Create a D1 Database:
   ```bash
   npx wrangler d1 create hermes-memories
   ```
   *(Or just create one via the Cloudflare Dashboard -> D1 SQL)*
2. Get your `Account ID`, `Database ID`, and create an `API Token` (with `Account` -> `D1` -> `Edit` permissions).

## Installation

```bash
hermes plugins install https://github.com/benben17/hermes-d1-memory.git
```


## Configuration

Add the following to your `~/.hermes/.env` file:

```bash
CLOUDFLARE_ACCOUNT_ID="your_account_id"
CLOUDFLARE_API_TOKEN="your_api_token"
CLOUDFLARE_D1_DATABASE_ID="your_database_id"
```

## Advanced Configuration (Optional)

Add these to your `~/.hermes/.env` to fine-tune performance:

| Key | Default | Description |
|-----|---------|-------------|
| `D1_MEM_ENABLE_RAW_SYNC_TURN` | `false` | Enable to mirror every long (>20 chars) turn to D1. |
| `D1_MEM_BATCH_SIZE` | `25` | Number of records to sync per HTTP call. |
| `D1_MEM_PREFETCH_LIMIT` | `4` | Max memories to inject into the system prompt. |
| `D1_MEM_FLUSH_INTERVAL` | `3` | Seconds between background sync attempts. |

## Activate

```bash
hermes config set memory.provider d1-mem
```

Restart your Hermes agent session. You can check the health and queue status via `hermes doctor`.

## License
MIT
