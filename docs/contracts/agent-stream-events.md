# Agent 流式事件现状（契约草案）

> 本文记录当前代码行为和未来前端需要关注的兼容面，不代表已经实现了版本化 `StreamEvent`、`sequence` 或事件重放。

## 传输格式

流式接口返回 UTF-8 编码的 Server-Sent Events，`Content-Type` 为 `text/event-stream`。生产环境浏览器通过入口 Nginx 的 `/api/` 访问流式接口；该代理关闭响应缓冲与压缩，读取空闲超时为 600 秒，后端事件格式与终止语义不受代理路径影响。当前 Agent Stream 的实际格式类似：

```text
event: agent_message
data: {"event":"agent_message","id":"...","conversation_id":"...","message_id":"...","task_id":"...","thought":"...","observation":"","tool":"","tool_input":{},"answer":"Hello","latency":0.12}

```

应用调试、公开 API、WebApp 和辅助智能体通过 `internal/service/chat_runtime.py` 的 `ChatRuntime` 统一编码 SSE，使用 `QueueEvent.value` 作为事件名，并复用事件合并和用量序列化函数。保持既有字段和辅助智能体的 `data:` 空白格式；OpenAPI 独有的 `end_user_id` 继续保留。

## 公共字段

SSE 的 JSON 数据使用 `ensure_ascii=False`，直接输出中文；聊天、工作流、提示词优化及调试事件遵循相同规则。引号、反斜杠和换行仍由 JSON 编码器转义，客户端继续按 JSON 解析一次。此设置只改变传输文本的可读性，不改变事件名、字段或终止行为，也不会解码正文中原本存在的字面量 `\uXXXX`。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | UUID | 稳定的事件/步骤标识；同一回答步骤的 Token Chunk 共用该值。 |
| `event` | string | 事件判别字段，同时也是 SSE 事件名。 |
| `task_id` | UUID | Agent 任务 ID。 |
| `conversation_id` | UUID | 当前会话 ID；调试、公开对话与 WebApp 流中存在。 |
| `message_id` | UUID | 当前消息 ID；调试、公开对话与 WebApp 流中存在。 |
| `end_user_id` | UUID | 公开应用对话中的终端用户 ID。 |

`version`、`sequence`、`created_at` 和 `trace_id` 是候选目标字段，目前代码尚未发送。

## Agent 事件

| 事件 | 用途 | 关键字段 |
| --- | --- | --- |
| `long_term_memory_recall` | 已加载长期记忆。 | `observation` |
| `agent_thought` | 模型产生工具调用思考。 | `thought`, `latency` |
| `agent_message` | 流式回答片段。 | `thought`, `answer`, `latency` |
| `agent_action` | 已调用工具。 | `tool`, `tool_input`, `observation` |
| `dataset_retrieval` | 已完成知识库检索。 | `tool_input`, `observation` |
| `ping` | 保持空闲连接。 | 无事件专属字段 |
| `agent_end` | 正常终止。 | 无 |
| `stop` | 用户主动停止。 | 无 |
| `timeout` | 服务端超时终止。 | 无 |
| `error` | 异常终止。 | `observation` |

一个任务只发送一个终止事件，并且终止后不再发送 Frame。`AgentQueueManager` 对队列创建及终止状态加锁，拒绝重复终止和终止后的事件；`listen()` 发出终止事件后直接返回，不再进行 Redis 停止检查或发送心跳。后台 Agent 统一捕获未处理异常并发布现有 `error` 事件，执行退出时关闭队列，避免响应等待已退出的生产线程。

这一路径由公开 API、应用调试、WebApp 及辅助智能体共享，不改变事件名或字段。模型返回 HTTP 400（例如 `Prompt exceeds max length`）时，已开始的 SSE 响应通过 `error.observation` 表达失败并结束响应体，不会将已发送的 HTTP 200 改为 400。该处理不自动截断提示词，也不保证取消已经发出的模型请求。调试器暂停进程期间，响应仍需等待恢复执行。

离线回归覆盖真实 LangGraph 后台线程中的模拟模型 400、未处理节点异常、正常/错误/停止/超时终止、重复终止、队列并发初始化，以及 Service 的 SSE 序列化和保存参数。公共执行层在终止事件处关闭 iterator 并安排一次保存，不再读取后续事件；异常退出或缺少终止事件时补充既有 `error` 事件，避免部分消息被记为正常完成。模型原有错误事件原样保留，执行层意外异常只返回通用错误信息。测试使用 Fake Redis 和模型，不访问外部服务。

## 其他流式事件

- `optimize_prompt` 携带 `optimize_prompt` 文本片段。
- `workflow` 直接在顶层携带当前 Workflow Node 的结果字段。

## Agent 用量字段（2026-09-18）

应用调试、公开 API、WebApp 和辅助智能体的 SSE 包含 `message_token_count`、`answer_token_count`、`total_token_count`、`total_price` 和 `usage`。原有事件名和文本字段保持不变。金额通过 Decimal 计算，JSON 金额使用字符串，未知费用使用 `null`；旧数值列中的零不能用于判断调用是否免费。

- 普通文本片段的 `usage` 为 `null`，不能根据该片段的零计数判定本轮免费。
- 每次 LLM 调用结束后，工具调用使用 `agent_thought` 携带结算数据；普通回答发送与文本片段同一个 `id` 的空 `agent_message`，携带整次调用的用量。合并时拼接文本，但覆盖统计字段，不累加结算快照。
- 步骤 `usage` 保存 `provider`、`model`、`response_model`、输入/输出/缓存/推理 token、`source`、`complete`、`price_status`、币种和费率快照。缓存、推理是输入/输出的细分，不再加到总量。
- `agent_end`、`error`、`stop`、`timeout` 的 `usage` 是当前 Agent 的汇总，`scope=agent`，包含 `call_count`、已知 token 小计、`complete`、`total_price` 和按币种区分的 `known_costs`。异常终止保守标记为不完整。
- 公开 API 非流式响应和历史消息接口使用相同汇总口径；`Message.usage` 保存汇总，`MessageAgentThought.usage` 保存调用明细。历史记录没有快照时返回 `{}`，不追溯估算历史账单。

目前只统计本次 Agent 主循环中的模型调用；摘要、标题、建议问题及独立 Workflow 的辅助调用不包括在 `scope=agent` 中。底层 SDK 未返回响应的重试、断流后的供应商实际收费无法由本地精确恢复。客户端断开后，调试、公开 API、WebApp 和辅助 Agent 的 Service 会在后台继续排空同一个 Agent iterator，再保存已收到的步骤和终止状态；这不取消已经发出的模型请求，也不提供断线客户端重连或事件重放。

价格配置、迁移和验证见 [模型用量统计](../runbooks/model-usage.md)。未来前端消费金额前须检查 `usage.complete` 和 `usage.total_price`，并标明“按价目表计算”，不将其作为供应商实际扣款。

## 后续兼容原则

- 在前端开始消费前，应把现有事件名和顶层字段视为待确认的兼容面。
- 正式冻结契约前，应先决定是否把 SSE `event:` 统一为 `QueueEvent.value`，并通过测试保证它与 `data.event` 一致。
- 引入正式契约时优先增加字段，不要无过渡地重命名或删除现有字段。
- 修改字段类型或终止语义时，应明确版本和迁移方式，并增加普通回答、工具调用、检索、停止、超时、异常、心跳与断连测试。
- 由于 Agent 事件使用进程内 Queue，目前不支持通过 `Last-Event-ID` 重连，也不支持服务端重放。

## 图片输入

四个聊天入口支持可选 `image_urls` 数组和纯图片消息，详见 [图片输入契约](image-input.md)。图片不增加 SSE 事件；能力校验在启动流前完成。当前图片由客户端提交数据展示，重新加载通过历史消息的 `image_urls` 恢复。
