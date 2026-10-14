#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   tool_node
@Time   :   2026/3/9
@Author :   s.qiu@foxmail.com
"""

import json
import time
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from internal.core.tools.api_tools.entities import ToolEntity
from internal.core.workflow.entities.node_entity import NodeResult, NodeStatus
from internal.core.workflow.entities.workflow_entity import WorkflowState
from internal.core.workflow.nodes import BaseNode
from internal.core.workflow.nodes.tool.tool_entity import ToolNodeData
from internal.core.workflow.utils.helper import extract_variables_from_state
from internal.exception import NotFoundException, FailException
from internal.model import ApiTool


class ToolNode(BaseNode):
    """工具节点"""
    node_data: ToolNodeData
    _tool: BaseTool = PrivateAttr(None)

    def __init__(self, *args: Any, account_id: UUID, **kwargs: Any):
        """工具初始化"""
        super().__init__(*args, **kwargs)
        from app.http.module import injector

        # 内置工具 通过内置插件管理器获取
        if self.node_data.tool_type == "builtin_tool":
            from internal.core.tools.builtin_tools.providers import BuiltinProviderManager
            builtin_provider_manager = injector.get(BuiltinProviderManager)
            builtin_tool = builtin_provider_manager.get_tool(self.node_data.provider_id, self.node_data.tool_id)
            if not builtin_tool:
                raise NotFoundException("该内置插件不存在")
            self._tool = builtin_tool(**self.node_data.params)

        # API工具 通过自定义插件管理器获取
        else:
            from pkg.sqlalchemy import SQLAlchemy
            db = injector.get(SQLAlchemy)

            # 根据提供者获取插件
            api_tool = db.session.query(ApiTool).filter(
                ApiTool.provider_id == self.node_data.provider_id,
                ApiTool.name == self.node_data.tool_id,
                ApiTool.account_id == account_id,
            ).one_or_none()
            if not api_tool:
                raise NotFoundException("该API插件不存在")

            from internal.core.tools.api_tools.providers.api_provider_manager import ApiProviderManager
            api_provider_manager = injector.get(ApiProviderManager)
            self._tool = api_provider_manager.get_tool(ToolEntity(
                id=str(api_tool.id),
                name=api_tool.name,
                url=api_tool.url,
                method=api_tool.method,
                description=api_tool.description,
                headers=api_tool.provider.headers,
                parameters=api_tool.parameters,
            ))

    def invoke(
            self,
            state: WorkflowState,
            config: RunnableConfig | None = None,
            **kwargs: Any,
    ) -> WorkflowState:
        """工具插件执行节点"""
        start_at = time.perf_counter()
        inputs_dict = extract_variables_from_state(self.node_data.inputs, state)
        # 调用工具并获取结果
        try:
            result = self._tool.invoke(inputs_dict)
        except Exception as e:
            raise FailException("扩展插件执行失败，请稍后尝试")
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)

        # 组装输出结构
        outputs = {}
        if self.node_data.outputs:
            outputs[self.node_data.outputs[0].name] = result
        else:
            outputs["text"] = result
        return {
            "node_results": [
                NodeResult(
                    node_data=self.node_data,
                    status=NodeStatus.SUCCEEDED,
                    inputs=inputs_dict,
                    outputs=outputs,
                    latency=(time.perf_counter() - start_at),
                )
            ]
        }
