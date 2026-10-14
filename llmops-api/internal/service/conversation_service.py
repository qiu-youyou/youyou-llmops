#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   coversition_service
@Time   :   2026/1/21 10:52
@Author :   s.qiu@foxmail.com
"""

from datetime import datetime
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from flask import Flask
from injector import inject
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import desc

from internal.core.language_model.messages import format_query_for_text_model
from internal.core.agent.entities.queue_entity import AgentThought, QueueEvent
from internal.core.agent.usage import (
    merge_agent_thought,
    summarize_usage,
    summary_fields,
    thought_usage_fields,
)
from internal.core.language_model import LanguageModelManager
from internal.entity.conversation_entity import (
    SUMMARIZER_TEMPLATE,
    CONVERSATION_NAME_TEMPLATE,
    SUGGESTED_QUESTIONS_TEMPLATE,
    MessageStatus,
    SuggestedQuestions,
    InvokeFrom,
)
from internal.exception.exception import NotFoundException, ValidateErrorException
from internal.model.account import Account
from internal.schema.conversation_schema import GetConversationMessagesWithPageReq
from internal.model import Conversation, Message, MessageAgentThought
from pkg.paginator.paginator import Paginator
from pkg.sqlalchemy import SQLAlchemy
from .base_service import BaseService


@inject
@dataclass
class ConversationService(BaseService):
    """会话服务"""

    db: SQLAlchemy
    language_model_manager: LanguageModelManager

    def get_conversation(self, conversation_id: UUID, account: Account) -> Conversation:
        """获取指定的会话信息"""
        # 根据 conversation_id 查询会话记录
        conversation = self.get(Conversation, conversation_id)
        if (
            not conversation
            or conversation.created_by != account.id
            or conversation.is_deleted
        ):
            raise NotFoundException("该会话不存在或被删除")

        return conversation

    def get_message(self, message_id: UUID, account: Account) -> Message:
        """获取指定的消息"""
        # 根据 message_id 查询消息记录
        message = self.get(Message, message_id)
        if not message or message.created_by != account.id or message.is_deleted:
            raise NotFoundException("该消息不存在或被删除")

        return message

    def get_conversation_messages_with_page(
        self,
        conversation_id: UUID,
        req: GetConversationMessagesWithPageReq,
        account: Account,
    ) -> tuple[list[Message], Paginator]:
        """获取当前账号下该会话的消息分页列表"""
        conversation = self.get_conversation(conversation_id, account)

        paginator = Paginator(db=self.db, req=req)
        filters = []
        if req.created_at.data:
            try:
                created_at_datetime = datetime.fromtimestamp(req.created_at.data)
            except (OverflowError, OSError, ValueError) as exc:
                raise ValidateErrorException("created_at游标格式错误") from exc
            filters.append(Message.created_at <= created_at_datetime)

        messages = paginator.paginate(
            self.db.session.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.app_id == conversation.app_id,
                Message.invoke_from == conversation.invoke_from,
                Message.created_by == conversation.created_by,
                Message.status.in_([MessageStatus.STOP, MessageStatus.NORMAL]),
                Message.answer != "",
                ~Message.is_deleted,
                *filters,
            )
            .order_by(desc("created_at"))
        )

        return messages, paginator

    def update_conversation(
        self, conversation_id: UUID, account: Account, **kwargs
    ) -> Conversation:
        """根据传递的会话id+账号+kwargs更新会话信息"""
        conversation = self.get_conversation(conversation_id, account)

        # 更新会话信息
        self.update(conversation, **kwargs)

        return conversation

    def delete_message(
        self, conversation_id: UUID, message_id: UUID, account: Account
    ) -> Message:
        """删除指定的消息记录"""
        conversation = self.get_conversation(conversation_id, account)

        # 获取消息并校验权限
        message = self.get_message(message_id, account)

        # 判断消息和会话是否关联
        if (
            conversation.id != message.conversation_id
            or conversation.app_id != message.app_id
            or conversation.invoke_from != message.invoke_from
        ):
            raise NotFoundException("该会话下不存在该消息，请核实后重试")

        # 校验通过修改消息is_deleted属性标记删除
        self.update(message, is_deleted=True)

        return message

    def delete_conversation(
        self, conversation_id: UUID, account: Account
    ) -> Conversation:
        """删除指定的会话记录"""
        conversation = self.get_conversation(conversation_id, account)

        # 更新会话的删除状态
        self.update(conversation, is_deleted=True)

        return conversation

    def summary(
        self, human_message: str, ai_message: str, old_summary: str = ""
    ) -> str:
        """根据消息和旧的摘要生成 新摘要"""
        prompt = ChatPromptTemplate.from_template(SUMMARIZER_TEMPLATE)
        llm = self.language_model_manager.create_default_language_model(
            {"temperature": 0.5}
        )
        # 构建链应用
        chain = prompt | llm | StrOutputParser()
        new_summary = chain.invoke(
            {
                "new_lines": f"Human: {human_message}\nAI: {ai_message}",
                "summary": old_summary,
            }
        )

        return new_summary

    def generate_conversation_name(self, query: str) -> str:
        """根据 query 生成当前 会话名称"""
        fallback_name = self._normalize_conversation_name(query, "新的对话")
        # 提取query 截取过长的部分
        if len(query) > 2000:
            query = query[:300] + "...[TRUNCATED]" + query[-300:]
        query = query.replace("\n", " ")
        try:
            prompt = ChatPromptTemplate.from_messages(
                [("system", CONVERSATION_NAME_TEMPLATE), ("human", "{query}")]
            )
            llm = self.language_model_manager.create_default_language_model(
                {"temperature": 0}
            )
            chain = prompt | llm | StrOutputParser()
            generated_name = chain.invoke({"query": query})
            return self._normalize_conversation_name(generated_name, fallback_name)
        except Exception:
            logging.exception("生成会话名称失败，已回退为用户问题摘要")
            return fallback_name

    def generate_suggested_questions(self, histories: str) -> list[str]:
        """根据历史信息生成 建议问题（不超过3条）"""

        parser = PydanticOutputParser(pydantic_object=SuggestedQuestions)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    SUGGESTED_QUESTIONS_TEMPLATE + "\n{format_instructions}",
                ),
                ("human", "{histories}"),
            ]
        ).partial(format_instructions=parser.get_format_instructions())

        try:
            structured_llm = (
                self.language_model_manager.create_system_structured_chat_model(
                    SuggestedQuestions,
                    {"temperature": 0},
                    max_attempts=2,
                )
            )

            chain = prompt | structured_llm
            suggested_questions = chain.invoke({"histories": histories})
            questions = suggested_questions.questions

        except Exception:
            logging.exception("生成建议问题失败，已回退为空列表")
            return []

        return questions[:3]

    @staticmethod
    def _normalize_conversation_name(name: str, fallback: str) -> str:
        """清理模型标题，并确保始终返回可用的短标题。"""
        normalized_name = " ".join((name or "").split()).strip("`'\"")
        if not normalized_name:
            normalized_name = fallback
        if len(normalized_name) > 50:
            normalized_name = normalized_name[:50] + "..."
        return normalized_name

    def save_agent_thoughts(
        self,
        flask_app: Flask,
        account_id: UUID,
        app_id: UUID,
        app_config: dict[str, Any],
        conversation_id: UUID,
        message_id: UUID,
        agent_thoughts: list[AgentThought],
    ):
        """存储智能体 推理消息"""
        with flask_app.app_context():
            position = 0
            latency = 0

            # 在子线程重新查询 保证会话有效性
            conversation = self.get(Conversation, conversation_id)
            message = self.get(Message, message_id)

            # 兼容调用方传入尚未合并的文本片段和最终统计事件。
            merged = {}
            for item in agent_thoughts:
                merge_agent_thought(merged, item)
            agent_thoughts = list(merged.values())

            # 存储智能体推理过程
            for agent_thought in agent_thoughts:
                #  存储 记忆召回、推理、消息、动作、知识库检索 步骤
                if agent_thought.event in [
                    QueueEvent.AGENT_THOUGHT,
                    QueueEvent.AGENT_MESSAGE,
                    QueueEvent.AGENT_ACTION,
                    QueueEvent.DATASET_RETRIEVAL,
                ]:
                    # 更新位置及总耗时
                    position += 1
                    latency += agent_thought.latency
                    values = dict(
                        app_id=app_id,
                        conversation_id=conversation.id,
                        message_id=message.id,
                        invoke_from=message.invoke_from,
                        created_by=account_id,
                        position=position,
                        event=agent_thought.event,
                        thought=agent_thought.thought,
                        observation=agent_thought.observation,
                        tool=agent_thought.tool,
                        tool_input=agent_thought.tool_input,
                        message=agent_thought.message,
                        answer=agent_thought.answer,
                        latency=agent_thought.latency,
                        **thought_usage_fields(agent_thought),
                    )
                    existing = self.db.session.get(
                        MessageAgentThought, agent_thought.id
                    )
                    if existing is None:
                        self.create(MessageAgentThought, id=agent_thought.id, **values)
                    else:
                        self.update(existing, **values)

                # 时间是否为 agent_message
                if agent_thought.event == QueueEvent.AGENT_MESSAGE:
                    # 更新消息
                    self.update(
                        message,
                        message=agent_thought.message,
                        answer=agent_thought.answer,
                        latency=latency,
                    )

                    # 更新长期记忆
                    if app_config["long_term_memory"]["enable"]:
                        try:
                            new_summary = self.summary(
                                format_query_for_text_model(message.query, (message.image_urls or [])),
                                agent_thought.answer,
                                conversation.summary,
                            )
                            self.update(conversation, summary=new_summary)
                        except Exception:
                            logging.exception(
                                "更新会话摘要失败，继续保存消息和其他辅助信息"
                            )

                    # 生成会话名称
                    if conversation.is_new:
                        if not message.query.strip() and message.image_urls:
                            new_conversation_name = "图片对话"
                        else:
                            new_conversation_name = self.generate_conversation_name(message.query)
                        self.update(conversation, name=new_conversation_name)

                # 终止事件不是 AGENT_MESSAGE；放在消息分支外处理。
                if agent_thought.event in [
                    QueueEvent.STOP,
                    QueueEvent.ERROR,
                    QueueEvent.TIMEOUT,
                ]:
                    self.update(
                        message,
                        status=agent_thought.event.value,
                        error=(
                            agent_thought.observation
                            if agent_thought.event == QueueEvent.ERROR
                            else ""
                        ),
                    )

            self.update(message, **summary_fields(summarize_usage(agent_thoughts)))
