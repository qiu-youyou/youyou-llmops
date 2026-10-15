#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   workflow_service
@Time   :   2026/3/15 20:50
@Author :   s.qiu@foxmail.com
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Generator
from uuid import UUID

from flask import request
from injector import inject
from pydantic import ValidationError
from sqlalchemy import desc

from internal.core.tools.builtin_tools.providers import BuiltinProviderManager
from internal.core.language_model import LanguageModelManager
from internal.core.workflow import Workflow as WorkflowTool
from internal.core.workflow.entities.edge_entity import BaseEdgeData
from internal.core.workflow.entities.node_entity import NodeType, BaseNodeData
from internal.core.workflow.entities.workflow_entity import WorkflowConfig
from internal.core.workflow.nodes import (
    CodeNodeData,
    DatasetRetrievalNodeData,
    EndNodeData,
    HttpRequestNodeData,
    LLMNodeData,
    StartNodeData,
    TemplateTransformNodeData,
    ToolNodeData,
)
from internal.entity.workflow_entity import (
    DEFAULT_WORKFLOW_CONFIG,
    WorkflowStatus,
    WorkflowResultStatus,
)
from internal.exception import (
    ValidateErrorException,
    NotFoundException,
    ForbiddenException,
    FailException,
)
from internal.lib.helper import convert_model_to_dict
from internal.model import Workflow, Account, Dataset, ApiTool, WorkflowResult
from internal.schema.workflow_schema import CreateWorkflowReq, GetWorkflowsWithPageReq
from pkg.paginator import Paginator
from pkg.sqlalchemy import SQLAlchemy
from .base_service import BaseService


@inject
@dataclass
class WorkflowService(BaseService):
    """工作流服务"""

    db: SQLAlchemy
    builtin_provider_manager: BuiltinProviderManager
    language_model_manager: LanguageModelManager

    def create_workflow(self, req: CreateWorkflowReq, account: Account) -> Workflow:
        """创建工作流"""
        check_workflow = (
            self.db.session.query(Workflow)
            .filter(
                Workflow.tool_call_name == req.tool_call_name.data.strip(),
                Workflow.account_id == account.id,
            )
            .one_or_none()
        )
        if check_workflow:
            raise ValidateErrorException("当前账号下已经存在同名的工作流")
        return self.create(
            Workflow,
            **{
                **req.data,
                **DEFAULT_WORKFLOW_CONFIG,
                "account_id": account.id,
                "is_debug_passed": False,
                "status": WorkflowStatus.DRAFT,
                "tool_call_name": req.tool_call_name.data.strip(),
            },
        )

    def get_workflow(self, workflow_id: UUID, account: Account) -> Workflow:
        """获取工作流信息"""
        workflow = self.get(Workflow, workflow_id)
        if not workflow:
            raise NotFoundException("工作流不存在")
        if workflow.account_id != account.id:
            raise ForbiddenException("当前账号无权限访问该应用")
        return workflow

    def delete_workflow(self, workflow_id: UUID, account: Account) -> Workflow:
        """删除工作流"""
        workflow = self.get_workflow(workflow_id, account)
        self.delete(workflow)
        return workflow

    def update_workflow(
        self, workflow_id: UUID, account: Account, **kwargs
    ) -> Workflow:
        """更新工作流"""
        workflow = self.get_workflow(workflow_id, account)
        workflow = self.update(workflow, **kwargs)
        return workflow

    def get_workflows_with_page(
        self, req: GetWorkflowsWithPageReq, account: Account
    ) -> tuple[list[Workflow], Paginator]:
        """获取工作流分页接口列表"""
        paginator = Paginator(db=self.db, req=req)
        filters = [Workflow.account_id == account.id]
        if req.search_word.data:
            filters.append(Workflow.name.ilike(f"%{req.search_word.data}%"))
        if req.status.data:
            filters.append(Workflow.status == req.status.data)

        workflows = paginator.paginate(
            self.db.session.query(Workflow)
            .filter(*filters)
            .order_by(desc("created_at"))
        )
        return workflows, paginator

    def update_draft_graph(
        self, workflow_id: UUID, draft_graph: dict[str, Any], account: Account
    ) -> Workflow:
        """更新工作流草稿图配置"""
        workflow = self.get_workflow(workflow_id, account)
        validate_draft_graph = self._validate_graph(draft_graph, account)
        # 更新工作流草稿配置 每次修改都需要将 is_debug_passed 重置为 False
        self.update(
            workflow, **{"draft_graph": validate_draft_graph, "is_debug_passed": False}
        )
        return workflow

    def get_draft_graph(self, workflow_id: UUID, account: Account) -> dict[str, Any]:
        """获取工作流草稿配置信息"""
        workflow = self.get_workflow(workflow_id, account)
        draft_graph = workflow.draft_graph
        validate_draft_graph = self._validate_graph(draft_graph, account)

        # 遍历节点 为工具节点/知识库节点附加元数据
        for node in validate_draft_graph["nodes"]:
            # 如果是工具类型 附加工具的名称、名称、参数等额外信息
            if node.get("node_type") == NodeType.TOOL:  # 内置工具
                if node.get("tool_type") == "builtin_tool":
                    provider = self.builtin_provider_manager.get_provider(
                        node.get("provider_id")
                    )
                    if not provider:
                        continue
                    tool_entity = provider.get_tool_entity(node.get("tool_id"))
                    if not tool_entity:
                        continue

                    param_keys = set([param.name for param in tool_entity.params])
                    params = node.get("params")
                    # 工具的 params 和草稿中的 params是否一致 不一致全部重置为默认值
                    if set(params.keys()) - param_keys:
                        params = {
                            param.name: param.default
                            for param in tool_entity.params
                            if param.default is not None
                        }
                    # 数据校验成功附加展示信息
                    provider_entity = provider.provider_entity
                    node["meta"] = {
                        "type": "builtin_tool",
                        "provider": {
                            "id": provider_entity.name,
                            "name": provider_entity.name,
                            "label": provider_entity.label,
                            "icon": f"{request.scheme}://{request.host}/builtin-tools/{provider_entity.name}/icon",
                            "description": provider_entity.description,
                        },
                        "tool": {
                            "id": tool_entity.name,
                            "name": tool_entity.name,
                            "label": tool_entity.label,
                            "description": tool_entity.description,
                            "params": params,
                        },
                    }
                else:  # 自定义工具
                    # 查询数据库工具
                    tool_record = (
                        self.db.session.query(ApiTool)
                        .filter(
                            ApiTool.provider_id == node.get("provider_id"),
                            ApiTool.name == node.get("tool_id"),
                            ApiTool.account_id == account.id,
                        )
                        .one_or_none()
                    )
                    if not tool_record:
                        continue
                    # 组装api工具展示信息
                    provider = tool_record.provider
                    node["meta"] = {
                        "type": "api_tool",
                        "provider": {
                            "id": str(provider.id),
                            "name": provider.name,
                            "label": provider.name,
                            "icon": provider.icon,
                            "description": provider.description,
                        },
                        "tool": {
                            "id": str(tool_record.id),
                            "name": tool_record.name,
                            "label": tool_record.name,
                            "description": tool_record.description,
                            "params": {},
                        },
                    }

            elif node.get("node_type") == NodeType.DATASET_RETRIEVAL:
                """处理节点类型为知识库检索 附加知识库名称图标等meta信息"""
                datasets = (
                    self.db.session.query(Dataset)
                    .filter(
                        Dataset.id.in_(node.get("dataset_ids", [])),
                        Dataset.account_id == account.id,
                    )
                    .all()
                )
                node["meta"] = {
                    "datasets": [
                        {
                            "id": dataset.id,
                            "name": dataset.name,
                            "icon": dataset.icon,
                            "description": dataset.description,
                        }
                        for dataset in datasets
                    ]
                }
        return validate_draft_graph

    def debug_workflow(
        self, workflow_id: UUID, inputs: dict[str, Any], account: Account
    ) -> Generator:
        """调试工作流配置 流式事件输出"""
        workflow = self.get_workflow(workflow_id, account)
        # 创建工作流工具
        workflow_tool = WorkflowTool(
            workflow_config=WorkflowConfig(
                account_id=account.id,
                name=workflow.tool_call_name,
                description=workflow.description,
                nodes=workflow.draft_graph.get("nodes", []),
                edges=workflow.draft_graph.get("edges", []),
            )
        )

        def handle_stream() -> Generator:
            """流式处理节点运行结果"""
            node_results = []

            workflow_in_session = self.get_workflow(workflow_id, account)

            # 添加数据库工作流运行结果记录
            workflow_result = self.create(
                WorkflowResult,
                **{
                    "app_id": None,
                    "account_id": account.id,
                    "workflow_id": workflow_in_session.id,
                    "graph": workflow_in_session.draft_graph,
                    "state": [],
                    "latency": 0,
                    "status": WorkflowResultStatus.RUNNING,
                },
            )

            # 调用stream获取工具嘻嘻
            start_at = time.perf_counter()
            try:
                for chunk in workflow_tool.stream(inputs):
                    # chunk的格式为:{"node_name": WorkflowState}，所以需要取出节点响应结构的第1个key
                    first_key = next(iter(chunk))

                    # 取出各个节点的运行结果
                    node_result = chunk[first_key]["node_results"][0]
                    node_result_dict = convert_model_to_dict(node_result)
                    node_results.append(node_result_dict)

                    # 组装响应数据并流式事件输出
                    data = {"id": str(uuid.uuid4()), **node_result_dict}
                    yield f"event: workflow\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

                # 流式输出完毕后，将结果存储到数据库中
                self.update(
                    workflow_result,
                    **{
                        "status": WorkflowResultStatus.SUCCEEDED,
                        "latency": (time.perf_counter() - start_at),
                        "state": node_results,
                    },
                )
                self.update(workflow_in_session, **{"is_debug_passed": True})
            except Exception:
                logging.exception("工作流调试执行失败，workflow_id=%s", workflow_id)
                self.update(
                    workflow_result,
                    **{
                        "status": WorkflowResultStatus.FAILED,
                        "latency": (time.perf_counter() - start_at),
                        "state": node_results,
                    },
                )
                # 失败的本次调试不能保留旧的通过标记，否则修改后的图可能被误发布。
                self.update(workflow_in_session, **{"is_debug_passed": False})
                data = {
                    "id": str(uuid.uuid4()),
                    "status": "failed",
                    "error": "工作流执行失败",
                }
                yield f"event: workflow\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        return handle_stream()

    def publish_workflow(self, workflow_id: UUID, account: Account) -> Workflow:
        """发布指定的工作流"""
        workflow = self.get_workflow(workflow_id, account)
        if workflow.is_debug_passed is False:
            raise FailException("该工作流未调试通过，请调试通过后发布")
        if workflow.status == WorkflowStatus.PUBLISHED:
            raise FailException("该工作流已发布")
        # 使用 WorkflowConfig 二次校验
        try:
            WorkflowConfig(
                account_id=account.id,
                name=workflow.tool_call_name,
                description=workflow.description,
                nodes=workflow.draft_graph.get("nodes", []),
                edges=workflow.draft_graph.get("edges", []),
            )
        except Exception:
            self.update(workflow, **{"is_debug_passed": False})
            raise ValidateErrorException("工作流配置校验失败")

        self.update(
            workflow,
            **{
                "graph": workflow.draft_graph,
                "status": WorkflowStatus.PUBLISHED,
                "is_debug_passed": False,
            },
        )

        return workflow

    def cancel_publish_workflow(self, workflow_id: UUID, account: Account) -> Workflow:
        """取消发布指定的工作流"""
        workflow = self.get_workflow(workflow_id, account)
        if workflow.status != WorkflowStatus.PUBLISHED:
            raise FailException("该工作流未发布")

        self.update(
            workflow,
            **{"graph": {}, "status": WorkflowStatus.DRAFT, "is_debug_passed": False},
        )
        return workflow

    def _validate_graph(
        self, graph: dict[str, Any], account: Account
    ) -> dict[str, Any]:
        """校验传递的graph信息，涵盖nodes和edges对应的数据，该函数使用相对宽松的校验方式，并且因为是草稿，不需要校验节点与边的关系"""
        # 提取nodes和edges数据
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        if not isinstance(nodes, list):
            raise ValidateErrorException("工作流节点列表数据类型出错")
        if not isinstance(edges, list):
            raise ValidateErrorException("工作流边列表数据类型出错")
        # 构建节点类型与节点数据类映射
        node_data_classes = {
            NodeType.CODE: CodeNodeData,
            NodeType.DATASET_RETRIEVAL: DatasetRetrievalNodeData,
            NodeType.END: EndNodeData,
            NodeType.HTTP_REQUEST: HttpRequestNodeData,
            NodeType.LLM: LLMNodeData,
            NodeType.START: StartNodeData,
            NodeType.TEMPLATE_TRANSFORM: TemplateTransformNodeData,
            NodeType.TOOL: ToolNodeData,
        }

        # 循环校验nodes中各个节点对应的数据
        node_data_dict: dict[UUID, BaseNodeData] = {}
        start_nodes = 0
        end_nodes = 0
        for node in nodes:
            # 校验传递的node数据是不是字典
            if not isinstance(node, dict):
                raise ValidateErrorException("工作流节点数据类型出错")

            # 提取节点的node_type类型，并判断类型是否正确
            node_type = node.get("node_type", "")
            node_data_cls = node_data_classes.get(node_type, None)
            if node_data_cls is None:
                raise ValidateErrorException("工作流节点类型出错")

            # 实例化节点数据类型，保留具体字段错误，避免后续误报边端点不存在
            try:
                node_data = node_data_cls(**node)
            except ValidationError as error:
                node_label = node.get("title") or node.get("id") or node_type
                details = "; ".join(
                    f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                    for item in error.errors(include_url=False, include_input=False)
                )
                raise ValidateErrorException(
                    f"工作流节点[{node_label}]配置错误: {details}"
                ) from error

            # 判断节点id是否唯一
            if node_data.id in node_data_dict:
                raise ValidateErrorException("工作流节点id必须唯一")

            # 判断节点title是否唯一
            if any(
                item.title.strip() == node_data.title.strip()
                for item in node_data_dict.values()
            ):
                raise ValidateErrorException("工作流节点title必须唯一")

            # 对特殊节点进行判断，涵盖开始/结束/知识库检索/工具
            if node_data.node_type == NodeType.START:
                if start_nodes >= 1:
                    raise ValidateErrorException("工作流中只允许有1个开始节点")
                start_nodes += 1
            elif node_data.node_type == NodeType.END:
                if end_nodes >= 1:
                    raise ValidateErrorException("工作流中只允许有1个结束节点")
                end_nodes += 1
            elif node_data.node_type == NodeType.DATASET_RETRIEVAL:
                # 剔除关联知识库列表中不属于当前账户的数据
                datasets = (
                    self.db.session.query(Dataset)
                    .filter(
                        Dataset.id.in_(node_data.dataset_ids[:5]),
                        Dataset.account_id == account.id,
                    )
                    .all()
                )
                node_data.dataset_ids = [dataset.id for dataset in datasets]
            elif node_data.node_type == NodeType.LLM:
                validated_model_config = self.language_model_manager.validate_model_config(
                    node_data.language_model_config
                )
                node_data.language_model_config = validated_model_config.model_dump()
            elif node_data.node_type == NodeType.TOOL:
                # 判断工具的类型执行不同的操作
                if node_data.tool_type == "builtin_tool":
                    tool = self.builtin_provider_manager.get_tool(
                        node_data.provider_id, node_data.tool_id
                    )
                    if not tool:
                        raise ValidateErrorException("工具节点绑定的内置工具不存在")
                else:
                    # API工具，查询当前工具是否属于当前账号
                    tool_record = (
                        self.db.session.query(ApiTool)
                        .filter(
                            ApiTool.provider_id == node_data.provider_id,
                            ApiTool.name == node_data.tool_id,
                            ApiTool.account_id == account.id,
                        )
                        .one_or_none()
                    )
                    if not tool_record:
                        raise ValidateErrorException("工具节点绑定的API工具不存在")

            # 将数据添加到node_data_dict中
            node_data_dict[node_data.id] = node_data

        # 循环校验edges中各个节点对应的数据
        edge_data_dict: dict[UUID, BaseEdgeData] = {}
        for edge in edges:
            # 边类型为非字典则抛出错误，否则转换成BaseEdgeData
            if not isinstance(edge, dict):
                raise ValidateErrorException("工作流边数据类型出错")
            try:
                edge_data = BaseEdgeData(**edge)
            except ValidationError as error:
                edge_label = edge.get("id", "未知")
                details = "; ".join(
                    f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                    for item in error.errors(include_url=False, include_input=False)
                )
                raise ValidateErrorException(
                    f"工作流边[{edge_label}]配置错误: {details}"
                ) from error

            # 校验边edges的id是否唯一
            if edge_data.id in edge_data_dict:
                raise ValidateErrorException("工作流边数据id必须唯一")

            # 校验边中的source/target/source_type/target_type必须和nodes对得上
            source_node = node_data_dict.get(edge_data.source)
            target_node = node_data_dict.get(edge_data.target)
            if source_node is None or target_node is None:
                raise ValidateErrorException(
                    f"工作流边[{edge_data.id}]起点/终点对应的节点不存在"
                )
            if (
                edge_data.source_type != source_node.node_type
                or edge_data.target_type != target_node.node_type
            ):
                raise ValidateErrorException(
                    f"工作流边[{edge_data.id}]起点/终点节点类型错误"
                )

            # 校验边Edges里的边必须唯一(source+target必须唯一)
            if any(
                item.source == edge_data.source and item.target == edge_data.target
                for item in edge_data_dict.values()
            ):
                raise ValidateErrorException("工作流边数据不能重复添加")

            # 基础数据校验通过，将数据添加到edge_data_dict中
            edge_data_dict[edge_data.id] = edge_data

        return {
            "nodes": [
                convert_model_to_dict(node_data)
                for node_data in node_data_dict.values()
            ],
            "edges": [
                convert_model_to_dict(edge_data)
                for edge_data in edge_data_dict.values()
            ],
        }
