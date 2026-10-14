from dataclasses import dataclass
from flask import request

from flask_login import current_user, login_required
from injector import inject
from uuid import UUID
from internal.model.account import Account
from internal.schema.assistant_agent_schema import (
    AssistantAgentChat,
    GetAssistantAgentMessagesWithPageReq,
    GetAssistantAgentMessagesWithPageResp,
)
from pkg.paginator.paginator import PageModel
from pkg.response.response import (
    compact_generate_response,
    success_json,
    success_message,
    validate_error_json,
)
from internal.service import AssistantAgentService


@inject
@dataclass
class AssistantAgentHandler:
    """辅助AGNENT模块处理器"""

    assistant_agent_service: AssistantAgentService

    @login_required
    def capabilities(self):
        return success_json(self.assistant_agent_service.capabilities())

    @login_required
    def assistant_agent_chat(self):
        """辅助智能体对话"""
        req = AssistantAgentChat()
        if not req.validate():
            return validate_error_json(req.errors)

        response = self.assistant_agent_service.assistant_agent_chat(
            req, current_user.id
        )

        return compact_generate_response(response)

    @login_required
    def stop_assistant_agent_chat(self, task_id: UUID):
        """辅助智能体停止会话"""
        self.assistant_agent_service.stop_assistant_agent_chat(task_id, current_user)
        return success_message("停止会话成功")

    @login_required
    def get_assistant_agent_messages_with_page(self):
        """辅助智能体会话消息分页列表"""

        req = GetAssistantAgentMessagesWithPageReq(request.args)
        if not req.validate():
            return validate_error_json(req.errors)

        messages, paginator = (
            self.assistant_agent_service.get_assistant_agent_messages_with_page(
                req, current_user
            )
        )
        resp = GetAssistantAgentMessagesWithPageResp(many=True)

        return success_json(PageModel(list=resp.dump(messages), paginator=paginator))

    @login_required
    def delete_assistant_agent_conversation(self):
        """清空辅助智能体会话消息列表"""

        self.assistant_agent_service.delete_assistant_agent_conversation(current_user)
        return success_message("辅助智能体会话消息删除成功")
