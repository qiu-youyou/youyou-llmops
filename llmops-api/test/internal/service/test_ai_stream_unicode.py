import json
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from internal.service.ai_service import AIService


def test_optimize_prompt_preserves_unicode_and_sse_boundaries():
    content = '中文🙂\n下一行，"引号"与字面量 \\u5546'
    service = SimpleNamespace(language_model_manager=SimpleNamespace(
        create_default_language_model=lambda _: RunnableLambda(lambda _: content),
    ))

    frames = list(AIService.optimize_prompt(service, "优化中文提示词"))

    assert len(frames) == 1
    assert "中文🙂" in frames[0]
    assert frames[0].startswith("event: optimize_prompt\ndata: ")
    assert frames[0].endswith("\n\n")
    assert len(frames[0].splitlines()) == 3
    assert json.loads(frames[0].split("data: ", 1)[1])["optimize_prompt"] == content
