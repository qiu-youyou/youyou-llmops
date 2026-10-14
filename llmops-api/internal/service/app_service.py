#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   app_service
@Time   :   2025/9/17 16:21
@Author :   s.qiu@foxmail.com
"""

from copy import deepcopy
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator
from uuid import UUID
from injector import inject
from langchain_classic.prompts import ChatPromptTemplate
from langchain_classic.schema import StrOutputParser
from langchain_core.runnables import RunnableParallel
from redis import Redis
from sqlalchemy import Uuid, func, desc

from internal.core.language_model.messages import build_human_message
from internal.core.agent.agents import AgentQueueManager
from internal.core.language_model.entities.model_entity import ModelParameterType
from internal.core.language_model import LanguageModelManager
from internal.core.tools.api_tools.providers import ApiProviderManager
from internal.core.tools.builtin_tools.providers import BuiltinProviderManager
from langchain_community.utilities.dalle_image_generator import DallEAPIWrapper
from internal.entity.ai_entity import OPTIMIZE_PROMPT_TEMPLATE
from internal.entity.app_entity import (
    GENERATE_ICON_PROMPT_TEMPLATE,
    AppStatus,
    AppConfigType,
    DEFAULT_APP_CONFIG,
)
from internal.entity.conversation_entity import InvokeFrom, MessageStatus
from internal.entity.workflow_entity import WorkflowStatus
from internal.exception import (
    NotFoundException,
    ForbiddenException,
    ValidateErrorException,
    FailException,
)
from internal.lib.helper import generate_random_string, get_value_type
from internal.model import (
    App,
    Account,
    AppConfigVersion,
    ApiTool,
    Dataset,
    AppConfig,
    AppDatasetJoin,
    Message,
)
from internal.model.workflow import Workflow
from internal.schema.app_schema import (
    DebugChatReq,
    CreateAppReq,
    GetPublishHistoriesWithPageReq,
    GetDebugConversationMessagesWithPageReq,
    GetAppsWithPageReq,
)
from pkg.paginator import Paginator
from pkg.sqlalchemy import SQLAlchemy
from .app_config_service import AppConfigService
from .chat_runtime import ChatRuntime, prepare_app_agent
from .base_service import BaseService
from .conversation_service import ConversationService
from .retrieval_service import RetrievalService
from .cos_service import CosService


@inject
@dataclass
class AppService(BaseService):
    """应用 服务"""

    db: SQLAlchemy
    redis_client: Redis
    cos_service: CosService
    app_config_service: AppConfigService
    retrieval_service: RetrievalService
    conversation_service: ConversationService
    builtin_provider_manager: BuiltinProviderManager
    api_provider_manager: ApiProviderManager
    language_model_manager: LanguageModelManager

    def create_app(self, req: CreateAppReq, account: Account) -> App:
        """个人空间新增应用"""
        with self.db.auto_commit():
            app = App(
                account_id=account.id,
                name=req.name.data,
                icon=req.icon.data,
                description=req.description.data,
                status=AppStatus.DRAFT,
            )
            # 创建APP并刷新数据
            self.db.session.add(app)
            self.db.session.flush()

            app_config_version = AppConfigVersion(
                version=0,
                app_id=app.id,
                config_type=AppConfigType.DRAFT,
                **DEFAULT_APP_CONFIG,
            )
            # 添加草稿记录
            self.db.session.add(app_config_version)
            self.db.session.flush()
            # 为APP关联草稿配置ID
            app.draft_app_config_id = app_config_version.id
        return app

    def auto_create_app(self, name: str, description: str, account_id: UUID) -> None:
        """利用AI自动创建一个AGENT"""
        #  系统默认LLM辅助模型
        llm = self.language_model_manager.create_default_language_model(
            {"temperature": 0.8}
        )

        # 构建生成 icon 链
        dalle_api_wrapper = DallEAPIWrapper(model="dall-e-3", size="1024x1024")

        generate_icon_chain = (
            ChatPromptTemplate.from_template(GENERATE_ICON_PROMPT_TEMPLATE)
            | llm
            | StrOutputParser()
            | dalle_api_wrapper.run
        )

        # 构建生成 Prompt 链
        generate_preset_prompt_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", OPTIMIZE_PROMPT_TEMPLATE),
                    ("human", "应用名称: {name}\n\n应用描述: {description}"),
                ]
            )
            | llm
            | StrOutputParser()
        )

        # 构建并行链
        generate_app_config_chain = RunnableParallel(
            {
                # "icon": generate_icon_chain,
                "preset_prompt": generate_preset_prompt_chain,
            }
        )

        app_config = generate_app_config_chain.invoke(
            {"name": name, "description": description}
        )

        # 将生成图片上传到 TecentCOS 中
        # icon_resp = requests.get(app_config.get("icon"))
        # if icon_resp.status_code == 200:
        #     icon_content = icon_resp.content
        # else:
        #     raise FailException("生成应用图标出错")

        account = self.get(Account, account_id)
        # upload_file = self.cos_service.upload_file(
        #     FileStorage(io.BytesIo(icon_content), filename="icon.png"), True, account
        # )
        # icon = self.cos_service.get_file_url(upload_file.key)

        # 创建应用并写入草稿配置
        with self.db.auto_commit():
            app = App(
                account_id=account.id,
                name=name,
                icon="https://notes.qiuyouyou.cn/static/icon.png",
                description=description,
            )
            self.db.session.add(app)
            self.db.session.flush()

            app_config_version = AppConfigVersion(
                app_id=app.id,
                config_type=AppConfigType.DRAFT,
                version=0,
                **{
                    **DEFAULT_APP_CONFIG,
                    "preset_prompt": app_config.get("preset_prompt", ""),
                },
            )
            self.db.session.add(app_config_version)
            self.db.session.flush()

            app.draft_app_config_id = app_config_version.id

    def get_app(self, app_id: UUID, account: Account) -> App:
        """获取应用基础信息"""
        app = self.get(App, app_id)
        if not app:
            raise NotFoundException("该应用不存在")
        if app.account_id != account.id:
            raise ForbiddenException("当前账号无权限！")
        return app

    def delete_app(self, app_id: uuid.UUID, account: Account) -> App:
        """删除指定应用"""
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            for model in (AppDatasetJoin, AppConfigVersion, AppConfig):
                self.db.session.query(model).filter_by(app_id=app.id).delete()
            self.db.session.delete(app)
        return app

    def update_app(self, app_id: uuid.UUID, account: Account, **kwargs) -> App:
        """更新指定应用信息"""
        app = self.get_app(app_id, account)
        self.update(app, **kwargs)
        return app

    def get_apps_with_page(
        self, req: GetAppsWithPageReq, account: Account
    ) -> tuple[list[App], Paginator]:
        """获取应用分页列表"""
        paginate = Paginator(self.db, req)
        filters = [App.account_id == account.id]
        if req.search_word.data:
            filters.append(App.name.ilike(f"%{req.search_word.data}%"))

        apps = paginate.paginate(
            self.db.session.query(App).filter(*filters).order_by(desc("created_at"))
        )
        return apps, paginate

    def _get_app_for_update(self, app_id: UUID, account: Account) -> App:
        """生命周期写入先锁住应用行，再读取草稿和版本号。"""
        app = (
            self.db.session.query(App)
            .filter(App.id == app_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if app is None:
            raise NotFoundException("该应用不存在")
        if app.account_id != account.id:
            raise ForbiddenException("当前账号无权限！")
        return app

    def _get_draft_record(self, app: App) -> AppConfigVersion:
        draft = (
            self.db.session.query(AppConfigVersion)
            .filter_by(app_id=app.id, config_type=AppConfigType.DRAFT)
            .populate_existing()
            .one_or_none()
        )
        if draft is None:
            draft = AppConfigVersion(
                app_id=app.id,
                version=0,
                config_type=AppConfigType.DRAFT,
                **deepcopy(DEFAULT_APP_CONFIG),
            )
            self.db.session.add(draft)
            self.db.session.flush()
        app.draft_app_config_id = draft.id
        return draft

    @staticmethod
    def _config_values(config: AppConfigVersion) -> dict[str, Any]:
        return deepcopy({key: getattr(config, key) for key in DEFAULT_APP_CONFIG})

    def copy_app(self, app_id: UUID, account: Account) -> App:
        """只复制应用信息及独立草稿，不复制发布凭证和会话。"""
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            values = self._config_values(self._get_draft_record(app))
            new_app = App(
                account_id=account.id,
                name=app.name,
                icon=app.icon,
                description=app.description,
                status=AppStatus.DRAFT,
            )
            self.db.session.add(new_app)
            self.db.session.flush()
            draft = AppConfigVersion(
                app_id=new_app.id, version=0, config_type=AppConfigType.DRAFT, **values
            )
            self.db.session.add(draft)
            self.db.session.flush()
            new_app.draft_app_config_id = draft.id
        return new_app

    def get_draft_app_config(self, app_id: UUID, account: Account) -> dict[str, Any]:
        app = self.get_app(app_id, account)
        return self.app_config_service.get_draft_app_config(app)

    def update_draft_app_config(
        self, app_id: UUID, draft_app_config: dict[str, Any], account: Account
    ) -> AppConfigVersion:
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            values = self._validate_draft_app_config(
                deepcopy(draft_app_config), account
            )
            draft = self._get_draft_record(app)
            for key, value in values.items():
                setattr(draft, key, value)
            draft.updated_at = datetime.now()
        return draft

    def publish_draft_app_config(self, app_id: UUID, account: Account):
        """运行配置、知识库关联、发布版本及应用指针一起提交。"""
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            draft = self._get_draft_record(app)
            values = self._validate_draft_app_config(
                self._config_values(draft), account
            )
            runtime = AppConfig(
                app_id=app.id,
                **deepcopy(
                    {key: value for key, value in values.items() if key != "datasets"}
                ),
            )
            self.db.session.add(runtime)
            self.db.session.flush()
            self.db.session.query(AppDatasetJoin).filter_by(app_id=app.id).delete()
            self.db.session.add_all(
                [
                    AppDatasetJoin(app_id=app.id, dataset_id=dataset_id)
                    for dataset_id in values["datasets"]
                ]
            )
            max_version = (
                self.db.session.query(
                    func.coalesce(func.max(AppConfigVersion.version), 0)
                )
                .filter_by(app_id=app.id, config_type=AppConfigType.PUBLISHED)
                .scalar()
            )
            self.db.session.add(
                AppConfigVersion(
                    app_id=app.id,
                    version=max_version + 1,
                    config_type=AppConfigType.PUBLISHED,
                    **deepcopy(values),
                )
            )
            for key, value in values.items():
                setattr(draft, key, deepcopy(value))
            app.app_config_id = runtime.id
            app.status = AppStatus.PUBLISHED
        return app

    def cancel_publish_app_config(self, app_id: UUID, account: Account):
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            if app.status == AppStatus.DRAFT:
                raise FailException("该应用未发布")
            app.status = AppStatus.DRAFT
            app.app_config_id = None
            app.token = None
            self.db.session.query(AppDatasetJoin).filter_by(app_id=app.id).delete()
        return app

    def fallback_history_to_draft(
        self, app_id: UUID, app_config_version_id: UUID, account: Account
    ):
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            history = (
                self.db.session.query(AppConfigVersion)
                .filter_by(
                    id=app_config_version_id,
                    app_id=app.id,
                    config_type=AppConfigType.PUBLISHED,
                )
                .one_or_none()
            )
            if history is None:
                raise NotFoundException("该历史版本不存在")
            values = self._validate_draft_app_config(
                self._config_values(history), account
            )
            draft = self._get_draft_record(app)
            for key, value in values.items():
                setattr(draft, key, value)
            draft.updated_at = datetime.now()
        return draft

    def get_publish_histories_with_page(
        self, app_id: UUID, req: GetPublishHistoriesWithPageReq, account: Account
    ):
        """获取应用的发布历史 配置信息 列表"""
        self.get_app(app_id, account)
        paginator = Paginator(db=self.db, req=req)
        filters = [
            AppConfigVersion.app_id == app_id,
            AppConfigVersion.config_type == AppStatus.PUBLISHED,
        ]
        app_config_versions = paginator.paginate(
            self.db.session.query(AppConfigVersion)
            .filter(*filters)
            .order_by(desc("version"))
        )

        return app_config_versions, paginator

    def get_debug_conversation_summary(self, app_id: UUID, account: Account):
        """获取应用会话调试的长期记忆"""
        app = self.get_app(app_id, account)
        draft_app_config = self.get_draft_app_config(app_id, account)

        # 应用配置中是否启用长期记忆
        if draft_app_config["long_term_memory"]["enable"] is False:
            raise FailException("该应用未开启长期记忆功能")
        return app.debug_conversation.summary

    def update_debug_conversation_summary(
        self, app_id: UUID, summary: str, account: Account
    ):
        """更新应用会话调试的长期记忆"""
        app = self.get_app(app_id, account)
        # 获取并校验草稿配置信息
        draft_app_config = self.get_draft_app_config(app_id, account)
        # 应用配置中是否启用长期记忆
        if draft_app_config["long_term_memory"]["enable"] is False:
            raise FailException("该应用未开启长期记忆功能")
        # 更新长期记忆
        debug_conversation = app.debug_conversation
        self.update(debug_conversation, summary=summary)
        return debug_conversation

    def delete_debug_conversation(self, app_id: UUID, account: Account):
        """删除应用会话调试记录"""
        app = self.get_app(app_id, account)
        if not app.debug_conversation_id:
            return app
        # 删除与会话的关联
        app = self.update(app, debug_conversation_id=None)
        return app

    def get_debug_conversation_messages_with_page(
        self,
        app_id: UUID,
        req: GetDebugConversationMessagesWithPageReq,
        account: Account,
    ):
        """获取调试会话消息列表"""
        app = self.get_app(app_id, account)
        debug_conversation = app.debug_conversation

        paginator = Paginator(db=self.db, req=req)
        filters = [
            Message.conversation_id == debug_conversation.id,
            Message.status.in_([MessageStatus.NORMAL, MessageStatus.STOP]),
            Message.answer != "",
        ]
        if req.created_at.data:
            # 时间戳转换为DateTime
            created_at_datetime = datetime.fromtimestamp(req.created_at.data)
            filters.append(Message.created_at <= created_at_datetime)

        messages = paginator.paginate(
            self.db.session.query(Message).filter(*filters).order_by(desc("created_at"))
        )
        return messages, paginator

    def stop_debug_chat(self, app_id: UUID, task_id: UUID, account: Account) -> None:
        """关闭应用指定任务的调试会话"""
        self.get_app(app_id, account)
        AgentQueueManager.set_stop_flag(task_id, InvokeFrom.DEBUGGER, account.id)

    def debug_chat(self, app_id: UUID, req: DebugChatReq, account: Account) -> Generator:
        """智能体 会话调试"""

        # 获取应用信息
        app = self.get_app(app_id, account)
        # 获取当前应用 最新草稿配置
        draft_app_config = self.get_draft_app_config(app_id, account)
        # 获取当前应用 会话信息
        debug_conversation = app.debug_conversation

        # 运行准备成功后再创建消息。
        agent, history = prepare_app_agent(
            db=self.db,
            language_model_manager=self.language_model_manager,
            app_config_service=self.app_config_service,
            retrieval_service=self.retrieval_service,
            config=draft_app_config,
            conversation=debug_conversation,
            resource_owner_id=app.account_id,
            operator_id=account.id,
            invoke_from=InvokeFrom.DEBUGGER,
            req=req,
        )

        message = self.create(
            Message,
            app_id=app.id,
            conversation_id=debug_conversation.id,
            invoke_from=InvokeFrom.DEBUGGER,
            created_by=account.id,
            query=req.query.data,
            image_urls=req.image_urls.data,
            status=MessageStatus.NORMAL,
        )

        runtime = ChatRuntime(
            conversation_service=self.conversation_service,
            account_id=account.id,
            app_id=app.id,
            app_config=draft_app_config,
            conversation_id=debug_conversation.id,
            message_id=message.id,
        )
        return runtime.stream(
            agent,
            {
                "messages": [build_human_message(req.query.data, req.image_urls.data)],
                "history": history,
                "long_term_memory": debug_conversation.summary,
            },
        )

    def get_published_config(self, app_id: UUID, account: Account) -> dict[str, Any]:
        """获取应用发布配置信息"""
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            if app.status != AppStatus.PUBLISHED:
                app.token = None
            elif not app.token:
                app.token = generate_random_string(16)
        return {
            "web_app": {
                "token": app.token_with_default,
                "status": app.status,
            }
        }

    def regenerate_web_app_token(self, app_id: UUID, account: Account) -> str:
        """重新生成 webapp token 凭证信息/重新生成"""
        with self.db.auto_commit():
            app = self._get_app_for_update(app_id, account)
            if app.status != AppStatus.PUBLISHED:
                raise FailException("应用未发布无法生成凭证")
            token = generate_random_string(16)
            app.token = token
        return token

    def _validate_draft_app_config(
        self, draft_app_config: dict[str, Any], account: Account
    ) -> dict[str, Any]:
        """校验传递的应用草稿配置信息，返回校验后的数据"""
        # 1.校验上传的草稿配置中对应的字段，至少拥有一个可以更新的配置
        acceptable_fields = [
            "model_config",
            "dialog_round",
            "preset_prompt",
            "tools",
            "workflows",
            "datasets",
            "retrieval_config",
            "long_term_memory",
            "opening_statement",
            "opening_questions",
            "speech_to_text",
            "text_to_speech",
            "suggested_after_answer",
            "review_config",
        ]

        # 2.判断传递的草稿配置是否在可接受字段内
        if (
            not draft_app_config
            or not isinstance(draft_app_config, dict)
            or set(draft_app_config.keys()) - set(acceptable_fields)
        ):
            raise ValidateErrorException("草稿配置字段出错，请核实后重试")

        # 3.校验model_config字段，provider/model使用严格校验(出错的时候直接抛出)，parameters使用宽松校验，出错时使用默认值
        if "model_config" in draft_app_config:
            # 3.1 获取模型配置并判断数据是否为字典
            model_config = draft_app_config["model_config"]
            if not isinstance(model_config, dict):
                raise ValidateErrorException("模型配置格式错误，请核实后重试")

            # 3.2 判断model_config键信息是否正确
            if set(model_config.keys()) != {"provider", "model", "parameters"}:
                raise ValidateErrorException("模型键配置格式错误，请核实后重试")
            if not isinstance(model_config["parameters"], dict):
                raise ValidateErrorException("模型参数必须是字典")

            # 3.3 判断模型提供者信息是否正确
            if not model_config["provider"] or not isinstance(
                model_config["provider"], str
            ):
                raise ValidateErrorException("模型服务提供商类型必须为字符串")
            provider = self.language_model_manager.get_provider(
                model_config["provider"]
            )
            if not provider:
                raise ValidateErrorException("该模型服务提供商不存在，请核实后重试")

            # 3.4 判断模型信息是否正确
            if not model_config["model"] or not isinstance(model_config["model"], str):
                raise ValidateErrorException("模型名字必须是否字符串")
            model_entity = provider.get_model_entity(model_config["model"])
            if not model_entity:
                raise ValidateErrorException("该服务提供商下不存在该模型，请核实后重试")

            # 3.5 判断传递的parameters是否正确，如果不正确则设置默认值，并剔除多余字段，补全未传递的字段
            parameters = {}
            for parameter in model_entity.parameters:
                # 3.6 从model_config中获取参数值，如果不存在则设置为默认值
                parameter_value = model_config["parameters"].get(
                    parameter.name, parameter.default
                )

                # 3.7 判断参数是否必填
                if parameter.required:
                    # 3.8 参数必填，则值不允许为None，如果为None则设置默认值
                    if parameter_value is None:
                        parameter_value = parameter.default
                    else:
                        # 3.9 值非空则校验数据类型是否正确，不正确则设置默认值
                        if get_value_type(parameter_value) != parameter.type.value:
                            parameter_value = parameter.default
                else:
                    # 3.10 参数非必填，数据非空的情况下需要校验
                    if parameter_value is not None:
                        if get_value_type(parameter_value) != parameter.type.value:
                            parameter_value = parameter.default

                # 3.11 判断参数是否存在options，如果存在则数值必须在options中选择
                if parameter.options and parameter_value not in parameter.options:
                    parameter_value = parameter.default

                # 3.12 参数类型为int/float，如果存在min/max时候需要校验
                if (
                    parameter.type in [ModelParameterType.INT, ModelParameterType.FLOAT]
                    and parameter_value is not None
                ):
                    # 3.13 校验数值的min/max
                    if (parameter.min and parameter_value < parameter.min) or (
                        parameter.max and parameter_value > parameter.max
                    ):
                        parameter_value = parameter.default

                parameters[parameter.name] = parameter_value

            # 3.13 覆盖Agent配置中的模型配置
            model_config["parameters"] = parameters
            draft_app_config["model_config"] = model_config

        # 4.校验dialog_round上下文轮数，校验数据类型以及范围
        if "dialog_round" in draft_app_config:
            dialog_round = draft_app_config["dialog_round"]
            if type(dialog_round) is not int or not (0 <= dialog_round <= 100):
                raise ValidateErrorException("携带上下文轮数范围为0-100")

        # 5.校验preset_prompt
        if "preset_prompt" in draft_app_config:
            preset_prompt = draft_app_config["preset_prompt"]
            if not isinstance(preset_prompt, str) or len(preset_prompt) > 2000:
                raise ValidateErrorException(
                    "人设与回复逻辑必须是字符串，长度在0-2000个字符"
                )

        # 6.校验tools工具
        if "tools" in draft_app_config:
            tools = draft_app_config["tools"]
            validate_tools = []

            # 6.1 tools类型必须为列表，空列表则代表不绑定任何工具
            if not isinstance(tools, list):
                raise ValidateErrorException("工具列表必须是列表型数据")
            # 6.2 tools的长度不能超过5
            if len(tools) > 5:
                raise ValidateErrorException("Agent绑定的工具数不能超过5")
            # 6.3 循环校验工具里的每一个参数
            for tool in tools:
                # 6.4 校验tool非空并且类型为字典
                if not tool or not isinstance(tool, dict):
                    raise ValidateErrorException("绑定插件工具参数出错")
                # 6.5 校验工具的参数是不是type、provider_id、tool_id、params
                if set(tool.keys()) != {"type", "provider_id", "tool_id", "params"}:
                    raise ValidateErrorException("绑定插件工具参数出错")
                # 6.6 校验type类型是否为builtin_tool以及api_tool
                if tool["type"] not in ["builtin_tool", "api_tool"]:
                    raise ValidateErrorException("绑定插件工具参数出错")
                # 6.7 校验provider_id和tool_id
                if (
                    not tool["provider_id"]
                    or not tool["tool_id"]
                    or not isinstance(tool["provider_id"], str)
                    or not isinstance(tool["tool_id"], str)
                ):
                    raise ValidateErrorException("插件提供者或者插件标识参数出错")
                # 6.8 校验params参数，类型为字典
                if not isinstance(tool["params"], dict):
                    raise ValidateErrorException("插件自定义参数格式错误")
                # 6.9 校验对应的工具是否存在，而且需要划分成builtin_tool和api_tool
                if tool["type"] == "builtin_tool":
                    builtin_tool = self.builtin_provider_manager.get_tool(
                        tool["provider_id"], tool["tool_id"]
                    )
                    if not builtin_tool:
                        continue
                else:
                    try:
                        UUID(tool["provider_id"])
                    except (ValueError, TypeError, AttributeError):
                        raise ValidateErrorException(
                            "插件提供者标识必须是UUID"
                        ) from None
                    api_tool = (
                        self.db.session.query(ApiTool)
                        .filter(
                            ApiTool.provider_id == tool["provider_id"],
                            ApiTool.name == tool["tool_id"],
                            ApiTool.account_id == account.id,
                        )
                        .one_or_none()
                    )
                    if not api_tool:
                        continue

                validate_tools.append(tool)

            # 6.10 校验绑定的工具是否重复
            check_tools = [
                f"{tool['provider_id']}_{tool['tool_id']}" for tool in validate_tools
            ]
            if len(set(check_tools)) != len(validate_tools):
                raise ValidateErrorException("绑定插件存在重复")

            # 6.11 重新赋值工具
            draft_app_config["tools"] = validate_tools

        if "workflows" in draft_app_config:
            workflows = draft_app_config["workflows"]
            # 7.1 关联的工作流不能超过5个
            if not isinstance(workflows, list):
                raise ValidateErrorException("工作流列表格式错误")
            if len(workflows) > 5:
                raise ValidateErrorException("应用下最多可以绑定5个工作流")
            for workflow_id in workflows:
                try:
                    UUID(workflow_id)
                except (ValueError, TypeError, AttributeError):
                    raise ValidateErrorException("工作流标识必须是UUID") from None
            # 7.2 判断是否有重复工作流
            if len(set(workflows)) != len(workflows):
                raise ValidateErrorException("绑定工作流存在重复")
            # 7.3 获取当前账号限工作流
            workflow_records = (
                self.db.session.query(Workflow)
                .filter(
                    Workflow.id.in_(workflows),
                    Workflow.account_id == account.id,
                    Workflow.status == WorkflowStatus.PUBLISHED,
                )
                .all()
            )
            workflow_sets = set(
                [str(workflow_record.id) for workflow_record in workflow_records]
            )
            draft_app_config["workflows"] = [
                workflow_id for workflow_id in workflows if workflow_id in workflow_sets
            ]

        # 8.校验datasets知识库列表
        if "datasets" in draft_app_config:
            datasets = draft_app_config["datasets"]

            # 8.1 判断datasets类型是否为列表
            if not isinstance(datasets, list):
                raise ValidateErrorException("绑定知识库列表参数格式错误")
            # 8.2 判断关联的知识库列表是否超过5个
            if len(datasets) > 5:
                raise ValidateErrorException("Agent绑定的知识库数量不能超过5个")
            # 8.3 循环校验知识库的每个参数
            for dataset_id in datasets:
                try:
                    UUID(dataset_id)
                except Exception as e:
                    raise ValidateErrorException("知识库列表参数必须是UUID")
            # 8.4 判断是否传递了重复的知识库
            if len(set(datasets)) != len(datasets):
                raise ValidateErrorException("绑定知识库存在重复")
            # 8.5 校验绑定的知识库权限，剔除不属于当前账号的知识库
            dataset_records = (
                self.db.session.query(Dataset)
                .filter(
                    Dataset.id.in_(datasets),
                    Dataset.account_id == account.id,
                )
                .all()
            )
            dataset_sets = set(
                [str(dataset_record.id) for dataset_record in dataset_records]
            )
            draft_app_config["datasets"] = [
                dataset_id for dataset_id in datasets if dataset_id in dataset_sets
            ]

        # 9.校验retrieval_config检索配置
        if "retrieval_config" in draft_app_config:
            retrieval_config = draft_app_config["retrieval_config"]

            # 9.1 判断检索配置非空且类型为字典
            if not retrieval_config or not isinstance(retrieval_config, dict):
                raise ValidateErrorException("检索配置格式错误")
            # 9.2 校验检索配置的字段类型
            if set(retrieval_config.keys()) != {"retrieval_strategy", "k", "score"}:
                raise ValidateErrorException("检索配置格式错误")
            # 9.3 校验检索策略是否正确
            if retrieval_config["retrieval_strategy"] not in [
                "semantic",
                "full_text",
                "hybrid",
            ]:
                raise ValidateErrorException("检测策略格式错误")
            # 9.4 校验最大召回数量
            if not isinstance(retrieval_config["k"], int) or not (
                0 <= retrieval_config["k"] <= 10
            ):
                raise ValidateErrorException("最大召回数量范围为0-10")
            # 9.5 校验得分/最小匹配度
            if not isinstance(retrieval_config["score"], float) or not (
                0 <= retrieval_config["score"] <= 1
            ):
                raise ValidateErrorException("最小匹配范围为0-1")

        # 10.校验long_term_memory长期记忆配置
        if "long_term_memory" in draft_app_config:
            long_term_memory = draft_app_config["long_term_memory"]

            # 10.1 校验长期记忆格式
            if not long_term_memory or not isinstance(long_term_memory, dict):
                raise ValidateErrorException("长期记忆设置格式错误")
            # 10.2 校验长期记忆属性
            if set(long_term_memory.keys()) != {"enable"} or not isinstance(
                long_term_memory["enable"], bool
            ):
                raise ValidateErrorException("长期记忆设置格式错误")

        # 11.校验opening_statement对话开场白
        if "opening_statement" in draft_app_config:
            opening_statement = draft_app_config["opening_statement"]

            # 11.1 校验对话开场白类型以及长度
            if not isinstance(opening_statement, str) or len(opening_statement) > 2000:
                raise ValidateErrorException("对话开场白的长度范围是0-2000")

        # 12.校验opening_questions开场建议问题列表
        if "opening_questions" in draft_app_config:
            opening_questions = draft_app_config["opening_questions"]

            # 12.1 校验是否为列表，并且长度不超过3
            if not isinstance(opening_questions, list) or len(opening_questions) > 3:
                raise ValidateErrorException("开场建议问题不能超过3个")
            # 12.2 开场建议问题每个元素都是一个字符串
            for opening_question in opening_questions:
                if not isinstance(opening_question, str):
                    raise ValidateErrorException("开场建议问题必须是字符串")

        # 13.校验speech_to_text语音转文本
        if "speech_to_text" in draft_app_config:
            speech_to_text = draft_app_config["speech_to_text"]

            # 13.1 校验语音转文本格式
            if not speech_to_text or not isinstance(speech_to_text, dict):
                raise ValidateErrorException("语音转文本设置格式错误")
            # 13.2 校验语音转文本属性
            if set(speech_to_text.keys()) != {"enable"} or not isinstance(
                speech_to_text["enable"], bool
            ):
                raise ValidateErrorException("语音转文本设置格式错误")

        # 14.校验text_to_speech文本转语音设置
        if "text_to_speech" in draft_app_config:
            text_to_speech = draft_app_config["text_to_speech"]

            # 14.1 校验字典格式
            if not isinstance(text_to_speech, dict):
                raise ValidateErrorException("文本转语音设置格式错误")
            # 14.2 校验字段类型
            if (
                set(text_to_speech.keys()) != {"enable", "voice", "auto_play"}
                or not isinstance(text_to_speech["enable"], bool)
                # todo:等待多模态Agent实现时添加音色
                or text_to_speech["voice"] not in ["echo"]
                or not isinstance(text_to_speech["auto_play"], bool)
            ):
                raise ValidateErrorException("文本转语音设置格式错误")

        # 15.校验回答后生成建议问题
        if "suggested_after_answer" in draft_app_config:
            suggested_after_answer = draft_app_config["suggested_after_answer"]

            # 10.1 校验回答后建议问题格式
            if not suggested_after_answer or not isinstance(
                suggested_after_answer, dict
            ):
                raise ValidateErrorException("回答后建议问题设置格式错误")
            # 10.2 校验回答后建议问题格式
            if set(suggested_after_answer.keys()) != {"enable"} or not isinstance(
                suggested_after_answer["enable"], bool
            ):
                raise ValidateErrorException("回答后建议问题设置格式错误")

        # 16.校验review_config审核配置
        if "review_config" in draft_app_config:
            review_config = draft_app_config["review_config"]

            # 16.1 校验字段格式，非空
            if not review_config or not isinstance(review_config, dict):
                raise ValidateErrorException("审核配置格式错误")
            # 16.2 校验字段信息
            if set(review_config.keys()) != {
                "enable",
                "keywords",
                "inputs_config",
                "outputs_config",
            }:
                raise ValidateErrorException("审核配置格式错误")
            # 16.3 校验enable
            if not isinstance(review_config["enable"], bool):
                raise ValidateErrorException("review.enable格式错误")
            # 16.4 校验keywords
            if (
                not isinstance(review_config["keywords"], list)
                or (review_config["enable"] and len(review_config["keywords"]) == 0)
                or len(review_config["keywords"]) > 100
            ):
                raise ValidateErrorException("review.keywords非空且不能超过100个关键词")
            for keyword in review_config["keywords"]:
                if not isinstance(keyword, str):
                    raise ValidateErrorException("review.keywords敏感词必须是字符串")
            # 16.5 校验inputs_config输入配置
            if (
                not review_config["inputs_config"]
                or not isinstance(review_config["inputs_config"], dict)
                or set(review_config["inputs_config"].keys())
                != {"enable", "preset_response"}
                or not isinstance(review_config["inputs_config"]["enable"], bool)
                or not isinstance(
                    review_config["inputs_config"]["preset_response"], str
                )
            ):
                raise ValidateErrorException("review.inputs_config必须是一个字典")
            # 16.6 校验outputs_config输出配置
            if (
                not review_config["outputs_config"]
                or not isinstance(review_config["outputs_config"], dict)
                or set(review_config["outputs_config"].keys()) != {"enable"}
                or not isinstance(review_config["outputs_config"]["enable"], bool)
            ):
                raise ValidateErrorException("review.outputs_config格式错误")
            # 16.7 在开启审核模块的时候，必须确保inputs_config或者是outputs_config至少有一个是开启的
            if review_config["enable"]:
                if (
                    review_config["inputs_config"]["enable"] is False
                    and review_config["outputs_config"]["enable"] is False
                ):
                    raise ValidateErrorException("输入审核和输出审核至少需要开启一项")

                if (
                    review_config["inputs_config"]["enable"]
                    and review_config["inputs_config"]["preset_response"].strip() == ""
                ):
                    raise ValidateErrorException("输入审核预设响应不能为空")

        return draft_app_config
