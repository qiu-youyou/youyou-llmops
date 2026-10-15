"""四种会话入口共用的 Agent 准备与执行生命周期。"""

import json
import logging
from copy import deepcopy
from threading import Thread
from uuid import uuid4

from flask import current_app

from internal.core.language_model.entities.model_entity import ModelFeature
from internal.exception import ValidateErrorException
from internal.schema.chat_schema import ChatReq
from internal.core.agent.agents import FunctionCallAgent
from internal.core.agent.entities import AgentConfig
from internal.core.agent.entities.queue_entity import AgentThought, QueueEvent
from internal.core.agent.usage import merge_agent_thought, stream_payload
from internal.core.memory import TokenBufferMemory
from internal.entity.dataset_entity import RetrievalSource

TERMINAL_EVENTS = {
    QueueEvent.AGENT_END,
    QueueEvent.ERROR,
    QueueEvent.STOP,
    QueueEvent.TIMEOUT,
}
logger = logging.getLogger(__name__)


def prepare_app_agent(
    *,
    db,
    language_model_manager,
    app_config_service,
    retrieval_service,
    config,
    conversation,
    resource_owner_id,
    operator_id,
    invoke_from,
    req: ChatReq,
):
    """资源按应用所有者解析；任务仍绑定发起会话的操作者。"""
    llm = language_model_manager.create_language_model(config["model_config"])
    if req.image_urls.data and ModelFeature.IMAGE_INPUT not in llm.features:
        raise ValidateErrorException("当前模型不支持图片输入，请切换支持图片的模型")
    history = TokenBufferMemory(
        db=db, conversation=conversation, model_instance=llm
    ).get_history_prompt_messages(message_limit=config["dialog_round"])
    tools = list(
        app_config_service.get_langchain_tools_by_tools_config(config["tools"])
    )
    if config["datasets"]:
        tools.append(
            retrieval_service.create_langchain_tool_from_search(
                flask_app=current_app._get_current_object(),
                dataset_ids=[dataset["id"] for dataset in config["datasets"]],
                account_id=resource_owner_id,
                retrival_source=RetrievalSource.APP,
                **config["retrieval_config"],
            )
        )
    if config["workflows"]:
        tools.extend(
            app_config_service.get_langchain_tools_by_workflow_ids(
                [workflow["id"] for workflow in config["workflows"]]
            )
        )
    return (
        FunctionCallAgent(
            llm=llm,
            agent_config=AgentConfig(
                user_id=operator_id,
                invoke_from=invoke_from,
                enable_long_term_memory=config["long_term_memory"]["enable"],
                preset_prompt=config["preset_prompt"],
                review_config=config["review_config"],
                tools=tools,
            ),
        ),
        history,
    )


class ChatRuntime:
    """在请求内捕获标量及配置快照，后台只接收事件和独立持久化参数。"""

    def __init__(
        self,
        *,
        conversation_service,
        account_id,
        app_id,
        app_config,
        conversation_id,
        message_id,
        end_user_id=None,
        data_prefix="data: ",
    ):
        self.save_callback = conversation_service.save_agent_thoughts
        self.save_kwargs = dict(
            flask_app=current_app._get_current_object(),
            account_id=account_id,
            app_id=app_id,
            app_config=deepcopy(app_config),
            conversation_id=conversation_id,
            message_id=message_id,
        )
        self.identifiers = dict(
            conversation_id=str(conversation_id), message_id=str(message_id)
        )
        if end_user_id is not None:
            self.identifiers["end_user_id"] = str(end_user_id)
        self.data_prefix = data_prefix

    @staticmethod
    def _background(target, kwargs):
        try:
            Thread(target=target, kwargs=kwargs).start()
        except RuntimeError:
            logger.exception(
                "Unable to start chat completion thread; completing inline"
            )
            target(**kwargs)

    def save(self, thoughts):
        self._background(
            self.save_callback, {**self.save_kwargs, "agent_thoughts": list(thoughts)}
        )

    def stream(self, agent, state):
        # 延迟启动 Agent，避免从未消费的 HTTP 响应启动无人接收的生产线程。
        def generate():
            thoughts = {}
            source = None
            finished = False
            task_id = uuid4()

            def finish():
                nonlocal finished
                if finished:
                    return
                finished = True
                try:
                    close = getattr(source, "close", None)
                    if close:
                        close()
                except Exception:
                    logger.exception("Unable to close chat iterator")
                finally:
                    self.save(thoughts.values())

            def consume():
                nonlocal source, task_id
                try:
                    source = iter(agent.stream(state))
                    for thought in source:
                        task_id = thought.task_id
                        yield thought
                        if thought.event in TERMINAL_EVENTS:
                            return
                    raise RuntimeError("Agent stream ended without a terminal event")
                except Exception:
                    logger.exception("Agent stream failed")
                    yield AgentThought(
                        id=uuid4(),
                        task_id=task_id,
                        event=QueueEvent.ERROR,
                        observation="会话执行失败，请稍后重试",
                    )

            events = consume()

            def drain():
                try:
                    for thought in events:
                        merge_agent_thought(thoughts, thought)
                        if thought.event in TERMINAL_EVENTS:
                            break
                finally:
                    finish()

            try:
                for thought in events:
                    merge_agent_thought(thoughts, thought)
                    data = {
                        **stream_payload(thought, thoughts),
                        **self.identifiers,
                        "id": str(thought.id),
                        "task_id": str(thought.task_id),
                    }
                    if thought.event in TERMINAL_EVENTS:
                        finish()
                    yield f"event: {thought.event.value}\n{self.data_prefix}{json.dumps(data, ensure_ascii=False)}\n\n"
                    if finished:
                        return
            finally:
                if not finished:
                    self._background(drain, {})

        return generate()
