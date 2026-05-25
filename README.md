# Hermes D1 Memory Provider

[**English**](./README.md) | [**中文**](./README.zh-CN.md)

A Cloudflare D1-backed external memory provider for Hermes Agent. It adds semantic-like (Full Text Search - FTS5) long-term memory using Cloudflare's serverless SQLite database, providing zero-maintenance, edge-deployed memory persistence.

This plugin uses pure HTTP REST calls to Cloudflare's API, meaning zero complex SDK dependencies. It is extremely lightweight and fast.

## Features

- **D1 Backed**: Uses Cloudflare D1 (SQLite) with FTS5 virtual tables for lightning-fast keyword search.
- **Zero Config SDK**: Uses `requests` to call the Cloudflare API directly. No huge cloud SDKs.
- **Auto Turn Sync**: Asynchronously captures conversation turns to D1 (`sync_turn`).
- **Context Prefetch**: Automatically retrieves relevant past memories based on the user's latest message before the agent replies.
- **Explicit Memory Tools**: Provides `d1_remember` and `d1_search` tools to the agent.
- **Built-in Fallback**: Hermes `MEMORY.md` and `USER.md` remain active. This provider acts as a massive historical diary alongside them.
- **Scope Isolation**: Memories are isolated by user and agent but can be retrieved cross-agent for the same user.

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

## Activate

```bash
hermes config set memory.provider d1-mem
```

Restart your Hermes agent session. The memory provider will automatically initialize the required FTS5 tables and triggers in your D1 database on the first run.

## License
MIT
