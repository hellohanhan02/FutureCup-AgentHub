"""09-C frozen scheduling problem, offline feasibility proof and independent checks.

The solver is used only for configuration feasibility, never to replace model
output. Runtime checks inspect the candidate and return violations, not a plan.
"""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from itertools import permutations
import json
from pathlib import Path
from typing import Awaitable, Callable

from futurecup.application.ports import WorkflowRepository, SafetyRepository
from futurecup.domain.models import JsonValue, TaskState
from futurecup.domain.verification import ArtifactVersion, CheckEvidence

DEFINITION = Path(__file__).resolve().parents[4] / "config/deepseek-selfloop09c.json"
GROUPS = ("instance", "membership", "dependencies", "priority", "adjacency", "windows", "completion", "gap")
CHECK_IDS = tuple("order09c-" + group + "-v2" for group in GROUPS)
HINTS = {
    "membership": "Use each input task exactly once and match total to the actual order length.",
    "dependencies": "Move each listed prerequisite before its dependent without dropping tasks.",
    "priority": "Respect the listed priority pairs; do not substitute a global priority sort.",
    "adjacency": "Keep the listed pair consecutive in the specified direction.",
    "windows": "Move each violating task into its inclusive one-based position window.",
    "completion": "Move the constrained task earlier and recompute cumulative completion, preserving all other constraints.",
    "gap": "Increase the listed position difference to the required minimum without violating other constraints.",
}


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Problem:
    serialized: str
    tasks: tuple[tuple[str, int, int], ...]
    dependencies: tuple[tuple[str, str], ...]
    priority: tuple[tuple[str, str], ...]
    adjacency: tuple[tuple[str, str], ...]
    windows: tuple[tuple[str, int, int], ...]
    completion: tuple[tuple[str, int], ...]
    gaps: tuple[tuple[str, str, int], ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(t[0] for t in self.tasks)

    @property
    def spec(self) -> dict:
        return json.loads(self.serialized)


def problem_from(spec: dict) -> Problem:
    """Bound all enumeration/data sizes; refuse ambiguous or unknown semantics."""
    def integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid challenge integer")
        return value
    if (not isinstance(spec, dict) or set(spec) != {"scenario_id", "semantics", "tasks", "constraints"}
            or spec["scenario_id"] != "dependency-schedule-09c-v1"
            or spec["semantics"] != {"position_base": 1, "single_machine": True, "idle_allowed": False}
            or type(spec["semantics"]["position_base"]) is not int
            or type(spec["semantics"]["single_machine"]) is not bool
            or type(spec["semantics"]["idle_allowed"]) is not bool
            or not isinstance(spec["tasks"], list) or not 6 <= len(spec["tasks"]) <= 8):
        raise ValueError("Invalid challenge specification")
    tasks = []
    for task in spec["tasks"]:
        if (not isinstance(task, dict) or set(task) != {"id", "duration", "priority"}
                or task["id"] not in tuple("ABCDEFGH")):
            raise ValueError("Invalid challenge task")
        tasks.append((task["id"], integer(task["duration"], 1, 9), integer(task["priority"], 0, 9)))
    ids = tuple(t[0] for t in tasks)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate challenge task")
    constraints = spec["constraints"]
    if not isinstance(constraints, dict) or set(constraints) != {
        "dependencies", "priority_before", "immediately_before", "position_windows", "completion_deadlines", "min_gaps"}:
        raise ValueError("Invalid challenge constraints")
    if any(not isinstance(v, list) or len(v) > 16 for v in constraints.values()):
        raise ValueError("Unbounded challenge constraints")
    def pairs(name):
        result = []
        for pair in constraints[name]:
            if not isinstance(pair, list) or len(pair) != 2 or any(p not in ids for p in pair) or pair[0] == pair[1]:
                raise ValueError("Invalid challenge pair")
            result.append(tuple(pair))
        return tuple(result)
    dependencies, priority, adjacency = pairs("dependencies"), pairs("priority_before"), pairs("immediately_before")
    priorities = {t[0]: t[2] for t in tasks}
    if any(priorities[a] <= priorities[b] for a, b in priority):
        raise ValueError("Priority pair must put higher priority first")
    windows, completion, gaps = [], [], []
    for row in constraints["position_windows"]:
        if not isinstance(row, dict) or set(row) != {"task", "min", "max"} or row["task"] not in ids:
            raise ValueError("Invalid position window")
        low = integer(row["min"], 1, len(ids))
        windows.append((row["task"], low, integer(row["max"], low, len(ids))))
    for row in constraints["completion_deadlines"]:
        if not isinstance(row, dict) or set(row) != {"task", "latest"} or row["task"] not in ids:
            raise ValueError("Invalid completion deadline")
        completion.append((row["task"], integer(row["latest"], 0, 100)))
    for row in constraints["min_gaps"]:
        if (not isinstance(row, dict) or set(row) != {"before", "after", "min_gap"}
                or row["before"] not in ids or row["after"] not in ids or row["before"] == row["after"]):
            raise ValueError("Invalid position gap")
        gaps.append((row["before"], row["after"], integer(row["min_gap"], 1, len(ids))))
    return Problem(json.dumps(spec, sort_keys=True, separators=(",", ":")), tuple(tasks), dependencies,
                   priority, adjacency, tuple(windows), tuple(completion), tuple(gaps))


def violations(problem: Problem, order: tuple[str, ...] | list[str]) -> dict[str, list[dict[str, JsonValue]]]:
    if len(order) != len(problem.ids) or set(order) != set(problem.ids):
        raise ValueError("Candidate must be a task permutation")
    position = {task: i + 1 for i, task in enumerate(order)}
    durations = {t[0]: t[1] for t in problem.tasks}
    finished, clock = {}, 0
    for task in order:
        clock += durations[task]
        finished[task] = clock
    result = {name: [] for name in GROUPS[2:]}
    for group, pairs in (("dependencies", problem.dependencies), ("priority", problem.priority)):
        for a, b in pairs:
            if position[a] >= position[b]:
                result[group].append({"before": a, "after": b, "before_position": position[a], "after_position": position[b]})
    for a, b in problem.adjacency:
        if position[b] != position[a] + 1:
            result["adjacency"].append({"before": a, "after": b, "required_gap": 1, "actual_gap": position[b] - position[a]})
    for task, low, high in problem.windows:
        if not low <= position[task] <= high:
            result["windows"].append({"task": task, "min": low, "max": high, "actual_position": position[task]})
    for task, latest in problem.completion:
        if finished[task] > latest:
            result["completion"].append({"task": task, "latest": latest, "actual_completion": finished[task]})
    for a, b, gap in problem.gaps:
        if position[b] - position[a] < gap:
            result["gap"].append({"before": a, "after": b, "min_gap": gap, "actual_gap": position[b] - position[a]})
    return result


def feasibility(problem: Problem) -> tuple[int, tuple[str, ...] | None]:
    count, witness = 0, None
    for candidate in permutations(problem.ids):
        if not any(violations(problem, candidate).values()):
            count += 1
            if witness is None:
                witness = candidate
    return count, witness


def output_schema(problem: Problem) -> dict:
    return {"type": "object", "required": ["task", "order", "total"], "additionalProperties": False,
        "properties": {"task": {"const": "dependency_schedule"}, "order": {"type": "array",
            "minItems": len(problem.ids), "maxItems": len(problem.ids), "uniqueItems": True,
            "items": {"type": "string", "enum": list(problem.ids)}}, "total": {"type": "integer"}}}


def input_schema(problem: Problem) -> dict:
    return {"type": "object", "required": ["task", "instructions", "spec"], "additionalProperties": False,
        "properties": {"task": {"const": "dependency_schedule"}, "instructions": {"type": "string", "minLength": 1, "maxLength": 1024},
                       "spec": {"const": problem.spec}}}


def check_definition(path: Path) -> tuple[Problem, dict]:
    value = json.loads(path.read_text("utf-8"))
    problem = problem_from(value["input"]["spec"])
    expected = [{"kind": "json_schema", "config": {"schema": output_schema(problem)}}] + [
        {"kind": "external_tests", "config": {"test_id": key}} for key in CHECK_IDS]
    if value["policy"]["checks"] != expected or value["workflow"]["input_schema"] != input_schema(problem):
        raise ValueError("Challenge verifier or input schema drift")
    count, witness = feasibility(problem)
    if count == 0:
        raise ValueError("Unsatisfiable challenge")
    return problem, {"scenario_id": problem.spec["scenario_id"], "spec_sha256": fingerprint(problem.spec),
        "feasible": True, "solution_count": count, "check_ids": list(CHECK_IDS), "verifier_version": 2}


@lru_cache(maxsize=1)
def frozen_definition() -> tuple[Problem, dict]:
    return check_definition(DEFINITION)


class OrderChallengeChecks:
    def __init__(self, workflows: WorkflowRepository, safety: SafetyRepository) -> None:
        self.workflows, self.safety = workflows, safety
        self.problem, self.summary = frozen_definition()

    @property
    def callbacks(self) -> dict[str, Callable[[ArtifactVersion], Awaitable[CheckEvidence]]]:
        def callback(group):
            async def run(artifact):
                return await self.check(group, artifact)
            return run
        return dict(zip(CHECK_IDS, (callback(group) for group in GROUPS)))

    async def check(self, group: str, artifact: ArtifactVersion) -> CheckEvidence:
        def evidence(status, reason, path, details=None):
            if status == "FAILED" and details is not None:
                details = {"repair_hint": HINTS.get(group, HINTS["membership"]), **details}
            return CheckEvidence("external_tests", status, reason, path, artifact.digest, "order09c_" + group, details=details)
        if group == "instance":
            try:
                node_id, marker = artifact.ref.id.rsplit(":", 1)
                session = await self.safety.session(node_id)
                report = await self.workflows.get_report(session["run_id"])
                node = next(n for n in report.nodes if n.id == node_id)
                attempt = await self.safety.attempt(session["current_attempt"])
                saved = attempt.get("artifact") or {}
                if (marker != "artifact" or session["state"] != "running" or node.state != TaskState.VERIFYING
                        or attempt["state"] != "running" or attempt["run_id"] != report.run.id
                        or attempt["node_id"] != node_id or node.execution_id != attempt["execution_id"]
                        or saved.get("ref") != {"id": artifact.ref.id, "version": artifact.ref.version}
                        or saved.get("digest") != artifact.digest or saved.get("content") != artifact.content
                        or fingerprint(report.input["spec"]) != self.summary["spec_sha256"] or node.input != report.input):
                    return evidence("ERROR", "order09c_instance_unbound", "spec")
                return evidence("PASSED", "order09c_instance_matched", "spec")
            except (KeyError, TypeError, ValueError, StopIteration):
                return evidence("ERROR", "order09c_instance_unavailable", "spec")
        value = artifact.content
        order = value.get("order") if isinstance(value, dict) else None
        strings = [x for x in order if isinstance(x, str)] if isinstance(order, list) else []
        missing = sorted(set(self.problem.ids) - set(strings))
        duplicates = sorted({x for x in strings if strings.count(x) > 1})
        illegal = sorted(set(strings) - set(self.problem.ids))
        permutation = (isinstance(order, list) and len(strings) == len(order) == len(self.problem.ids)
                       and not missing and not duplicates and not illegal)
        if group == "membership":
            if not permutation:
                return evidence("FAILED", "order09c_not_exact_task_permutation", "order", {
                    "missing_tasks": missing, "duplicate_tasks": duplicates, "illegal_tasks": illegal,
                    "invalid_task_positions": [i + 1 for i, x in enumerate(order) if not isinstance(x, str)]
                        if isinstance(order, list) else [],
                    "expected_count": len(self.problem.ids), "actual_count": len(order) if isinstance(order, list) else None})
            if (set(value) != {"task", "order", "total"} or value.get("task") != "dependency_schedule"):
                return evidence("FAILED", "order09c_output_contract_violation", "task",
                                {"repair_hint": "Use exactly task, order, total; task must be dependency_schedule."})
            if type(value.get("total")) is not int or value["total"] != len(order):
                return evidence("FAILED", "order09c_total_mismatch", "total", {
                    "expected_total": len(order), "actual_total": value.get("total"),
                    "repair_hint": "Set total to the number of task IDs, not cumulative duration or finish time."})
            return evidence("PASSED", "order09c_membership_matched", "order")
        if not permutation:
            # Existing verdicts have no NOT_EVALUATED. INCONCLUSIVE is fail-closed;
            # the independently failed membership check determines overall FAILED.
            return evidence("INCONCLUSIVE", "order09c_not_evaluated_precondition", "order",
                            {"evaluation": "NOT_EVALUATED", "blocked_by": "order09c_membership"})
        failures = violations(self.problem, order)[group]
        return evidence("FAILED" if failures else "PASSED", "order09c_" + group + ("_violation" if failures else "_matched"),
                        "order", {"violations": failures} if failures else None)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-definition", type=Path, required=True)
    args = parser.parse_args()
    try:
        _, summary = check_definition(args.check_definition)
    except (ValueError, TypeError, KeyError, OSError):
        print(json.dumps({"feasible": False, "error": "invalid_or_unsatisfiable_definition"}))
        raise SystemExit(1)
    print(json.dumps(summary))  # Deliberately excludes the solver witness.
