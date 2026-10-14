#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   app_schema
@Time   :   2025/9/5 14:19
@Author :   s.qiu@foxmail.com
"""

from flask_wtf import FlaskForm
from marshmallow import Schema, fields, pre_dump
from wtforms import StringField, IntegerField
from wtforms.validators import DataRequired, Length, URL, Optional, NumberRange

from internal.schema.chat_schema import ChatReq
from internal.entity.app_entity import AppStatus
from internal.lib.helper import datetime_to_timestamp
from internal.model import App, AppConfigVersion, Message
from pkg.paginator import PaginatorReq


class CreateAppReq(FlaskForm):
    """创建应用请求结构"""
    name = StringField("name", validators=[
        DataRequired("应用名称不能为空"), Length(max=30, message="应用名称最长为30个字符")])
    icon = StringField("icon", validators=[
        DataRequired("应用图标不能为空"), URL(message="图标格式必须是URL链接")
    ])
    description = StringField("description", validators=[
        Length(max=800, message="应用描述最长为800个字符")
    ])


class UpdateAppReq(FlaskForm):
    """修改应用请求结构"""
    name = StringField("name", validators=[
        DataRequired("应用名称不能为空"), Length(max=30, message="应用名称最长为30个字符")])
    icon = StringField("icon", validators=[
        DataRequired("应用图标不能为空"), URL(message="图标格式必须是URL链接")
    ])
    description = StringField("description", validators=[
        Length(max=800, message="应用描述最长为800个字符")
    ])


class GetAppResp(Schema):
    """获取应用基础信息响应结构"""
    id = fields.UUID(dump_default="")
    debug_conversation_id = fields.UUID(dump_default="")
    name = fields.String(dump_default="")
    icon = fields.String(dump_default="")
    description = fields.String(dump_default="")
    status = fields.String(dump_default="")
    draft_updated_at = fields.Integer(dump_default=0)
    updated_at = fields.Integer(dump_default=0)
    created_at = fields.Integer(dump_default=0)

    @pre_dump
    def process_data(self, data: App, **kwargs):
        return {
            "id": data.id,
            "debug_conversation_id": data.debug_conversation_id if data.debug_conversation_id else "",
            "name": data.name,
            "icon": data.icon,
            "description": data.description,
            "status": data.status,
            "draft_updated_at": datetime_to_timestamp(data.draft_app_config.updated_at),
            "updated_at": datetime_to_timestamp(data.updated_at),
            "created_at": datetime_to_timestamp(data.created_at),
        }


class GetAppsWithPageReq(PaginatorReq):
    """获取应用分页列表 请求结构"""
    search_word = StringField("search_word", default="", validators=[Optional()])


class GetAppsWithPageResp(Schema):
    """获取应用分页列表 响应结构"""
    id = fields.UUID(dump_default="")
    name = fields.String(dump_default="")
    icon = fields.String(dump_default="")
    description = fields.String(dump_default="")
    preset_prompt = fields.String(dump_default="")
    model_config = fields.Dict(dump_default={})
    status = fields.String(dump_default="")
    updated_at = fields.Integer(dump_default=0)
    created_at = fields.Integer(dump_default=0)

    @pre_dump
    def process_data(self, data: App, **kwargs):
        app_config = data.app_config if data.status == AppStatus.PUBLISHED else data.draft_app_config
        return {
            "id": data.id,
            "name": data.name,
            "icon": data.icon,
            "description": data.description,
            "preset_prompt": app_config.preset_prompt,
            "model_config": {
                "provider": app_config.model_config.get("provider", ""),
                "model": app_config.model_config.get("model", "")
            },
            "status": data.status,
            "updated_at": datetime_to_timestamp(data.updated_at),
            "created_at": datetime_to_timestamp(data.created_at),
        }


class GetPublishHistoriesWithPageReq(PaginatorReq):
    """获取发布历史配置信息分页"""


class GetPublishHistoriesWithPageResp(Schema):
    id = fields.UUID(dump_default="")
    version = fields.Integer(dump_default=0)
    created_at = fields.Integer(dump_default=0)

    @pre_dump
    def process_data(self, data: AppConfigVersion, **kwargs):
        return {
            "id": data.id,
            "version": data.version,
            "created_at": datetime_to_timestamp(data.created_at),
        }


class FallbackHistoryToDraftReq(FlaskForm):
    """回退历史版本到当前草稿请求"""
    app_config_version_id = StringField("app_config_version_id", validators=[
        DataRequired("回退版本id不能为空")
    ])


class UpdateDebugConversationSummaryReq(FlaskForm):
    """更新应用会话长期记忆请求体"""
    summary = StringField("summary", default="")


class DebugChatReq(ChatReq):
    """应用调试会话请求结构体"""


class CompletionReq(FlaskForm):
    """基础聊天接口请求验证"""
    # 必填、长度最大为2000
    query = StringField("query", validators=[
        DataRequired(message="用户的提问是必填"),
        Length(max=2000, message="用户的提问最大长度是2000"),
    ])


class GetDebugConversationMessagesWithPageReq(PaginatorReq):
    """会话消息列表分页请求结构体"""
    created_at = IntegerField("created_at", default=0, validators=[
        Optional(),
        NumberRange(min=0, message="created_at游标最小值为0")
    ])


class GetDebugConversationMessagesWithPageResp(Schema):
    """会话消息列表分页响应结构体"""
    id = fields.UUID(dump_default="")
    conversation_id = fields.UUID(dump_default="")
    image_urls = fields.List(fields.String(), dump_default=[])
    query = fields.String(dump_default="")
    answer = fields.String(dump_default="")
    total_token_count = fields.Integer(dump_default=0)
    message_token_count = fields.Integer(dump_default=0)
    answer_token_count = fields.Integer(dump_default=0)
    total_price = fields.String(allow_none=True)
    usage = fields.Dict(dump_default={})
    latency = fields.Float(dump_default=0)
    agent_thoughts = fields.List(fields.Dict, dump_default=[])
    created_at = fields.Integer(dump_default=0)

    @pre_dump
    def process_data(self, data: Message, **kwargs):
        return {
            "id": data.id,
            "conversation_id": data.conversation_id,
            "query": data.query,
            "image_urls": data.image_urls or [],
            "answer": data.answer,
            "total_token_count": data.total_token_count,
            "message_token_count": data.message_token_count,
            "answer_token_count": data.answer_token_count,
            "total_price": (data.usage or {}).get("total_price"),
            "usage": data.usage or {},
            "latency": data.latency,
            "agent_thoughts": [{
                "id": agent_thought.id,
                "position": agent_thought.position,
                "event": agent_thought.event,
                "thought": agent_thought.thought,
                "observation": agent_thought.observation,
                "tool": agent_thought.tool,
                "tool_input": agent_thought.tool_input,
                "usage": agent_thought.usage or {},
                "latency": agent_thought.latency,
                "created_at": datetime_to_timestamp(agent_thought.created_at),
            } for agent_thought in data.agent_thoughts],
            "created_at": datetime_to_timestamp(data.created_at),
        }
