# 聊天图片输入

应用调试、WebApp、辅助 Agent、开放 API 共用 `query` 与 `image_urls`。`image_urls` 省略时为 `[]`；文字与图片至少存在一项，允许纯图片。最多5张图片，仅接受 HTTPS URL，每条最长4096字符；拒绝账号密码、localhost、字面量非公网 IP、Base64 和非法数组元素。

```json
{
  "query": "对比这两张图片",
  "image_urls": ["https://example.com/a.png", "https://example.com/b.jpg"]
}
```

纯图片请求使用 `{"query":"","image_urls":["https://example.com/a.png"]}`。开放 API 还需原有 `app_id`、鉴权及会话参数，`stream: false` 同样支持图片。非流式响应及各历史消息接口增加 `image_urls`；旧消息为空数组。SSE 事件与终止语义不变，前端使用发送时的图片列表展示当前消息。

## 模型与历史

Provider 目录的 `image_input` 决定能力。新图片遇到不支持的模型，在创建消息/启动 SSE 前拒绝。图片通过 `HumanMessage` 的 `image_url` 块传递，不是模型初始化参数；纯文本保持原字符串格式。

历史图片恢复为相同内容块；切换到文本模型时使用 `[历史图片未提供：N张]`，保留数据库原 URL。回到视觉模型后可再次恢复。历史按完整轮次裁剪，文字约4字符/token、图片每张1024 token，仅是预算估算，费用继续使用 Provider 返回的 usage。视觉模型的历史预算为8192，文本模型为2000；视觉预算允许一轮最多5张图片及常规回答进入后续上下文，超过预算的整轮仍会被裁掉。

输入关键词审核只检查文字，不审核图片内容。会话摘要、建议问题使用图片数量占位与回答，不追加 URL；纯图片首轮标题为“图片对话”。

## 前端与能力接口

共享聊天面板支持 JPEG/PNG/WebP，本地每张最多15 MB，复用登录后的 `/upload-files/image`，返回 `data.image_url`。图片上传完成才可发送；上传失败可以重试或移除，发送失败恢复草稿。历史图片可放大预览，加载失败显示占位。

调试页面使用当前模型 `features`，WebApp 基础信息的 `app_config.features` 对应已发布模型的能力列表；`GET /assistant-agent/capabilities` 要求登录，返回 `data.features`。能力加载前禁用上传，切换模型保留待发图片并阻止不支持的发送。

## 地址生命周期与部署

后端不下载、探测或转存外链，不按 URL 后缀判断图片类型。模型服务负责访问图片。COS 返回地址须使用 HTTPS，并且对模型服务可读；本次不提供私有桶签名、自动续期或外链归档。URL 失效会导致展示或调用失败，不自动删除图片重试。

新增迁移 `c84a7f2e901d`（父版本 `b1263027e476`）为 `message.image_urls` 添加非空 JSONB 列，默认 `[]`，自动兼容旧记录。部署顺序：在确认目标数据库并备份后，使用现有部署流程执行 `flask db upgrade`，然后部署后端与前端。回退应用代码可以保留新增列；不要为回退应用直接 downgrade 删除图片历史。

离线测试不调用模型或 COS，也不执行数据库迁移。真实环境验收需分别验证迁移、COS 图片可达、视觉模型识图、第二轮不重新上传的追问，以及刷新后的历史展示。

## 维护约定

聊天请求共用 `ChatReq` 的字段与 `validate_image_urls` 校验，复用已有 `ListField`。Service 接收各自的请求 Schema（如 `DebugChatReq`），通过 `req.query.data` 和 `req.image_urls.data` 读取字段；未来新增输入字段不扩展 Handler/Service 的位置参数。模型能力只以 `ModelFeature.IMAGE_INPUT` / `features: [image_input]` 为准；能力接口返回原始 features 列表。Core 消息构造不依赖 Flask 请求或 Schema。
