# 文档中文与 JSON 转义排查

## 正常行为

普通 JSON 响应和 SSE JSON 数据直接输出中文。引号、换行和反斜杠仍必须按 JSON 规则转义；前端只解析一次 JSON，不对正文做额外 Unicode 解码。

TXT 按纯文本读取，优先使用 UTF-8，解码失败时检测编码。TXT 即使内容是 JSON，也应保留原文，避免通用文件加载器识别成 JSON 后重新序列化中文。Word、Markdown、PDF、HTML、CSV、Excel 使用各自的加载器；URL 文件名从 URL 路径提取，不包含查询参数。

如果用户上传的文本本身就是字面量 `\uXXXX`，系统会保留这些字符；代码示例、正则和路径也可能包含合法反斜杠，不能全局替换或使用 `unicode_escape` 解码。

## 已有片段的恢复

1. 检查原始文件是否直接包含中文。若源文件是转义过的完整 JSON，先用 JSON 解析器读取，再以 `ensure_ascii=False` 导出；不要对已经切开的片段强行解析 JSON。
2. 部署修复并重启 Flask 和 Celery，确保后台索引进程使用新加载器。
3. 对确认受影响的文档，保留原文件后，通过知识库页面删除旧文档并重新上传，重新生成分段、关键词和向量。这会替换原有片段及其命中统计。
4. 检查片段正文、中文关键词和检索结果。仅修改数据库 `content` 或前端展示会造成正文与索引不一致。

不自动批量修改历史数据；字面量转义可能是合法原文，必须结合源文件判断。

## 离线验证

在 `llmops-api` 目录运行：

```sh
uv run --no-sync pytest test/internal/core/test_file_extractor.py test/internal/service/test_chat_runtime.py test/internal/service/test_workflow_robustness.py test/internal/service/test_ai_stream_unicode.py -q
```

上述测试使用临时文件和模拟依赖，不访问数据库、对象存储或模型 API。格式路由测试验证加载器选择；不代表所有真实 Office/PDF 文件均完成端到端验证。
