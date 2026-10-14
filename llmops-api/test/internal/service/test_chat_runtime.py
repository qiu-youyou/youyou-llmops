import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import Flask

from internal.core.agent.entities.queue_entity import AgentThought, QueueEvent
from internal.entity.conversation_entity import InvokeFrom
from internal.service import chat_runtime as module


@pytest.fixture
def runtime(monkeypatch):
    class InlineThread:
        def __init__(self, target, kwargs):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr(module, "Thread", InlineThread)
    service = MagicMock()
    config = {"long_term_memory": {"enable": False}}
    with Flask(__name__).app_context():
        runtime = module.ChatRuntime(
            conversation_service=service,
            account_id=uuid4(),
            app_id=uuid4(),
            app_config=config,
            conversation_id=uuid4(),
            message_id=uuid4(),
        )
    config["long_term_memory"]["enable"] = True
    return runtime, service


@pytest.mark.parametrize("terminal", list(module.TERMINAL_EVENTS))
@pytest.mark.parametrize("disconnect", [False, True])
def test_terminal_stops_consumption_and_saves_once(runtime, terminal, disconnect):
    runner, service = runtime
    task_id = uuid4()
    closed = []

    def source():
        try:
            yield AgentThought(
                id=uuid4(),
                task_id=task_id,
                event=QueueEvent.AGENT_MESSAGE,
                answer="部分答案",
            )
            yield AgentThought(id=uuid4(), task_id=task_id, event=terminal)
            pytest.fail("终止事件后不应再读取 Agent")
        finally:
            closed.append(True)

    stream = runner.stream(SimpleNamespace(stream=lambda _: source()), {})
    first = next(stream)
    if disconnect:
        stream.close()
    else:
        frames = [first, *stream]
        assert len(frames) == 2
        assert frames[-1].startswith(f"event: {terminal.value}\n")
        # Fake 未提供用量；即使正常终止也不能宣称已完整结算。
        assert json.loads(frames[-1].split("data:")[1])["usage"]["complete"] is False
        stream.close()
    service.save_agent_thoughts.assert_called_once()
    saved = service.save_agent_thoughts.call_args.kwargs
    assert [event.event for event in saved["agent_thoughts"]] == [
        QueueEvent.AGENT_MESSAGE,
        terminal,
    ]
    assert saved["app_config"]["long_term_memory"]["enable"] is False
    assert closed == [True]


@pytest.mark.parametrize("failure", ["startup", "iteration", "missing_terminal"])
@pytest.mark.parametrize("disconnect", [False, True])
def test_unexpected_stream_failure_is_terminal_and_persisted(
    runtime, failure, disconnect
):
    runner, service = runtime

    def source():
        if failure == "startup":
            raise ValueError("private implementation details")
        yield AgentThought(
            id=uuid4(),
            task_id=uuid4(),
            event=QueueEvent.AGENT_MESSAGE,
            answer="partial",
        )
        if failure == "iteration":
            raise ValueError("private implementation details")

    stream = runner.stream(SimpleNamespace(stream=lambda _: source()), {})
    first = next(stream)
    if disconnect:
        stream.close()
    else:
        frames = [first, *stream]
        assert frames[-1].startswith("event: error\n")
        assert "private implementation details" not in frames[-1]
    service.save_agent_thoughts.assert_called_once()
    saved = service.save_agent_thoughts.call_args.kwargs["agent_thoughts"]
    assert saved[-1].event == QueueEvent.ERROR


def test_app_resource_owner_and_task_operator_are_distinct(monkeypatch):
    owner, visitor = uuid4(), uuid4()
    retrieval, config_service = MagicMock(), MagicMock()
    config_service.get_langchain_tools_by_tools_config.return_value = [
        "configured-tool"
    ]
    config_service.get_langchain_tools_by_workflow_ids.return_value = ["workflow-tool"]
    retrieval.create_langchain_tool_from_search.return_value = "dataset-tool"
    monkeypatch.setattr(
        module,
        "TokenBufferMemory",
        lambda **_: SimpleNamespace(get_history_prompt_messages=lambda **_: []),
    )
    monkeypatch.setattr(module, "FunctionCallAgent", lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "AgentConfig", lambda **kwargs: kwargs)
    with Flask(__name__).app_context():
        agent, _ = module.prepare_app_agent(
            req=SimpleNamespace(image_urls=SimpleNamespace(data=[])),
            db=MagicMock(),
            language_model_manager=MagicMock(),
            app_config_service=config_service,
            retrieval_service=retrieval,
            config={
                "model_config": {},
                "dialog_round": 3,
                "tools": [],
                "datasets": [{"id": "dataset"}],
                "retrieval_config": {},
                "workflows": [{"id": "workflow"}],
                "long_term_memory": {"enable": False},
                "preset_prompt": "",
                "review_config": {},
            },
            conversation=None,
            resource_owner_id=owner,
            operator_id=visitor,
            invoke_from=InvokeFrom.WEB_APP,
        )
    assert (
        retrieval.create_langchain_tool_from_search.call_args.kwargs["account_id"]
        == owner
    )
    assert agent["agent_config"]["user_id"] == visitor
    assert agent["agent_config"]["tools"] == [
        "configured-tool",
        "dataset-tool",
        "workflow-tool",
    ]
    assert config_service.get_langchain_tools_by_tools_config.return_value == [
        "configured-tool"
    ]


def test_thread_start_failure_completes_disconnect_inline(runtime, monkeypatch):
    runner, service = runtime

    class UnavailableThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module, "Thread", UnavailableThread)
    events = [
        AgentThought(id=uuid4(), task_id=uuid4(), event=event)
        for event in [QueueEvent.AGENT_MESSAGE, QueueEvent.STOP]
    ]
    stream = runner.stream(SimpleNamespace(stream=lambda _: iter(events)), {})
    next(stream)
    stream.close()
    service.save_agent_thoughts.assert_called_once()
    assert (
        service.save_agent_thoughts.call_args.kwargs["agent_thoughts"][-1].event
        == QueueEvent.STOP
    )


def test_iterator_close_failure_does_not_lose_terminal_or_save(runtime):
    runner, service = runtime

    def source():
        try:
            yield AgentThought(id=uuid4(), task_id=uuid4(), event=QueueEvent.AGENT_END)
        finally:
            raise RuntimeError("close failed")

    frames = list(runner.stream(SimpleNamespace(stream=lambda _: source()), {}))
    assert len(frames) == 1 and frames[0].startswith("event: agent_end\n")
    service.save_agent_thoughts.assert_called_once()


@pytest.mark.parametrize("entry", ["debug", "assistant", "public"])
def test_preparation_failure_is_eager_and_does_not_create_message(entry):
    from internal.service.app_service import AppService
    from internal.service.assistant_agent_service import AssistantAgentService
    from internal.service.openapi_service import OpenApiService
    from internal.entity.app_entity import AppStatus

    account = SimpleNamespace(
        id=uuid4(), assistant_agent_conversation=SimpleNamespace(id=uuid4())
    )
    app = SimpleNamespace(
        id=uuid4(),
        account_id=account.id,
        status=AppStatus.PUBLISHED,
        debug_conversation=account.assistant_agent_conversation,
    )
    end_user = SimpleNamespace(id=uuid4(), app_id=app.id)
    conversation = SimpleNamespace(
        app_id=app.id, created_by=end_user.id, invoke_from=InvokeFrom.SERVICE_API
    )
    manager = MagicMock()
    manager.create_language_model.side_effect = ValueError("bad config")
    manager.create_default_language_model.side_effect = ValueError("bad config")
    service = MagicMock()
    service.language_model_manager = manager
    service.get_app.return_value = app
    service.app_service.get_app.return_value = app
    service.get_draft_app_config.return_value = {"model_config": {}}
    service.app_config_service.get_app_config.return_value = {"model_config": {}}
    service.get.side_effect = (
        [account] if entry == "assistant" else [end_user, conversation]
    )
    with Flask(__name__).app_context(), pytest.raises(ValueError, match="bad config"):
        if entry == "debug":
            AppService.debug_chat(service, app.id, SimpleNamespace(image_urls=SimpleNamespace(data=[])), account)
        elif entry == "assistant":
            AssistantAgentService.assistant_agent_chat(service, SimpleNamespace(image_urls=SimpleNamespace(data=[])), account.id)
        else:
            req = SimpleNamespace(
                **{
                    k: SimpleNamespace(data=v)
                    for k, v in {
                        "image_urls": [],
                        "app_id": app.id,
                        "end_user_id": end_user.id,
                        "conversation_id": uuid4(),
                    }.items()
                }
            )
            OpenApiService.chat(service, req, account)
    service.create.assert_not_called()
