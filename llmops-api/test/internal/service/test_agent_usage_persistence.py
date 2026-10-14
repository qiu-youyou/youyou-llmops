"""不启动 Flask Factory/数据库；验证 ORM 保存值与历史接口序列化。"""
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from internal.core.agent.entities.queue_entity import AgentThought, QueueEvent
from internal.core.language_model.usage import TokenUsage
from internal.model import Message, MessageAgentThought
from internal.schema.app_schema import GetDebugConversationMessagesWithPageResp
from internal.schema.assistant_agent_schema import GetAssistantAgentMessagesWithPageResp
from internal.service.conversation_service import ConversationService


def test_storage_keeps_snapshots_and_repeated_save_does_not_double_count():
    db = MagicMock()
    rows = {}
    db.session.add.side_effect = lambda row: rows.setdefault(row.id, row)
    db.session.get.side_effect = lambda _model, key: rows.get(key)
    service = ConversationService(db=db, language_model_manager=MagicMock())
    conversation = SimpleNamespace(id=uuid4(), is_new=False)
    message = Message(id=uuid4(), query="问题", invoke_from="service_api")
    service.get = lambda model, _id: message if model is Message else conversation
    app = SimpleNamespace(app_context=nullcontext)
    usage = TokenUsage(
        provider="deepseek", model="deepseek-v4-pro", source="provider", complete=True,
        input_tokens=10, output_tokens=2, total_tokens=12, cache_read_tokens=8,
        total_price=Decimal("0.00000004"), currency="CNY", price_status="calculated",
        pricing={"input": "1", "output": "1", "unit": "0.000001"},
    )
    task_id, step_id = uuid4(), uuid4()
    part = AgentThought(id=step_id, task_id=task_id, event=QueueEvent.AGENT_MESSAGE, answer="答案")
    settlement = AgentThought(id=step_id, task_id=task_id, event=QueueEvent.AGENT_MESSAGE,
                              usage=usage, **usage.legacy_fields())
    tool_round = AgentThought(id=uuid4(), task_id=task_id, event=QueueEvent.AGENT_THOUGHT,
                              thought="调用工具", usage=usage, **usage.legacy_fields())
    kwargs = dict(flask_app=app, account_id=uuid4(), app_id=uuid4(),
                  app_config={"long_term_memory": {"enable": False}},
                  conversation_id=conversation.id, message_id=message.id,
                  agent_thoughts=[tool_round, part, settlement, settlement])
    service.save_agent_thoughts(**kwargs)
    service.save_agent_thoughts(**kwargs)
    assert len(rows) == 2
    assert all(isinstance(row, MessageAgentThought) for row in rows.values())
    assert rows[step_id].answer == "答案"
    assert rows[step_id].invoke_from == "service_api"
    assert rows[step_id].message_price_unit == Decimal("0.000001")
    assert rows[step_id].usage["cache_read_tokens"] == 8
    assert message.answer == "答案"
    assert message.total_token_count == 24
    assert message.message_token_count == 20
    assert message.answer_token_count == 4
    assert message.total_price == Decimal("0.00000008")
    assert message.usage["complete"] is True

    # ORM 保存的 JSON 和历史消息接口都保留精确金额及状态。
    record = SimpleNamespace(id=message.id, conversation_id=conversation.id,
        query=message.query, image_urls=[], answer=message.answer, total_token_count=message.total_token_count,
        message_token_count=message.message_token_count, answer_token_count=message.answer_token_count,
        usage=message.usage, latency=0, created_at=datetime(2026, 9, 18), agent_thoughts=[])
    for schema in [GetDebugConversationMessagesWithPageResp(), GetAssistantAgentMessagesWithPageResp()]:
        result = schema.dump(record)
        assert Decimal(result["total_price"]) == Decimal("0.00000008")
        assert result["usage"]["complete"] is True


def test_error_without_final_usage_is_saved_as_incomplete():
    service = ConversationService(db=MagicMock(), language_model_manager=MagicMock())
    service.db.session.get.return_value = None
    message = Message(id=uuid4(), query="问题", invoke_from="debugger")
    conversation = SimpleNamespace(id=uuid4(), is_new=False)
    service.get = lambda model, _id: message if model is Message else conversation
    service.save_agent_thoughts(
        flask_app=SimpleNamespace(app_context=nullcontext), account_id=uuid4(), app_id=uuid4(),
        app_config={"long_term_memory": {"enable": False}}, conversation_id=conversation.id,
        message_id=message.id, agent_thoughts=[
            AgentThought(id=uuid4(), task_id=uuid4(), event=QueueEvent.ERROR, observation="连接断开")
        ],
    )
    assert message.status == "error"
    assert message.error == "连接断开"
    assert message.usage["complete"] is False
    assert message.usage["total_price"] is None
