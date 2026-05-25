# Hermes D1 Memory Provider

A Cloudflare D1-backed external memory provider for Hermes Agent. It adds semantic-like (Full Text Search - FTS5) long-term memory using Cloudflare's serverless SQLite database, providing zero-maintenance, edge-deployed memory persistence.

This plugin uses pure HTTP REST calls to Cloudflare's API, meaning zero complex SDK dependencies.

## Features

- **D1 Backed**: Uses Cloudflare D1 (SQLite) with FTS5 virtual tables for lightning-fast keyword search.
- **Zero Config SDK**: Uses `requests` to call Cloudflare API directly. No huge cloud SDKs.
- **Auto Turn Sync**: Asynchronously captures conversation turns to D1 (`sync_turn`).
- **Context Prefetch**: Automatically retrieves relevant past memories based on the user's latest message before you reply.
- **Explicit Memory Tools**: Provides `d1_remember` and `d1_search` tools to the agent.
- **Built-in Fallback**: Hermes `MEMORY.md` and `USER.md` remain active. This is added alongside them.

## Requirements

- Hermes Agent installed
- A Cloudflare Account
- A Cloudflare API Token (with D1 Edit permissions)
- A created D1 Database ID

## Setup your Cloudflare D1

1. Create a D1 Database:
   ```bash
   npx wrangler d1 create hermes-memories
   ```
2. Get your `Account ID`, `Database ID`, and create an `API Token` (Edit D1 permissions).

## Installation

```bash
hermes plugins install https://github.com/your-username/hermes-d1-memory.git
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

Restart your Hermes agent session, and the memory provider will auto-initialize the required tables in your D1 database.
