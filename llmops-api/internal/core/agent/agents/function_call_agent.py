#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   function_call_agent
@Time   :   2026/1/23 10:05
@Author :   s.qiu@foxmail.com
"""

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    RemoveMessage,
    ToolMessage,
    messages_to_dict,
    AIMessage,
)
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from internal.core.agent.entities.agent_entity import (
    AgentState,
    AGENT_SYSTEM_PROMPT_TEMPLATE,
    DATASET_RETRIEVAL_TOOL_NAME,
    MAX_ITERATION_RESPONSE,
)
from internal.core.agent.entities.queue_entity import AgentThought, QueueEvent
from internal.core.language_model.entities.model_entity import ModelFeature
from internal.core.language_model.usage import TokenUsage, collect_usage
from internal.exception import FailException
from .base_agent import BaseAgent


class FunctionCallAgent(BaseAgent):
    """工具函数调用智能体"""

    name: str = "function_call_agent"

    def _build_agent(self) -> CompiledStateGraph:
        # 创建图
        graph = StateGraph(AgentState)
        # 添加节点
        graph.add_node("preset_operation", self._preset_operation_node)
        graph.add_node("long_term_memory_recall", self._long_term_memory_recall_node)
        graph.add_node("llm", self._llm_node)
        graph.add_node("tools", self._tools_node)

        # 起点、终点、条件边
        graph.set_entry_point("preset_operation")
        graph.add_conditional_edges(
            "preset_operation", self._preset_operation_condition
        )
        graph.add_edge("long_term_memory_recall", "llm")
        graph.add_conditional_edges("llm", self._tools_condition)
        graph.add_edge("tools", "llm")

        # 编译
        agent = graph.compile()
        return agent

    def _preset_operation_node(self, state: AgentState) -> AgentState:
        """预设节点：输入审核、数据预处理灯"""
        review_config = self.agent_config.review_config
        query = state["messages"][-1].content
        # 是否开启审核配置
        if review_config["enable"] and review_config["inputs_config"]["enable"]:
            contains_keyword = any(
                keyword in query for keyword in review_config["keywords"]
            )
            if contains_keyword:
                preset_response = review_config["inputs_config"]["preset_response"]
                self.agent_queue_manager.publish(
                    state["task_id"],
                    AgentThought(
                        id=uuid.uuid4(),
                        task_id=state["task_id"],
                        event=QueueEvent.AGENT_MESSAGE,
                        thought=preset_response,
                        message=messages_to_dict(state["messages"]),
                        answer=preset_response,
                        usage=TokenUsage.not_called(),
                        latency=0,
                    ),
                )
                self.agent_queue_manager.publish(
                    state["task_id"],
                    AgentThought(
                        id=uuid.uuid4(),
                        task_id=state["task_id"],
                        event=QueueEvent.AGENT_END,
                    ),
                )
                return {"messages": [AIMessage(preset_response)]}
        return {"messages": []}

    def _long_term_memory_recall_node(self, state: AgentState) -> AgentState:
        """长期记忆召回 节点"""

        # 获取配置 是否开启长期记忆
        long_term_memory = ""
        if self.agent_config.enable_long_term_memory:
            long_term_memory = state["long_term_memory"]
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=uuid.uuid4(),
                    task_id=state["task_id"],
                    event=QueueEvent.LONG_TERM_MEMORY_RECALL,
                    observation=long_term_memory,
                ),
            )

        # 构建系统预设消息 preset_prompt+long_term_memory
        preset_prompt = [
            SystemMessage(
                AGENT_SYSTEM_PROMPT_TEMPLATE.format(
                    preset_prompt=self.agent_config.preset_prompt,
                    long_term_memory=long_term_memory,
                )
            )
        ]

        # 将 history 添加到消息列表
        history = state["history"]
        if isinstance(history, list) and len(history) > 0:
            if len(history) % 2 != 0:
                self.agent_queue_manager.publish_error(
                    state["task_id"], "智能体历史消息格式错误"
                )
                logging.error(
                    f"智能体历史消息列表格式错误，history={json.dumps(messages_to_dict(history), ensure_ascii=False)}"
                )
                raise FailException("历史消息列表格式错误")
            preset_prompt.extend(history)

        # 将 query 当前提问 进行拼接
        human_message = state["messages"][-1]
        preset_prompt.append(HumanMessage(content=human_message.content))
        return {"messages": [RemoveMessage(id=human_message.id), *preset_prompt]}

    def _llm_node(self, state: AgentState) -> AgentState:
        """模型节点"""

        # 检测当前迭代次数是否符合
        if state["iteration_count"] > self.agent_config.max_iteration_count:
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=uuid.uuid4(),
                    task_id=state["task_id"],
                    event=QueueEvent.AGENT_MESSAGE,
                    thought=MAX_ITERATION_RESPONSE,
                    message=messages_to_dict(state["messages"]),
                    answer=MAX_ITERATION_RESPONSE,
                    usage=TokenUsage.not_called(),
                    latency=0,
                ),
            )
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=uuid.uuid4(),
                    task_id=state["task_id"],
                    event=QueueEvent.AGENT_END,
                ),
            )
            return {"messages": [AIMessage(MAX_ITERATION_RESPONSE)]}

        id = uuid.uuid4()
        llm = self.llm
        start_at = time.perf_counter()
        started_at = datetime.now(timezone.utc)

        # llm是否支持绑定工具 是否有可以绑定的工具
        if (
            ModelFeature.TOOL_CALL in llm.features
            and hasattr(llm, "bind_tools")
            and callable(getattr(llm, "bind_tools"))
            and len(self.agent_config.tools) > 0
        ):
            llm = llm.bind_tools(self.agent_config.tools)

        # 流式调用模型 获取内容
        gathered = None
        last_usage_chunk = None
        is_first_chunk = True
        generation_type = ""
        try:
            for chunk in llm.stream(state["messages"]):
                # 用量是整次请求的快照。不要累加每个 chunk 的累计用量。
                if chunk.usage_metadata is not None or chunk.response_metadata.get(
                    "token_usage"
                ):
                    last_usage_chunk = chunk
                if is_first_chunk:
                    gathered = chunk
                    is_first_chunk = False
                else:
                    gathered += chunk

                # 根据生成的类型 向队列中添加不同事件
                if chunk.tool_calls:
                    generation_type = "thought"
                elif chunk.content:
                    generation_type = "message"
                # 发布智能体消息事件
                if generation_type == "message" and chunk.content:
                    # 检测输出审核
                    review_config = self.agent_config.review_config
                    content = chunk.content
                    if (
                        review_config["enable"]
                        and review_config["outputs_config"]["enable"]
                    ):
                        for keyword in review_config["keywords"]:
                            content = re.sub(
                                re.escape(keyword), "**", content, flags=re.IGNORECASE
                            )
                    self.agent_queue_manager.publish(
                        state["task_id"],
                        AgentThought(
                            id=id,
                            task_id=state["task_id"],
                            event=QueueEvent.AGENT_MESSAGE,
                            thought=content,
                            message=messages_to_dict(state["messages"]),
                            answer=content,
                            latency=(time.perf_counter() - start_at),
                        ),
                    )
        except Exception as e:
            usage = collect_usage(
                last_usage_chunk, self.llm.metadata, started_at, complete=False
            )
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=id,
                    task_id=state["task_id"],
                    event=(
                        QueueEvent.AGENT_MESSAGE
                        if generation_type == "message"
                        else QueueEvent.AGENT_THOUGHT
                    ),
                    message=messages_to_dict(state["messages"]),
                    usage=usage,
                    **usage.legacy_fields(),
                    latency=time.perf_counter() - start_at,
                ),
            )
            logging.exception("LLM节点发生错误, 错误信息: %(error)s", {"error": e})
            self.agent_queue_manager.publish_error(
                state["task_id"], f"LLM节点发生错误, 错误信息: {str(e)}"
            )
            raise e

        # 使用供应商返回的用量，禁止拿 GPT 的 tokenizer 重新估算账单。
        usage_message = last_usage_chunk
        if usage_message is not None and gathered is not None:
            gathered = gathered.model_copy(
                update={
                    "usage_metadata": usage_message.usage_metadata,
                    "response_metadata": {
                        **gathered.response_metadata,
                        **usage_message.response_metadata,
                    },
                }
            )
            usage_message = gathered
        usage = collect_usage(usage_message, self.llm.metadata, started_at)
        # 即使没有可见文本（例如纯思考或空回复），也要结算本次调用。
        generation_type = (
            "thought" if gathered is not None and gathered.tool_calls else "message"
        )

        # 发布智能体推理事件
        if generation_type == "thought":
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=id,
                    task_id=state["task_id"],
                    event=QueueEvent.AGENT_THOUGHT,
                    thought=json.dumps(gathered.tool_calls, ensure_ascii=False),
                    message=messages_to_dict(state["messages"]),
                    usage=usage,
                    **usage.legacy_fields(),
                    latency=(time.perf_counter() - start_at),
                ),
            )
        elif generation_type == "message":
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=id,
                    task_id=state["task_id"],
                    event=QueueEvent.AGENT_MESSAGE,
                    message=messages_to_dict(state["messages"]),
                    usage=usage,
                    **usage.legacy_fields(),
                    latency=time.perf_counter() - start_at,
                ),
            )
            # 如果LLM直接生成answer则表示已经拿到了最终答案，则停止监听
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=uuid.uuid4(),
                    task_id=state["task_id"],
                    event=QueueEvent.AGENT_END,
                ),
            )

        return {
            "messages": [gathered or AIMessage(content="")],
            "iteration_count": state["iteration_count"] + 1,
        }

    def _tools_node(self, state: AgentState) -> AgentState:
        """工具节点"""

        # 工具映射转换
        tools_by_name = {tool.name: tool for tool in self.agent_config.tools}
        # 提取消息 工具调用信息
        tool_calls = state["messages"][-1].tool_calls
        # 循环执行工具 组装工具执行结果
        messages = []
        for tool_call in tool_calls:
            id = uuid.uuid4()
            start_at = time.perf_counter()

            try:
                tool = tools_by_name[tool_call["name"]]
                tool_result = tool.invoke(tool_call["args"])
            except Exception as e:
                tool_result = f"工具执行出错：{str(e)}"

            messages.append(
                ToolMessage(
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                    content=json.dumps(tool_result, ensure_ascii=False),
                )
            )

            # 判断执行工具的名字，提交不同事件，涵盖智能体动作以及知识库检索
            event = (
                QueueEvent.AGENT_ACTION
                if tool_call["name"] != DATASET_RETRIEVAL_TOOL_NAME
                else QueueEvent.DATASET_RETRIEVAL
            )
            self.agent_queue_manager.publish(
                state["task_id"],
                AgentThought(
                    id=id,
                    task_id=state["task_id"],
                    event=event,
                    observation=json.dumps(tool_result, ensure_ascii=False),
                    tool=tool_call["name"],
                    tool_input=tool_call["args"],
                    latency=(time.perf_counter() - start_at),
                ),
            )
        return {"messages": messages}

    @classmethod
    def _preset_operation_condition(
        cls, state: AgentState
    ) -> Literal["long_term_memory_recall", "__end__"]:
        """预设节点条件边 是否触发预设响应"""
        # 如果是AI消息 则为触发审核 直接结束
        message = state["messages"][-1]
        if message.type == "ai":
            return END
        return "long_term_memory_recall"

    @classmethod
    def _tools_condition(self, state: AgentState) -> Literal["tools", "__end__"]:
        """工具节点条件边 是否处罚工具节点"""

        # 提取最后一条AI消息
        ai_message = state["messages"][-1]
        # AI消息中如果存在 tools_calls 执行 tool 节点 反之表示结束
        if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
            return "tools"
        return END
