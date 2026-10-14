"""Offline image contracts: Flask JSON, message history and capability gate."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from flask import Flask

from internal.schema.app_schema import DebugChatReq
from internal.schema.assistant_agent_schema import AssistantAgentChat
from internal.schema.web_app_schema import WebAppChatReq
from internal.schema.openapi_schema import OpenAPIChatReq
from internal.core.language_model.messages import build_human_message, format_query_for_text_model
from internal.core.memory.token_buffer_memory import TokenBufferMemory, simple_token_counter
from internal.core.agent.agents.function_call_agent import FunctionCallAgent
from internal.service import chat_runtime

URL = 'https://example.com/image.png'


@pytest.mark.parametrize('form_class', [DebugChatReq, AssistantAgentChat, WebAppChatReq, OpenAPIChatReq])
@pytest.mark.parametrize('payload,valid', [
    ({'query': 'hello'}, True),
    ({'query': 'hello', 'image_urls': [URL, URL]}, True),
    ({'image_urls': [URL]}, True),
    ({'query': '', 'image_urls': [URL]}, True),
    ({'query': '  ', 'image_urls': []}, False),
    ({'query': 42, 'image_urls': [URL]}, False),
    ({'query': None, 'image_urls': [URL]}, False),
    ({'query': 'hello', 'image_urls': None}, False),
    ({'query': 'hello', 'image_urls': URL}, False),
    ({'query': 'hello', 'image_urls': {}}, False),
    ({'image_urls': [URL] * 5}, True),
    ({'image_urls': [URL] * 6}, False),
    ({'image_urls': ['https://example..com/image.png']}, False),
    ({'image_urls': ['https://0x7f.0.0.1/image.png']}, False),
    ({'image_urls': ['https://example.com:bad/image.png']}, False),
    ({'image_urls': [42]}, False),
    ({'image_urls': ['https://user:pass@example.com/a']}, False),
    ({'image_urls': ['https://localhost/a']}, False),
    ({'image_urls': ['https://127.0.0.1/a']}, False),
    ({'image_urls': ['https://[::1]/a']}, False),
    ({'image_urls': ['https://10.1.1.1/a']}, False),
    ({'image_urls': ['http://example.com/a']}, False),
    ({'image_urls': ['data:image/png;base64,abc']}, False),
    ({'image_urls': ['https://example.com/' + 'a' * 4096]}, False),
])
def test_json_contract(form_class, payload, valid):
    app = Flask(__name__)
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_request_context(method='POST', json={'app_id': str(uuid4()), **payload}):
        form = form_class()
        assert form.validate() is valid, form.errors
        if valid:
            assert form.image_urls.data == payload.get('image_urls', [])
            assert form.query.data == payload.get('query', '')


def memory(rows, features):
    query = MagicMock()
    query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    return TokenBufferMemory(SimpleNamespace(session=SimpleNamespace(query=lambda _: query)),
                             SimpleNamespace(id=uuid4()), SimpleNamespace(features=features))


def test_history_restores_images_and_trims_whole_turns():
    rows = [SimpleNamespace(query='latest', answer='answer', image_urls=[URL]),
            SimpleNamespace(query='older', answer='answer', image_urls=[URL])]
    history = memory(rows, ['image_input']).get_history_prompt_messages(max_token_limit=2000)
    assert len(history) == 2
    assert history[0].content[1]['image_url']['url'] == URL
    assert history[0].content[0]['text'] == 'latest'
    assert simple_token_counter(history) == 1028
    assert memory(rows, ['image_input']).get_history_prompt_messages(max_token_limit=1023) == []


def test_text_model_history_placeholder_and_legacy_rows():
    rows = [SimpleNamespace(query='', answer='answer', image_urls=[URL]),
            SimpleNamespace(query='legacy', answer='answer', image_urls=[])]
    history = memory(rows, []).get_history_prompt_messages()
    assert history[0].content == 'legacy'
    assert history[2].content == '\n[历史图片未提供：1张]'
    assert rows[0].image_urls == [URL]
    text = memory(rows, ['image_input']).get_history_prompt_text()
    assert URL not in text
    assert '附带1张图片' in text


def test_builder_and_auxiliary_prompts():
    assert build_human_message('hello', []).content == 'hello'
    assert build_human_message('', [URL]).content == [{'type': 'image_url', 'image_url': {'url': URL}}]
    assert format_query_for_text_model('', [URL]) == '\n[附带1张图片]'


def test_capability_rejected_before_history_tools_or_agent(monkeypatch):
    history = MagicMock()
    monkeypatch.setattr(chat_runtime, 'TokenBufferMemory', history)
    with pytest.raises(Exception, match='不支持图片'):
        chat_runtime.prepare_app_agent(db=None, language_model_manager=SimpleNamespace(create_language_model=lambda _: SimpleNamespace(features=[])),
            app_config_service=None, retrieval_service=None, config={'model_config': {}}, conversation=None,
            resource_owner_id=uuid4(), operator_id=uuid4(), invoke_from='debugger', req=SimpleNamespace(image_urls=SimpleNamespace(data=[URL])))
    history.assert_not_called()


def test_keyword_review_extracts_text_only():
    queue = MagicMock()
    fake = SimpleNamespace(agent_config=SimpleNamespace(review_config={
        'enable': True, 'inputs_config': {'enable': True, 'preset_response': 'blocked'}, 'keywords': ['secret']
    }), agent_queue_manager=queue)
    task = uuid4()
    FunctionCallAgent._preset_operation_node(fake, {'task_id': task, 'messages': [build_human_message('secret', [URL])]})
    assert queue.publish.call_count == 2
    queue.reset_mock()
    FunctionCallAgent._preset_operation_node(fake, {'task_id': task, 'messages': [build_human_message('', ['https://example.com/secret.png'])]})
    queue.publish.assert_not_called()


@pytest.mark.parametrize('entry', ['debug', 'web', 'assistant', 'public', 'public_sync'])
@pytest.mark.parametrize('query,urls', [('text', []), ('describe', [URL, URL]), ('', [URL])])
@pytest.mark.parametrize('supported', [True, False])
def test_chat_entrypoints_persist_and_deliver_images(monkeypatch, entry, query, urls, supported):
    from internal.service.app_service import AppService
    from internal.service.web_app_service import WebAppService
    from internal.service.assistant_agent_service import AssistantAgentService
    from internal.service.openapi_service import OpenApiService
    from internal.service import assistant_agent_service
    from internal.entity.app_entity import AppStatus
    from internal.entity.conversation_entity import InvokeFrom
    from internal.core.agent.entities.queue_entity import AgentResult
    from internal.exception import ValidateErrorException

    user = SimpleNamespace(id=uuid4())
    end_user = SimpleNamespace(id=uuid4())
    conversation = SimpleNamespace(id=uuid4(), summary='', is_deleted=False,
        invoke_from=InvokeFrom.WEB_APP if entry == 'web' else InvokeFrom.SERVICE_API,
        created_by=user.id if entry == 'web' else end_user.id)
    user.assistant_agent_conversation = conversation
    app = SimpleNamespace(id=uuid4(), account_id=user.id, status=AppStatus.PUBLISHED, debug_conversation=conversation)
    end_user.app_id = conversation.app_id = app.id
    config = dict(model_config={}, dialog_round=3, tools=[], datasets=[], workflows=[],
                  long_term_memory={'enable': False}, preset_prompt='', review_config={})
    model = SimpleNamespace(features=['image_input'] if supported else [])
    service = MagicMock()
    service.get_app.return_value = service.get_web_app.return_value = service.app_service.get_app.return_value = app
    service.get_draft_app_config.return_value = service.app_config_service.get_app_config.return_value = config
    service.get.side_effect = lambda cls, _: user if cls.__name__ == 'Account' else end_user if cls.__name__ == 'EndUser' else conversation
    service.language_model_manager.create_language_model.return_value = model
    service.language_model_manager.create_default_language_model.return_value = model
    service.create.return_value = SimpleNamespace(id=uuid4())
    service.app_config_service.get_langchain_tools_by_tools_config.return_value = []
    agent = MagicMock()
    agent.invoke.return_value = AgentResult(answer='answer', agent_thoughts=[])
    for module in [chat_runtime, assistant_agent_service]:
        monkeypatch.setattr(module, 'FunctionCallAgent', lambda **_: agent)
        monkeypatch.setattr(module, 'TokenBufferMemory', lambda **_: SimpleNamespace(get_history_prompt_messages=lambda **_: []))
    monkeypatch.setattr(assistant_agent_service, 'AgentConfig', lambda **kwargs: SimpleNamespace(**kwargs))
    runtime = MagicMock()
    runtime.stream.side_effect = lambda agent, state: state
    for module_name in ['app_service', 'web_app_service', 'assistant_agent_service', 'openapi_service']:
        import importlib
        monkeypatch.setattr(importlib.import_module('internal.service.' + module_name), 'ChatRuntime', lambda **_: runtime)
    req = SimpleNamespace(**{k: SimpleNamespace(data=v) for k, v in dict(
        app_id=app.id, end_user_id=end_user.id, conversation_id=conversation.id,
        query=query, image_urls=urls, stream=entry != 'public_sync').items()})
    def call():
        if entry == 'debug': return AppService.debug_chat(service, app.id, req, user)
        if entry == 'web': return WebAppService.web_app_chat(service, 'token', req, user)
        if entry == 'assistant': return AssistantAgentService.assistant_agent_chat(service, req, user.id)
        return OpenApiService.chat(service, req, user)
    with Flask(__name__).app_context():
        if urls and not supported:
            with pytest.raises(ValidateErrorException, match='不支持图片'): call()
            service.create.assert_not_called()
            runtime.stream.assert_not_called()
            return
        call()
    saved = service.create.call_args.kwargs
    assert saved['query'] == query
    assert saved['image_urls'] == urls
    state = agent.invoke.call_args.args[0] if entry == 'public_sync' else runtime.stream.call_args.args[1]
    assert state['messages'][0].content == build_human_message(query, urls).content


def test_image_only_save_uses_title_and_summary_placeholder():
    from contextlib import nullcontext
    from internal.service.conversation_service import ConversationService
    from internal.model import Message
    from internal.core.agent.entities.queue_entity import AgentThought, QueueEvent
    service = ConversationService(db=MagicMock(), language_model_manager=MagicMock())
    service.db.session.get.return_value = None
    service.summary = MagicMock(return_value='summary')
    service.generate_conversation_name = MagicMock()
    conversation = SimpleNamespace(id=uuid4(), is_new=True, name='', summary='')
    message = Message(id=uuid4(), query='', image_urls=[URL], invoke_from='debugger')
    service.get = lambda model, _: message if model is Message else conversation
    service.save_agent_thoughts(flask_app=SimpleNamespace(app_context=nullcontext), account_id=uuid4(),
        app_id=uuid4(), app_config={'long_term_memory': {'enable': True}},
        conversation_id=conversation.id, message_id=message.id,
        agent_thoughts=[AgentThought(id=uuid4(), task_id=uuid4(), event=QueueEvent.AGENT_MESSAGE, answer='a cat')])
    assert conversation.name == '图片对话'
    service.generate_conversation_name.assert_not_called()
    service.summary.assert_called_once_with('\n[附带1张图片]', 'a cat', '')
    assert message.image_urls == [URL]


@pytest.mark.parametrize('urls', [None, [URL]])
def test_history_response_includes_images_and_legacy_default(urls):
    from datetime import datetime
    from internal.schema.app_schema import GetDebugConversationMessagesWithPageResp
    from internal.schema.assistant_agent_schema import GetAssistantAgentMessagesWithPageResp
    from internal.schema.conversation_schema import GetConversationMessagesWithPageResp
    row = SimpleNamespace(id=uuid4(), conversation_id=uuid4(), query='', answer='cat',
        total_token_count=0, message_token_count=0, answer_token_count=0, usage={}, latency=0,
        agent_thoughts=[], image_urls=urls, created_at=datetime(2026, 10, 14))
    for schema in [GetDebugConversationMessagesWithPageResp, GetAssistantAgentMessagesWithPageResp, GetConversationMessagesWithPageResp]:
        assert schema().dump(row)['image_urls'] == (urls or [])


def test_default_visual_history_budget_retains_five_images():
    history = memory([SimpleNamespace(query='', answer='five pictures', image_urls=[URL] * 5)], ['image_input'])
    messages = history.get_history_prompt_messages()
    assert len(messages) == 2
    assert len(messages[0].content) == 5


def test_default_capabilities_read_catalog_without_sdk(monkeypatch):
    from internal.core.language_model import LanguageModelManager
    from internal.service.assistant_agent_service import AssistantAgentService

    monkeypatch.setenv('SYSTEM_LLM_PROVIDER', 'openai')
    monkeypatch.setenv('SYSTEM_LLM_MODEL', 'gpt-4o-mini')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    service = SimpleNamespace(language_model_manager=LanguageModelManager())
    capabilities = AssistantAgentService.capabilities(service)
    assert 'image_input' in capabilities['features']
    assert 'image_urls' not in capabilities['features']


def test_web_app_features_come_from_published_model():
    from internal.core.language_model import LanguageModelManager
    from internal.service.web_app_service import WebAppService

    service = SimpleNamespace(
        language_model_manager=LanguageModelManager(),
        app_config_service=SimpleNamespace(get_app_config=lambda _: {
            'model_config': {'provider': 'openai', 'model': 'gpt-4o-mini'}
        }),
    )
    assert 'image_input' in WebAppService.get_model_features(service, SimpleNamespace())
