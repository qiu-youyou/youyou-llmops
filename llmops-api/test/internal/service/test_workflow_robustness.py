from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask

from internal.core.workflow import Workflow
from internal.core.workflow.entities.workflow_entity import WorkflowConfig
from internal.exception import ValidateErrorException


def _graph_with_template_ref(ref_node_id: str) -> dict:
    start_id = "10000000-0000-0000-0000-000000000001"
    template_id = "10000000-0000-0000-0000-000000000002"
    end_id = "10000000-0000-0000-0000-000000000003"
    return {
        "nodes": [
            {"id": start_id, "node_type": "start", "title": "Start", "inputs": [
                {"name": "query", "type": "string", "value": {"type": "literal", "content": ""}},
            ]},
            {"id": template_id, "node_type": "template_transform", "title": "Template",
             "template": "{{ query }}", "inputs": [
                {"name": "query", "type": "string", "value": {"type": "ref", "content": {
                    "ref_node_id": ref_node_id, "ref_var_name": "query",
                }}},
             ]},
            {"id": end_id, "node_type": "end", "title": "End", "outputs": []},
        ],
        "edges": [
            {"id": "10000000-0000-0000-0000-000000000011", "source": start_id,
             "source_type": "start", "target": template_id, "target_type": "template_transform"},
            {"id": "10000000-0000-0000-0000-000000000012", "source": template_id,
             "source_type": "template_transform", "target": end_id, "target_type": "end"},
        ],
    }


def test_workflow_rejects_self_referential_input_but_allows_predecessor_reference():
    account_id = uuid4()
    start_id = "10000000-0000-0000-0000-000000000001"
    template_id = "10000000-0000-0000-0000-000000000002"

    WorkflowConfig(account_id=account_id, name="workflow", description="test",
                   **_graph_with_template_ref(start_id))
    with pytest.raises(ValidateErrorException, match="引用数据出错"):
        WorkflowConfig(account_id=account_id, name="workflow", description="test",
                       **_graph_with_template_ref(template_id))


def test_serial_workflow_passes_start_value_through_template_to_end():
    graph = _graph_with_template_ref("10000000-0000-0000-0000-000000000001")
    template_id = "10000000-0000-0000-0000-000000000002"
    graph["nodes"][-1]["outputs"] = [{
        "name": "answer", "type": "string", "value": {"type": "ref", "content": {
            "ref_node_id": template_id, "ref_var_name": "output",
        }},
    }]
    config = WorkflowConfig(account_id=uuid4(), name="workflow", description="test", **graph)

    with Flask(__name__).app_context():
        assert Workflow(workflow_config=config).invoke({"query": "hello"}) == {"answer": "hello"}


def test_workflow_passes_its_account_to_custom_tool_node(monkeypatch):
    from internal.core.workflow.entities.node_entity import NodeType
    from internal.core.workflow import workflow as workflow_module

    account_id = uuid4()
    graph = {
        "nodes": [
            {"id": "20000000-0000-0000-0000-000000000001", "node_type": "start", "title": "Start", "inputs": []},
            {"id": "20000000-0000-0000-0000-000000000002", "node_type": "tool", "title": "Tool", "type": "api_tool", "provider_id": "provider", "tool_id": "tool"},
            {"id": "20000000-0000-0000-0000-000000000003", "node_type": "end", "title": "End", "outputs": []},
        ],
        "edges": [
            {"id": "20000000-0000-0000-0000-000000000011", "source": "20000000-0000-0000-0000-000000000001", "source_type": "start", "target": "20000000-0000-0000-0000-000000000002", "target_type": "tool"},
            {"id": "20000000-0000-0000-0000-000000000012", "source": "20000000-0000-0000-0000-000000000002", "source_type": "tool", "target": "20000000-0000-0000-0000-000000000003", "target_type": "end"},
        ],
    }
    received = []

    class RecorderToolNode:
        def __init__(self, **kwargs):
            received.append(kwargs)

        def __call__(self, state):
            return state

    monkeypatch.setitem(workflow_module.NodeClasses, NodeType.TOOL, RecorderToolNode)
    config = WorkflowConfig(account_id=account_id, name="workflow", description="test", **graph)

    with Flask(__name__).app_context():
        Workflow(workflow_config=config)

    assert received == [{"node_data": config.nodes[1], "account_id": account_id}]


def test_workflow_debug_failure_persists_failed_result_resets_publish_gate_and_emits_event(monkeypatch):
    from internal.service import workflow_service as module

    workflow = SimpleNamespace(
        id=uuid4(), tool_call_name="workflow", description="test",
        draft_graph={"nodes": [], "edges": []}, is_debug_passed=True,
    )
    workflow_result = SimpleNamespace(id=uuid4())

    class BrokenWorkflow:
        def __init__(self, **_):
            pass

        def stream(self, _):
            raise RuntimeError("node failed")
            yield  # pragma: no cover

    monkeypatch.setattr(module, "WorkflowTool", BrokenWorkflow)
    monkeypatch.setattr(module, "WorkflowConfig", lambda **kwargs: kwargs)
    updates = []
    service = SimpleNamespace(
        get_workflow=lambda *_: workflow,
        create=lambda *_args, **_kwargs: workflow_result,
        update=lambda record, **kwargs: updates.append((record, kwargs)),
    )
    account = SimpleNamespace(id=uuid4())

    frames = list(module.WorkflowService.debug_workflow(service, workflow.id, {}, account))

    assert frames[-1].startswith("event: workflow\n")
    assert '"status": "failed"' in frames[-1]
    assert "工作流执行失败" in frames[-1]
    assert (workflow, {"is_debug_passed": False}) in updates
    assert any(values.get("status") == "failed" for _, values in updates)
