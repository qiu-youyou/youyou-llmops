#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   openapi_service
@Time   :   2026/2/27 13:44
@Author :   s.qiu@foxmail.com
"""

from dataclasses import dataclass
from injector import inject

from internal.core.language_model.messages import build_human_message
from internal.core.language_model import LanguageModelManager
from internal.entity.app_entity import AppStatus
from internal.entity.conversation_entity import InvokeFrom, MessageStatus
from internal.exception import NotFoundException, ForbiddenException
from internal.model import Account, EndUser, Conversation, Message
from internal.schema.openapi_schema import OpenAPIChatReq
from pkg.response import Response
from pkg.sqlalchemy import SQLAlchemy
from .app_config_service import AppConfigService
from .app_service import AppService
from .chat_runtime import ChatRuntime, prepare_app_agent
from .base_service import BaseService
from .conversation_service import ConversationService
from .retrieval_service import RetrievalService


@inject
@dataclass
class OpenApiService(BaseService):
    """开放API服务"""

    db: SQLAlchemy
    app_service: AppService
    app_config_service: AppConfigService
    retrieval_service: RetrievalService
    conversation_service: ConversationService
    language_model_manager: LanguageModelManager

    def chat(self, req: OpenAPIChatReq, account: Account):
        """开放API 发起对话，返回块内容或生成器"""

        # 获取当前应用 应用状态是否已发布
        app = self.app_service.get_app(req.app_id.data, account)
        if app.status != AppStatus.PUBLISHED:
            raise NotFoundException("应用不存在或未发布！")

        # 是否传递了终端用户ID 如果传递了判断是否关联当前应用 否则需要创建终端用户
        if req.end_user_id.data:
            end_user = self.get(EndUser, req.end_user_id.data)
            if not end_user or end_user.app_id != app.id:
                raise ForbiddenException("当前账号不存在或未关联当前应用！")
        else:
            end_user = self.create(
                EndUser, **{"tenant_id": account.id, "app_id": app.id}
            )

        # 是否传递了会话ID 传递了需要检测会话归属信息 否则需要创建会话
        if req.conversation_id.data:
            conversation = self.get(Conversation, req.conversation_id.data)
            if (
                not conversation
                or conversation.app_id != app.id
                or conversation.invoke_from != InvokeFrom.SERVICE_API
                or conversation.created_by != end_user.id
            ):
                raise ForbiddenException("会话不存在或不属于该用户/应用/调用方式")
        else:
            conversation = self.create(
                Conversation,
                **{
                    "app_id": app.id,
                    "name": "New Conversation",
                    "invoke_from": InvokeFrom.SERVICE_API,
                    "created_by": end_user.id,
                },
            )

        # 获取当前应用的运行配置
        app_config = self.app_config_service.get_app_config(app)

        # 运行准备成功后再创建消息，避免模型配置错误留下空消息。
        agent, history = prepare_app_agent(
            db=self.db,
            language_model_manager=self.language_model_manager,
            app_config_service=self.app_config_service,
            retrieval_service=self.retrieval_service,
            config=app_config,
            conversation=conversation,
            resource_owner_id=app.account_id,
            operator_id=account.id,
            invoke_from=InvokeFrom.SERVICE_API,
            req=req,
        )

        message = self.create(
            Message,
            **{
                "app_id": app.id,
                "conversation_id": conversation.id,
                "invoke_from": InvokeFrom.SERVICE_API,
                "created_by": end_user.id,
                "query": req.query.data,
                "image_urls": req.image_urls.data,
                "status": MessageStatus.NORMAL,
            },
        )

        agent_state = {
            "messages": [build_human_message(req.query.data, req.image_urls.data)],
            "long_term_memory": conversation.summary,
            "history": history,
        }

        runtime = ChatRuntime(
            conversation_service=self.conversation_service,
            account_id=account.id,
            app_id=app.id,
            app_config=app_config,
            conversation_id=conversation.id,
            message_id=message.id,
            end_user_id=end_user.id,
        )
        if req.stream.data is True:
            return runtime.stream(agent, agent_state)

        agent_result = agent.invoke(agent_state)
        runtime.save(agent_result.agent_thoughts)

        return Response(
            data={
                "id": str(message.id),
                "end_user_id": str(end_user.id),
                "conversation_id": str(conversation.id),
                "query": req.query.data,
                "image_urls": req.image_urls.data,
                "answer": agent_result.answer,
                "total_token_count": agent_result.total_token_count,
                "message_token_count": agent_result.message_token_count,
                "answer_token_count": agent_result.answer_token_count,
                "total_price": agent_result.usage.get("total_price"),
                "usage": agent_result.usage,
                "latency": agent_result.latency,
                "agent_thoughts": [
                    {
                        "id": str(agent_thought.id),
                        "event": agent_thought.event,
                        "thought": agent_thought.thought,
                        "observation": agent_thought.observation,
                        "tool": agent_thought.tool,
                        "tool_input": agent_thought.tool_input,
                        "latency": agent_thought.latency,
                        "created_at": 0,
                        "usage": (
                            agent_thought.usage.model_dump(mode="json")
                            if agent_thought.usage
                            else None
                        ),
                    }
                    for agent_thought in agent_result.agent_thoughts
                ],
            }
        )
