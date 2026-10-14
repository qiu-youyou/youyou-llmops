#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   openapi_schema
@Time   :   2026/2/27 11:47
@Author :   s.qiu@foxmail.com
"""
import uuid

from internal.schema.chat_schema import ChatReq
from wtforms import StringField, BooleanField
from wtforms.validators import DataRequired, UUID, Optional, ValidationError


class OpenAPIChatReq(ChatReq):
    """开发API对话结构 请求结构"""
    app_id = StringField('app_id', validators=[
        DataRequired(),
        UUID("app_id 格式必须为UUID")
    ])
    end_user_id = StringField('end_user_id', validators=[
        Optional(),
        UUID("end_user_id 格式必须为UUID")
    ])
    conversation_id = StringField("conversation_id", default="")
    stream = BooleanField("stream", default=True)

    def validate_conversation_id(self, field: StringField) -> None:
        """校验 conversation_id"""
        if field.data:
            try:
                uuid.UUID(field.data)
            except Exception as _:
                raise ValidationError("conversation_id 格式必须为UUID")
            if not self.end_user_id.data:
                raise ValidationError("传递 conversation_id 则 end_user_id 不能为空")
