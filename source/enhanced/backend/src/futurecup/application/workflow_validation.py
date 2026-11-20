"""Compile-time graph and template checks. No eval or arbitrary expressions."""

import json
from dataclasses import asdict

from futurecup.application.ports import WorkflowDataValidator
from futurecup.application.selfloop import recovery_from
from futurecup.domain.verification import ResourceBudget
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.scheduling import TaskFeatures
from futurecup.domain.models import JsonValue, NodeKind, RunLimits, WorkflowDefinition


def invalid(code: str = "workflow_invalid") -> None:
    raise RuntimeFault(code, "Invalid workflow definition or mapping", 422)


def identifier(value: object, node: bool = False) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 64
            and all(c.isascii() and (c.isalnum() or c in ("_-" if node else "_.-")) for c in value))


def bounded_json(value: object, maximum: int = 131072) -> None:
    try:
        if len(json.dumps(value, allow_nan=False)) > maximum:
            invalid("workflow_data_too_large")
    except (ValueError, TypeError, RecursionError):
        invalid("workflow_data_invalid")


def check_template(value: JsonValue, ancestors: set[str] | None,
                   node_ids: set[str], loop: bool = False, depth: int = 0) -> None:
    if depth > 24:
        invalid("workflow_mapping_depth")
    if isinstance(value, dict):
        if "$literal" in value:
            if set(value) != {"$literal"}:
                invalid()
            return
        if "$ref" in value:
            if not set(value).issubset({"$ref", "default"}) or not isinstance(value["$ref"], str):
                invalid()
            parts = value["$ref"].split(".")
            if not all(parts) or len(value["$ref"]) > 512 or len(parts) > 32:
                invalid()
            if parts[0] == "nodes":
                if (len(parts) < 3 or parts[2] != "output" or parts[1] not in node_ids
                        or (ancestors is not None and parts[1] not in ancestors)):
                    invalid("workflow_mapping_reference")
            elif parts[0] not in ({"input", "result", "iteration"} if loop else {"input"}):
                invalid("workflow_mapping_reference")
            return
        for item in value.values():
            check_template(item, ancestors, node_ids, loop, depth + 1)
    elif isinstance(value, list):
        for item in value:
            check_template(item, ancestors, node_ids, loop, depth + 1)


def check_predicate(value: object, ancestors: set[str], ids: set[str], loop: bool = False) -> None:
    if not isinstance(value, dict) or set(value) != {"left", "op", "right"}:
        invalid("workflow_predicate_invalid")
    if value["op"] not in ("eq", "ne", "gt", "gte", "lt", "lte"):
        invalid("workflow_predicate_invalid")
    check_template(value["left"], ancestors, ids, loop)
    check_template(value["right"], ancestors, ids, loop)


def validate_definition(definition: WorkflowDefinition, data: WorkflowDataValidator) -> None:
    bounded_json(asdict(definition))
    try:
        ResourceBudget(**definition.resource_limits)
        if any(not key.startswith("max_") for key in definition.resource_limits):
            invalid("resource_limits_invalid")
    except (ValueError, TypeError):
        invalid("resource_limits_invalid")
    if (not identifier(definition.ref.id) or not identifier(definition.ref.version)
            or not isinstance(definition.name, str) or not definition.name.strip() or len(definition.name) > 128):
        invalid()
    for value, maximum in ((definition.max_concurrency, 16), (definition.max_node_executions, 512),
                           (definition.max_subworkflow_depth, 8)):
        if type(value) is not int or not 1 <= value <= maximum:
            invalid()
    if (isinstance(definition.timeout_seconds, bool) or not isinstance(definition.timeout_seconds, (int, float))
            or not 0 < definition.timeout_seconds <= 300 or definition.error_policy not in ("continue", "fail_fast")):
        invalid()
    if not 1 <= len(definition.nodes) <= 64 or len(definition.edges) > 256:
        invalid()
    if definition.scheduling_strategy not in ("fixed", "round_robin", "caas", "adaptive_caas"):
        invalid("scheduling_strategy_invalid")
    ids = {node.id for node in definition.nodes}
    if len(ids) != len(definition.nodes) or not all(identifier(key, True) for key in ids):
        invalid("workflow_duplicate_node")
    incoming: dict[str, list[str]] = {key: [] for key in ids}
    seen: set[tuple[str, str]] = set()
    kinds = {node.id: node.kind for node in definition.nodes}
    specs = {node.id: node for node in definition.nodes}
    for edge in definition.edges:
        if edge.source not in ids or edge.target not in ids or (edge.source, edge.target) in seen:
            invalid("workflow_edge_invalid")
        seen.add((edge.source, edge.target))
        incoming[edge.target].append(edge.source)
        if (kinds[edge.source] == NodeKind.CONDITION and edge.branch not in {"true", "false"}) or (
                kinds[edge.source] != NodeKind.CONDITION and edge.branch is not None
                and not (edge.branch == "fallback" and specs[edge.source].config.get("recovery", {}).get("fallback_node") == edge.target)) :
            invalid("workflow_branch_invalid")
    ancestors: dict[str, set[str]] = {}
    while len(ancestors) < len(ids):
        candidates = [key for key in ids if key not in ancestors and all(p in ancestors for p in incoming[key])]
        if not candidates:
            invalid("workflow_cycle")
        for key in candidates:
            ancestors[key] = set(incoming[key])
            for parent in incoming[key]:
                ancestors[key].update(ancestors[parent])
    configs = {
        NodeKind.TASK: {"agent_id", "agent_version", "output_mode", "limits", "candidate_agents", "task_type",
                        "required_skills", "preferred_skills", "required_tools", "priority", "scheduling_features",
                        "no_candidate_policy", "schedule_timeout_seconds", "verification", "recovery"},
        NodeKind.PARALLEL: {"join"}, NodeKind.CONDITION: {"predicate"},
        NodeKind.LOOP: {"workflow", "max_iterations", "until"},
        NodeKind.SUBWORKFLOW: {"workflow"},
    }
    for node in definition.nodes:
        config = node.config
        if node.kind not in configs or not isinstance(config, dict):
            invalid()
        if set(config) - (configs[node.kind] | {"input", "timeout_seconds", "on_error"}):
            invalid("workflow_config_invalid")
        timeout = config.get("timeout_seconds", definition.timeout_seconds)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300
                or config.get("on_error", definition.error_policy) not in ("continue", "fail_fast")):
            invalid()
        check_template(config.get("input", {"$ref": "input"}), ancestors[node.id], ids)
        if node.kind == NodeKind.TASK:
            try:
                TaskFeatures(**config.get('scheduling_features', {}))
            except (ValueError, TypeError):
                invalid('scheduling_features_invalid')
            if "recovery" in config and "verification" not in config:
                invalid("verification_required")
            if "verification" in config:
                ref = config["verification"]
                if not isinstance(ref, dict) or set(ref) != {"id", "version"} or not all(identifier(v) for v in ref.values()):
                    invalid("verification_reference_invalid")
                try:
                    recovery = recovery_from(config.get("recovery", {}))
                    if recovery.on_failure == "fallback" and not any(e.source == node.id and e.target == recovery.fallback_node and e.branch == "fallback" for e in definition.edges):
                        invalid("recovery_fallback_invalid")
                    if len(recovery.tool_options) > 15 or any(len(option) > 16 for option in recovery.tool_options):
                        invalid("recovery_tools_invalid")
                    for option in recovery.tool_options:
                        if any(not identifier(ref.id) or not identifier(ref.version) for ref in option):
                            invalid("recovery_tools_invalid")
                except (ValueError, TypeError, KeyError):
                    invalid("recovery_limits_invalid")
            fixed = "agent_id" in config
            if ((fixed and not identifier(config["agent_id"])) or
                    (fixed and "candidate_agents" in config) or
                    (not fixed and not any(key in config for key in ("candidate_agents", "task_type", "required_skills"))) or
                    (not fixed and "agent_version" in config) or
                    ("agent_version" in config and not identifier(config["agent_version"]))
                    or config.get("output_mode", "text") not in ("text", "json", "tool_result")):
                invalid()
            for key in ("candidate_agents", "required_skills", "preferred_skills"):
                items = config.get(key, [])
                if not isinstance(items, list) or len(items) > 64 or not all(identifier(item) for item in items) or len(set(items)) != len(items):
                    invalid("scheduling_requirements_invalid")
            if not identifier(config.get("task_type", "default")):
                invalid("scheduling_requirements_invalid")
            tools = config.get("required_tools", [])
            if not isinstance(tools, list) or len(tools) > 16:
                invalid("scheduling_requirements_invalid")
            for tool in tools:
                if not isinstance(tool, dict) or set(tool) != {"id", "version"} or not all(identifier(v) for v in tool.values()):
                    invalid("scheduling_requirements_invalid")
            priority = config.get("priority", 50)
            wait = config.get("schedule_timeout_seconds", 10)
            if (type(priority) is not int or not 0 <= priority <= 100
                    or isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 < wait <= 300
                    or config.get("no_candidate_policy", "fail") not in ("fail", "wait")):
                invalid("scheduling_requirements_invalid")
            try:
                RunLimits(**config.get("limits", {}))
            except (ValueError, TypeError):
                invalid("workflow_limits_invalid")
        elif node.kind == NodeKind.PARALLEL:
            if not incoming[node.id] or config.get("join") not in ("all", "all_selected"):
                invalid("workflow_join_invalid")
        elif node.kind == NodeKind.CONDITION:
            check_predicate(config.get("predicate"), ancestors[node.id], ids)
            labels = {edge.branch for edge in definition.edges if edge.source == node.id}
            if labels != {"true", "false"}:
                invalid("workflow_branch_invalid")
        if node.kind in {NodeKind.LOOP, NodeKind.SUBWORKFLOW}:
            ref = config.get("workflow")
            if not isinstance(ref, dict) or set(ref) != {"id", "version"} or not all(identifier(v) for v in ref.values()):
                invalid("workflow_reference_invalid")
        if node.kind == NodeKind.LOOP:
            count = config.get("max_iterations")
            if type(count) is not int or not 1 <= count <= 100:
                invalid("workflow_loop_invalid")
            check_predicate(config.get("until"), ancestors[node.id], ids, loop=True)
        bounded_json(config)
    for schema in (definition.input_schema, definition.output_schema):
        data.check_schema(schema)
    if not isinstance(definition.output_mapping, dict):
        invalid()
    check_template(definition.output_mapping, None, ids)


def resolve(value: JsonValue, context: dict[str, JsonValue]) -> JsonValue:
    if isinstance(value, dict):
        if "$literal" in value:
            return value["$literal"]
        if "$ref" in value:
            current: JsonValue = context
            try:
                for part in str(value["$ref"]).split("."):
                    if isinstance(current, dict):
                        current = current[part]
                    elif isinstance(current, list) and part.isdigit():
                        current = current[int(part)]
                    else:
                        raise KeyError
                return current
            except (KeyError, IndexError):
                if "default" in value:
                    return value["default"]
                raise RuntimeFault("workflow_mapping_missing", "Required mapped output is unavailable", 422) from None
        return {key: resolve(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    return value


def predicate(config: dict[str, JsonValue], context: dict[str, JsonValue]) -> bool:
    left, right = resolve(config["left"], context), resolve(config["right"], context)
    operation = config["op"]
    if operation in {"eq", "ne"}:
        equal = left == right and (isinstance(left, bool) == isinstance(right, bool))
        return equal if operation == "eq" else not equal
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in (left, right)):
        raise RuntimeFault("workflow_predicate_type", "Ordered comparisons require numbers", 422)
    if operation == "gt":
        return left > right
    if operation == "gte":
        return left >= right
    if operation == "lt":
        return left < right
    return left <= right
