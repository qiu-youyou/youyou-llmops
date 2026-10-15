#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   ai_service
@Time   :   2026/2/25 09:47
@Author :   s.qiu@foxmail.com
"""

import json
from dataclasses import dataclass
from typing import Generator
from uuid import UUID

from injector import inject
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from internal.core.language_model.messages import format_query_for_text_model
from internal.core.language_model import LanguageModelManager
from internal.entity.ai_entity import OPTIMIZE_PROMPT_TEMPLATE
from internal.exception import ForbiddenException
from internal.model import Account, Message
from pkg.sqlalchemy import SQLAlchemy
from .base_service import BaseService
from .conversation_service import ConversationService


@inject
@dataclass
class AIService(BaseService):
    """AI辅助服务"""

    db: SQLAlchemy
    conversation_service: ConversationService
    language_model_manager: LanguageModelManager

    def optimize_prompt(self, prompt: str) -> Generator[str, None, None]:
        """根据传递的预设prompt进行优化"""
        prompt_template = ChatPromptTemplate.from_messages(
            [("system", OPTIMIZE_PROMPT_TEMPLATE), ("human", "{prompt}")]
        )
        llm = self.language_model_manager.create_default_language_model(
            {"temperature": 0.5}
        )
        chain = prompt_template | llm | StrOutputParser()

        # 调用流事件返回
        for chunk in chain.stream({"prompt": prompt}):
            data = {"optimize_prompt": chunk}
            yield f"event: optimize_prompt\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def generate_suggested_questions_from_message_id(
        self, message_id: UUID, account: Account
    ) -> list[str]:
        """根据传递的消息id+账号生成建议问题列表"""
        message = self.get(Message, message_id)
        if not message or message.created_by != account.id:
            raise ForbiddenException("消息不存在或无权限")
        # 构建对话 生成建议问题
        histories = f"Human: {format_query_for_text_model(message.query, (message.image_urls or []))}\nAI:{message.answer}"
        return self.conversation_service.generate_suggested_questions(histories)
