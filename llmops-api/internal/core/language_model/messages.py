"""模型消息构造与文本提取，供当前输入、历史记忆和辅助提示词共用。"""
from langchain_core.messages import HumanMessage


def build_human_message(
    query: str, image_urls: list[str], *, include_images: bool = True
) -> HumanMessage:
    """纯文本保持字符串；图文使用 image_url 块，文本模型历史使用占位。"""
    if not image_urls:
        return HumanMessage(content=query)
    if not include_images:
        return HumanMessage(content=f"{query}\n[历史图片未提供：{len(image_urls)}张]")

    content = []
    if query.strip():
        content.append({"type": "text", "text": query})
    for image_url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    return HumanMessage(content=content)


def extract_message_text(content: str | list[str | dict]) -> str:
    """提取文本用于关键词审核；图片 URL 不参与审核和文本提示词。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def format_query_for_text_model(query: str, image_urls: list[str]) -> str:
    """摘要和建议问题只接收文字与图片数量，不接收图片地址。"""
    if image_urls:
        return f"{query}\n[附带{len(image_urls)}张图片]"
    return query
