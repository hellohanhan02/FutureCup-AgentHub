"""Workflow test helpers. Model calls remain explicit Mock calls."""

import json
from dataclasses import replace
from pathlib import Path

from futurecup.adapters.workflow_store import JsonSchemaWorkflowValidator, SQLiteWorkflowRepository
from futurecup.api.workflows import WorkflowInput
from futurecup.application.workflow import WorkflowEngine
from futurecup.domain.models import NodeKind, VersionRef, WorkflowDefinition, WorkflowEdge, WorkflowNode
from runtime_support import service


def task(identifier, agent_id="analyst", **config):
    return WorkflowNode(identifier, NodeKind.TASK, {"agent_id": agent_id, **config})


def workflow(nodes=None, edges=(), identifier="test-flow", **kwargs):
    return replace(WorkflowDefinition(VersionRef(identifier, "1"), tuple(nodes or [task("work")]),
        tuple(WorkflowEdge(*edge) for edge in edges), {}, {}, 4, name=identifier), **kwargs)


def engine(runtime=None, path=":memory:", dispatch=None):
    return WorkflowEngine(runtime or service(), SQLiteWorkflowRepository(str(path)), JsonSchemaWorkflowValidator(), dispatch)


def example(filename):
    root = Path(__file__).resolve().parents[2]
    return WorkflowInput.model_validate(json.loads((root / f"examples/workflows/{filename}.json").read_text(encoding="utf-8"))).definition()


def nodes(report):
    return {node.node_id: node for node in report.nodes}
