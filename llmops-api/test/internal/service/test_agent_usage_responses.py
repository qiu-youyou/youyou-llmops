import importlib
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import Flask

from internal.core.agent.entities.queue_entity import (
    AgentThought,
    AgentResult,
    QueueEvent,
)
from internal.core.agent.usage import (
    merge_agent_thought,
    summarize_usage,
    summary_fields,
)
from internal.core.language_model.usage import TokenUsage
from internal.service import chat_runtime
from internal.entity.app_entity import AppStatus
from internal.entity.conversation_entity import InvokeFrom


@pytest.mark.parametrize(
    "entry,stream",
    [("debug", True), ("web", True), ("public", True), ("public", False)],
)
@pytest.mark.parametrize("terminal", [QueueEvent.AGENT_END, QueueEvent.ERROR])
def test_services_return_and_save_consistent_usage(
    monkeypatch, entry, stream, terminal
):
    module = importlib.import_module(
        "internal.service."
        + {
            "debug": "app_service",
            "web": "web_app_service",
            "public": "openapi_service",
        }[entry]
    )
    usage = TokenUsage(
        source="provider",
        complete=True,
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        currency="CNY",
        total_price=Decimal("0.00036"),
        price_status="calculated",
    )
    step_id, task_id = uuid4(), uuid4()
    events = [
        AgentThought(
            id=step_id, task_id=task_id, event=QueueEvent.AGENT_MESSAGE, answer="答案"
        ),
        AgentThought(
            id=step_id,
            task_id=task_id,
            event=QueueEvent.AGENT_MESSAGE,
            usage=usage,
            **usage.legacy_fields(),
        ),
        AgentThought(
            id=uuid4(),
            task_id=task_id,
            event=terminal,
            observation=(
                "Prompt exceeds max length" if terminal == QueueEvent.ERROR else ""
            ),
        ),
    ]
    merged = {}
    for event in events:
        merge_agent_thought(merged, event)
    result = AgentResult(
        answer="答案",
        agent_thoughts=list(merged.values()),
        **summary_fields(summarize_usage(list(merged.values()))),
    )
    monkeypatch.setattr(
        chat_runtime,
        "FunctionCallAgent",
        lambda **_kwargs: SimpleNamespace(
            stream=lambda _state: iter(events),
            invoke=lambda _state: result,
        ),
    )
    monkeypatch.setattr(
        chat_runtime,
        "TokenBufferMemory",
        lambda **_kwargs: SimpleNamespace(
            get_history_prompt_messages=lambda **_kwargs: [],
        ),
    )
    saves = []

    class DeferredThread:
        def __init__(self, target, kwargs):
            saves.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(chat_runtime, "Thread", DeferredThread)
    account = SimpleNamespace(id=uuid4())
    app_id = uuid4()
    end_user = SimpleNamespace(id=uuid4(), app_id=app_id)
    conversation = SimpleNamespace(
        id=uuid4(),
        app_id=app_id,
        invoke_from=InvokeFrom.WEB_APP if entry == "web" else InvokeFrom.SERVICE_API,
        created_by=account.id if entry == "web" else end_user.id,
        summary="",
        is_deleted=False,
    )
    message = SimpleNamespace(id=uuid4())
    app = SimpleNamespace(
        id=app_id,
        account_id=account.id,
        status=AppStatus.PUBLISHED,
        debug_conversation=conversation,
    )
    config = {
        "model_config": {"provider": "zhipu", "model": "glm-5.2"},
        "dialog_round": 3,
        "tools": [],
        "datasets": [],
        "workflows": [],
        "long_term_memory": {"enable": False},
        "preset_prompt": "",
        "review_config": {},
    }
    service = SimpleNamespace(
        db=MagicMock(),
        get_app=lambda *_args: app,
        get_web_app=lambda *_args: app,
        get_draft_app_config=lambda *_args: config,
        create=lambda *_args, **_kwargs: message,
        app_service=SimpleNamespace(get_app=lambda *_args: app),
        app_config_service=SimpleNamespace(
            get_app_config=lambda *_args: config,
            get_langchain_tools_by_tools_config=lambda *_args: [],
        ),
        language_model_manager=SimpleNamespace(
            create_language_model=lambda *_args: object()
        ),
        conversation_service=MagicMock(),
        retrieval_service=MagicMock(),
        get=lambda model, _id: (
            end_user if model.__name__ == "EndUser" else conversation
        ),
    )
    request = SimpleNamespace(
        **{
            key: SimpleNamespace(data=value)
            for key, value in {
                "image_urls": [],
                "app_id": app_id,
                "end_user_id": end_user.id,
                "conversation_id": conversation.id,
                "query": "问题",
                "stream": stream,
            }.items()
        }
    )
    with Flask(__name__).app_context():
        if entry == "debug":
            response = module.AppService.debug_chat(service, app_id, request, account)
        elif entry == "web":
            response = module.WebAppService.web_app_chat(
                service, "token", request, account
            )
        else:
            response = module.OpenApiService.chat(service, request, account)
        if stream:
            frames = list(response)
            payloads = [json.loads(frame.split("data:", 1)[1]) for frame in frames]
            assert all(data["message_id"] == str(message.id) for data in payloads)
            assert all(
                ("end_user_id" in data) is (entry == "public") for data in payloads
            )
            assert frames[-1].startswith(f"event: {terminal.value}\n")
            if terminal == QueueEvent.ERROR:
                assert payloads[-1]["observation"] == "Prompt exceeds max length"
            assert "".join(data["answer"] for data in payloads) == "答案"
            assert payloads[0]["total_price"] is None
            assert payloads[1]["usage"]["source"] == "provider"
            data = payloads[-1]
        else:
            data = response.data
    assert data["total_token_count"] == 120
    assert data["usage"]["input_tokens"] == 100
    if terminal == QueueEvent.ERROR:
        assert data["total_price"] is None
        assert data["usage"]["known_costs"] == {"CNY": "0.00036"}
    else:
        assert Decimal(data["total_price"]) == Decimal("0.00036")
    assert data["usage"]["complete"] is (terminal == QueueEvent.AGENT_END)
    saved = next(event for event in saves[0]["agent_thoughts"] if event.id == step_id)
    assert saved.answer == "答案"
    assert saved.usage == usage


@pytest.mark.parametrize("entry", ["debug", "public"])
def test_stream_disconnect_drains_agent_and_persists_terminal_result(
    monkeypatch, entry
):
    """客户端停止消费 SSE 后，Service 仍要消费同一 Agent 流并保存最终状态。"""
    module = importlib.import_module(
        "internal.service.app_service"
        if entry == "debug"
        else "internal.service.openapi_service"
    )
    step_id, task_id = uuid4(), uuid4()
    events = iter(
        [
            AgentThought(
                id=step_id,
                task_id=task_id,
                event=QueueEvent.AGENT_MESSAGE,
                answer="部分回答",
            ),
            AgentThought(
                id=uuid4(),
                task_id=task_id,
                event=QueueEvent.ERROR,
                observation="模型断开",
            ),
        ]
    )
    monkeypatch.setattr(
        chat_runtime,
        "FunctionCallAgent",
        lambda **_kwargs: SimpleNamespace(
            stream=lambda _state: events,
        ),
    )
    monkeypatch.setattr(
        chat_runtime, "AgentConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(
        chat_runtime,
        "TokenBufferMemory",
        lambda **_kwargs: SimpleNamespace(
            get_history_prompt_messages=lambda **_kwargs: [],
        ),
    )
    thread_calls = []

    class InlineThread:
        def __init__(self, target, kwargs):
            self.target = target
            self.kwargs = kwargs

        def start(self):
            thread_calls.append(self.kwargs)
            self.target(**self.kwargs)

    monkeypatch.setattr(chat_runtime, "Thread", InlineThread)
    account = SimpleNamespace(id=uuid4())
    app_id = uuid4()
    end_user = SimpleNamespace(id=uuid4(), app_id=app_id)
    conversation = SimpleNamespace(
        id=uuid4(),
        app_id=app_id,
        invoke_from=InvokeFrom.SERVICE_API,
        created_by=end_user.id,
        summary="",
    )
    message = SimpleNamespace(id=uuid4())
    app = SimpleNamespace(
        id=app_id,
        account_id=account.id,
        status=AppStatus.PUBLISHED,
        debug_conversation=conversation,
    )
    config = {
        "model_config": {"provider": "zhipu", "model": "glm-5.2"},
        "dialog_round": 3,
        "tools": [],
        "datasets": [],
        "workflows": [],
        "long_term_memory": {"enable": False},
        "preset_prompt": "",
        "review_config": {},
    }
    service = SimpleNamespace(
        db=MagicMock(),
        get_app=lambda *_args: app,
        get_draft_app_config=lambda *_args: config,
        create=lambda *_args, **_kwargs: message,
        app_service=SimpleNamespace(get_app=lambda *_args: app),
        app_config_service=SimpleNamespace(
            get_app_config=lambda *_args: config,
            get_langchain_tools_by_tools_config=lambda *_args: [],
        ),
        language_model_manager=SimpleNamespace(
            create_language_model=lambda *_args: object()
        ),
        conversation_service=MagicMock(),
        retrieval_service=MagicMock(),
        get=lambda model, _id: (
            end_user if model.__name__ == "EndUser" else conversation
        ),
    )
    request = SimpleNamespace(
        **{
            key: SimpleNamespace(data=value)
            for key, value in {
                "image_urls": [],
                "app_id": app_id,
                "end_user_id": end_user.id,
                "conversation_id": conversation.id,
                "query": "问题",
                "stream": True,
            }.items()
        }
    )

    with Flask(__name__).app_context():
        stream = (
            module.AppService.debug_chat(service, app_id, request, account)
            if entry == "debug"
            else module.OpenApiService.chat(service, request, account)
        )
        next(stream)
        stream.close()

    saved_calls = service.conversation_service.save_agent_thoughts.call_args_list
    assert len(saved_calls) == 1
    saved = saved_calls[0].kwargs["agent_thoughts"]
    assert [item.event for item in saved] == [
        QueueEvent.AGENT_MESSAGE,
        QueueEvent.ERROR,
    ]
    assert saved[0].answer == "部分回答"


def test_assistant_stream_disconnect_drains_and_persists(monkeypatch):
    """辅助 Agent 与调试/公开入口使用相同的断连保存语义。"""
    module = importlib.import_module("internal.service.assistant_agent_service")
    task_id = uuid4()
    events = iter(
        [
            AgentThought(
                id=uuid4(),
                task_id=task_id,
                event=QueueEvent.AGENT_MESSAGE,
                answer="部分回答",
            ),
            AgentThought(id=uuid4(), task_id=task_id, event=QueueEvent.STOP),
        ]
    )
    monkeypatch.setattr(
        module,
        "FunctionCallAgent",
        lambda **_kwargs: SimpleNamespace(
            stream=lambda _state: events,
        ),
    )
    monkeypatch.setattr(
        module, "AgentConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(
        module,
        "TokenBufferMemory",
        lambda **_kwargs: SimpleNamespace(
            get_history_prompt_messages=lambda **_kwargs: [],
        ),
    )

    class InlineThread:
        def __init__(self, target, kwargs):
            self.target = target
            self.kwargs = kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr(chat_runtime, "Thread", InlineThread)
    account = SimpleNamespace(
        id=uuid4(), assistant_agent_conversation=SimpleNamespace(id=uuid4(), summary="")
    )
    message = SimpleNamespace(id=uuid4())
    service = SimpleNamespace(
        db=MagicMock(),
        get=lambda *_args: account,
        create=lambda *_args, **_kwargs: message,
        language_model_manager=SimpleNamespace(
            create_default_language_model=lambda *_args: object()
        ),
        faiss_service=SimpleNamespace(convert_faiss_to_tool=lambda: MagicMock()),
        convert_create_app_to_tool=lambda *_args: MagicMock(),
        conversation_service=MagicMock(),
        retrieval_service=MagicMock(),
    )

    flask_app = Flask(__name__)
    flask_app.config["ASSISTANT_AGENT_ID"] = uuid4()
    with flask_app.app_context():
        stream = module.AssistantAgentService.assistant_agent_chat(
            service, SimpleNamespace(query=SimpleNamespace(data="问题"), image_urls=SimpleNamespace(data=[])), account.id
        )
        next(stream)
        stream.close()

    saved = service.conversation_service.save_agent_thoughts.call_args.kwargs[
        "agent_thoughts"
    ]
    assert [item.event for item in saved] == [QueueEvent.AGENT_MESSAGE, QueueEvent.STOP]
