# Hermes D1 记忆存储插件 (Hermes D1 Memory Provider)

[**English**](./README.md) | [**中文**](./README.zh-CN.md)

基于 Cloudflare D1 构建的 Hermes Agent 外部记忆插件。它利用 Cloudflare 边缘部署的无服务器 SQLite 数据库，为你的 AI 代理提供零维护、超快速的长期记忆持久化能力，并内置了 FTS5 全文检索支持。

本插件仅依赖最基础的 HTTP REST 调用（基于 `requests` 库），**完全摒弃了臃肿的官方云 SDK**，极度轻量。

## 核心特性

- **基于 D1 驱动**: 利用 Cloudflare D1 (SQLite) 与 FTS5 虚拟表，实现毫秒级的全文关键词检索与 BM25 相关度打分。
- **零 SDK 依赖**: 纯粹使用 `requests` 库直连 Cloudflare API，安装无负担，不污染环境。
- **对话全自动同步**: 对话结束后，在后台异步线程中自动将有价值的对话片段 (`sync_turn`) 存入云端数据库。
- **上下文未卜先知 (Prefetch)**: 在下一轮对话开始前，AI 会根据你的提问，瞬间从 D1 库中预取相关历史记忆并塞入上下文。
- **主动记忆工具**: 为 AI 赋予了显式的 `d1_remember` (记笔记) 和 `d1_search` (翻找记录) 的能力。
- **与本地记忆共存**: 本插件不会覆盖 Hermes 原生的 `MEMORY.md` 和 `USER.md`，而是作为一个庞大的“云端历史日记本”与它们完美配合。
- **作用域隔离**: 记忆按照用户 (User) 和机器人 (Agent) 严格隔离写入，但在检索时支持同一用户跨机器人打通读取。

## 前置要求

- 已安装 Hermes Agent
- 一个 Cloudflare 账号
- 一个具有 D1 `Edit` 权限的 Cloudflare API Token
- 已创建好的 D1 数据库 ID

## 配置 Cloudflare D1

1. 创建一个 D1 数据库：
   ```bash
   npx wrangler d1 create hermes-memories
   ```
   *(或者直接登录 Cloudflare Dashboard -> D1 SQL -> 创建数据库)*
2. 拿到你的 `Account ID`（账户 ID）、`Database ID`（数据库 ID），并生成一个 `API Token`（权限必须包含：`Account` -> `D1` -> `Edit`）。

## 安装

```bash
hermes plugins install https://github.com/benben17/hermes-d1-memory.git
```

## 配置环境变量

将以下凭证写入你的 `~/.hermes/.env` 文件中：

```bash
CLOUDFLARE_ACCOUNT_ID="填入你的_account_id"
CLOUDFLARE_API_TOKEN="填入你的_api_token"
CLOUDFLARE_D1_DATABASE_ID="填入你的_database_id"
```

## 激活使用

在终端执行：
```bash
hermes config set memory.provider d1-mem
```

重启你的 Hermes Agent 会话即可。在首次运行时，插件会自动连接 Cloudflare 并在你的 D1 数据库中初始化所需的 FTS5 全文检索表和触发器（Triggers）。

## 开源协议
MIT
