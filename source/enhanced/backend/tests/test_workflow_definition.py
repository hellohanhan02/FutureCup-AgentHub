"""JSON structure, DAG and safe mapping tests; no model requests."""

import asyncio
from dataclasses import asdict, replace

import pytest

from futurecup.adapters.workflow_store import definition_from, JsonSchemaWorkflowValidator
from futurecup.application.workflow_validation import validate_definition
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import NodeKind, WorkflowEdge, WorkflowNode
from workflow_support import example, task, workflow


@pytest.mark.parametrize("name", ["sequential", "parallel", "branch-loop", "increment-body"])
def test_serializable_valid_json_definitions(name):
    definition = example(name)
    validate_definition(definition, JsonSchemaWorkflowValidator())
    assert definition_from(asdict(definition)) == definition


@pytest.mark.parametrize("definition,code", [
    (workflow(edges=[("missing", "work")]), "workflow_edge_invalid"),
    (workflow([task("a"), task("b")], [("a", "b"), ("b", "a")]), "workflow_cycle"),
    (workflow(edges=[("work", "work")]), "workflow_cycle"),
    (workflow([task("a"), task("a")]), "workflow_duplicate_node"),
    (workflow([task("bad.id")]), "workflow_duplicate_node"),
    (workflow([task("a"), task("b")], [("a", "b", "true")]), "workflow_branch_invalid"),
    (workflow([task("a"), task("b")], [("a", "b"), ("a", "b")]), "workflow_edge_invalid"),
    (workflow([task("a", input={"$ref": "nodes.b.output"}), task("b")]), "workflow_mapping_reference"),
    (workflow([task("a", input={"$ref": "__import__.os"})]), "workflow_mapping_reference"),
    (workflow([task("a", unexpected="invalid")]), "workflow_config_invalid"),
    (workflow([task("a", limits={"max_calls": 0})]), "workflow_limits_invalid"),
    (workflow([WorkflowNode("join", NodeKind.PARALLEL, {"join": "all_selected"})]), "workflow_join_invalid"),
    (workflow([WorkflowNode("loop", NodeKind.LOOP, {"max_iterations": 0, "workflow": {"id": "child", "version": "1"}})]), "workflow_loop_invalid"),
    (workflow([WorkflowNode("child", NodeKind.SUBWORKFLOW, {"workflow": {"id": "child"}})]), "workflow_reference_invalid"),
])
def test_invalid_structure_is_rejected(definition, code):
    with pytest.raises(RuntimeFault) as error:
        validate_definition(definition, JsonSchemaWorkflowValidator())
    assert error.value.code == code


@pytest.mark.parametrize("config", [{"output_mode": {}}, {"on_error": []}, {"timeout_seconds": True},
                                     {"agent_version": []}, {"input": {"$ref": 42}}])
def test_wrong_json_config_types_raise_public_validation_errors(config):
    with pytest.raises(RuntimeFault) as error:
        validate_definition(workflow([task("work", **config)]), JsonSchemaWorkflowValidator())
    assert error.value.status_code == 422


def test_remote_schema_references_and_nonfinite_values_rejected():
    validator = JsonSchemaWorkflowValidator()
    with pytest.raises(RuntimeFault):
        validate_definition(replace(workflow(), input_schema={"$ref": "https://must-not-be-fetched.invalid"}), validator)
    with pytest.raises(RuntimeFault):
        validate_definition(workflow([task("work", input=float("nan"))]), validator)


def test_workflow_versions_are_immutable():
    from workflow_support import engine
    async def scenario():
        service = engine()
        try:
            await service.register(workflow())
            with pytest.raises(RuntimeFault) as error:
                await service.register(workflow())
            assert error.value.code == "workflow_exists"
            new = replace(workflow(), ref=replace(workflow().ref, version="2"), name="new version")
            await service.register(new)
            assert (await service.repository.latest(new.ref.id)).ref.version == "2"
            assert (await service.repository.get_definition(workflow().ref)).name == "test-flow"
        finally:
            await service.close()
            await service.runtime.close()
    asyncio.run(scenario())
