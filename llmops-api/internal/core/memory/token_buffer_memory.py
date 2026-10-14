#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   token_buffer_memory
@Time   :   2026/2/5 11:24
@Author :   s.qiu@foxmail.com
"""
from dataclasses import dataclass

from langchain_core.messages import AnyMessage, AIMessage, get_buffer_string
from sqlalchemy import desc

from internal.core.language_model.entities.model_entity import BaseLanguageModel, ModelFeature
from internal.core.language_model.messages import (
    build_human_message,
    extract_message_text,
    format_query_for_text_model,
)
from internal.entity.conversation_entity import MessageStatus
from internal.model import Conversation, Message
from pkg.sqlalchemy import SQLAlchemy


TEXT_HISTORY_TOKEN_LIMIT = 2000
IMAGE_HISTORY_TOKEN_LIMIT = 8192
IMAGE_TOKEN_ESTIMATE = 1024  # 仅用于历史裁剪，实际用量与计费取 Provider 返回值。


def simple_token_counter(messages: list[AnyMessage]) -> int:
    total = 0
    for message in messages:
        total += (len(extract_message_text(message.content)) + 3) // 4
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    total += IMAGE_TOKEN_ESTIMATE
    return total


@dataclass
class TokenBufferMemory:
    """基于token计数的缓冲记忆组件"""
    db: SQLAlchemy  # 数据库实例
    conversation: Conversation  # 会话模型
    model_instance: BaseLanguageModel  # LLM模型

    def get_history_prompt_messages(
        self, max_token_limit: int | None = None, message_limit: int = 10
    ) -> list[AnyMessage]:
        """根据 token限制+消息数量 获取指定会话的历史消息列表"""
        if self.conversation is None:
            return []
        include_images = ModelFeature.IMAGE_INPUT in self.model_instance.features
        if max_token_limit is None:
            max_token_limit = IMAGE_HISTORY_TOKEN_LIMIT if include_images else TEXT_HISTORY_TOKEN_LIMIT
        # 查询会话消息列表 过滤状态为正常的数据 倒叙排序
        messages = self.db.session.query(Message).filter(
            Message.conversation_id == self.conversation.id,
            Message.answer != "",
            Message.is_deleted == False,
            Message.status.in_([MessageStatus.NORMAL, MessageStatus.STOP, MessageStatus.TIMEOUT])
        ).order_by(desc("created_at")).limit(message_limit).all()
        # 数据库按新到旧返回；每轮的提问和回答一起保留或裁剪。
        turns = []
        token_count = 0
        for message in messages:
            human_message = build_human_message(
                message.query, message.image_urls or [], include_images=include_images
            )
            turn = [human_message, AIMessage(message.answer)]
            turn_tokens = simple_token_counter(turn)
            if token_count + turn_tokens > max_token_limit:
                break
            turns.append(turn)
            token_count += turn_tokens

        return [message for turn in reversed(turns) for message in turn]

    def get_history_prompt_text(self, max_token_limit: int | None = None, message_limit: int = 10, human_prefix: str = "Human",
                                ai_prefix: str = "AI") -> str:
        """根据 token限制+消息数量 获取指定会话的历史消息文本 (短期记忆文本生成)"""
        messages = self.get_history_prompt_messages(max_token_limit=max_token_limit, message_limit=message_limit)
        for message in messages:
            if isinstance(message.content, list):
                images = [
                    block["image_url"]["url"] for block in message.content
                    if isinstance(block, dict) and block.get("type") == "image_url"
                ]
                message.content = format_query_for_text_model(extract_message_text(message.content), images)
        # 将消息列表转位文本
        return get_buffer_string(messages, human_prefix, ai_prefix)
