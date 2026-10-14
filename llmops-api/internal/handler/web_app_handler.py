from dataclasses import dataclass

from flask import request
from flask_login import current_user, login_required
from injector import inject

from internal.schema.web_app_schema import (
    GetConversationsReq,
    GetConversationsResp,
    GetWebAppResp,
    WebAppChatReq,
)
from internal.service import WebAppService
from pkg.response.response import (
    compact_generate_response,
    success_json,
    success_message,
    validate_error_json,
)


@inject
@dataclass
class WebAppHandler:
    """Webapp 处理器"""

    web_app_service: WebAppService

    @login_required
    def get_web_app(self, token: str):
        """根据 TOKEN 获取应用"""
        app = self.web_app_service.get_web_app(token)
        resp = GetWebAppResp()
        data = resp.dump(app)
        data["app_config"]["features"] = self.web_app_service.get_model_features(app)
        return success_json(data)

    @login_required
    def web_app_chat(self, token: str):
        """WEBAPP 会话"""
        req = WebAppChatReq()
        if not req.validate():
            return validate_error_json(req.errors)
        response = self.web_app_service.web_app_chat(
            token, req, current_user._get_current_object()
        )
        return compact_generate_response(response)

    @login_required
    def stop_web_app_chat(self, token: str, task_id: str):
        """WEBAPP 停止会话"""
        self.web_app_service.stop_debug_chat(token, task_id, current_user)
        return success_message("WEBAPP 停止会话成功")

    @login_required
    def get_conversations(self, token: str):
        """获取 WEBAPP 下的所有会话列表"""
        # 1.提取请求并校验
        req = GetConversationsReq(request.args)
        if not req.validate():
            return validate_error_json(req.errors)

        # 2.调用服务获取会话列表
        conversations = self.web_app_service.get_conversations(
            token, req.is_pinned.data, current_user
        )
        # 3.构建响应并返回
        resp = GetConversationsResp(many=True)

        return success_json(resp.dump(conversations))
