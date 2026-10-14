from dataclasses import dataclass
from typing import Generator

from injector import inject
from sqlalchemy import desc
from werkzeug.exceptions import FailedDependency

from internal.core.language_model.messages import build_human_message
from internal.core.agent.agents.agent_queue_manager import AgentQueueManager
from internal.entity.app_entity import AppStatus
from internal.entity.conversation_entity import InvokeFrom, MessageStatus
from internal.exception.exception import FailException, ForbiddenException
from internal.core.language_model import LanguageModelManager
from internal.model.account import Account
from internal.model.app import App
from internal.model.conversation import Conversation, Message
from internal.schema.web_app_schema import WebAppChatReq
from .app_config_service import AppConfigService
from .conversation_service import ConversationService
from .retrieval_service import RetrievalService

from .chat_runtime import ChatRuntime, prepare_app_agent
from .base_service import BaseService
from pkg.sqlalchemy import SQLAlchemy


@inject
@dataclass
class WebAppService(BaseService):
    """Webapp 服务"""

    db: SQLAlchemy
    app_config_service: AppConfigService
    retrieval_service: RetrievalService
    conversation_service: ConversationService
    language_model_manager: LanguageModelManager

    def get_web_app(self, token: str) -> App:
        """根据TOKEN获取应用"""
        app = self.db.session.query(App).filter(App.token == token).one_or_none()
        if not app:
            raise FailException("该应用不存在")
        if app.status != AppStatus.PUBLISHED:
            raise FailedDependency("该应用未发布")

        return app

    def get_model_features(self, app: App) -> list[str]:
        """返回已发布模型的能力，前端和运行时使用同一个模型目录。"""
        config = self.app_config_service.get_app_config(app)
        model_config = config["model_config"]
        provider = self.language_model_manager.get_provider(model_config["provider"])
        model = provider.get_model_entity(model_config["model"])
        return model.features

    def web_app_chat(
        self, token: str, req: WebAppChatReq, account: Account
    ) -> Generator:
        """WEBAPP 会话"""
        app = self.get_web_app(token)

        # 校验会话归属信息
        conversation = None
        if req.conversation_id.data:
            conversation = self.get(Conversation, req.conversation_id.data)
            if (
                not conversation
                or conversation.app_id != app.id
                or conversation.invoke_from != InvokeFrom.WEB_APP
                or conversation.created_by != account.id
                or conversation.is_deleted is True
            ):
                raise ForbiddenException("该会话不存在或者不属于当前应用/用户/调用方式")

        # 获取当前应用的已发布运行配置
        app_config = self.app_config_service.get_app_config(app)

        agent, history = prepare_app_agent(
            db=self.db,
            language_model_manager=self.language_model_manager,
            app_config_service=self.app_config_service,
            retrieval_service=self.retrieval_service,
            config=app_config,
            conversation=conversation,
            resource_owner_id=app.account_id,
            operator_id=account.id,
            invoke_from=InvokeFrom.WEB_APP,
            req=req,
        )

        # 运行所需的配置、模型和工具已就绪后，再创建会话与待保存的消息。
        if conversation is None:
            conversation = self.create(
                Conversation,
                app_id=app.id,
                name="New Conversation",
                invoke_from=InvokeFrom.WEB_APP,
                created_by=account.id,
            )
        message = self.create(
            Message,
            app_id=app.id,
            conversation_id=conversation.id,
            invoke_from=InvokeFrom.WEB_APP,
            created_by=account.id,
            query=req.query.data,
            image_urls=req.image_urls.data,
            status=MessageStatus.NORMAL,
        )

        runtime = ChatRuntime(
            conversation_service=self.conversation_service,
            account_id=account.id,
            app_id=app.id,
            app_config=app_config,
            conversation_id=conversation.id,
            message_id=message.id,
        )
        return runtime.stream(
            agent,
            {
                "messages": [build_human_message(req.query.data, req.image_urls.data)],
                "history": history,
                "long_term_memory": conversation.summary,
            },
        )

    def stop_debug_chat(self, token: str, task_id: str, account: Account):
        """WEBAPP 关闭指定任务会话"""
        self.get_web_app(token)
        AgentQueueManager.set_stop_flag(task_id, InvokeFrom.WEB_APP, account.id)

    def get_conversations(self, token: str, is_pinned: bool, account: Account):
        """获取  WEBAPP 下的所有会话列表"""
        app = self.get_web_app(token)

        conversations = (
            self.db.session.query(Conversation)
            .filter(
                Conversation.app_id == app.id,
                Conversation.created_by == account.id,
                Conversation.invoke_from == InvokeFrom.WEB_APP,
                Conversation.is_pinned == is_pinned,
                ~Conversation.is_deleted,
            )
            .order_by(desc("created_at"))
            .all()
        )

        return conversations
