"""Frozen 09-B business rules: inspect output, never generate or edit it."""

from futurecup.domain.verification import ArtifactVersion, CheckEvidence


async def verify_priority_plan(artifact: ArtifactVersion) -> CheckEvidence:
    def fail(reason: str, path: str, rule: str) -> CheckEvidence:
        return CheckEvidence("external_tests", "FAILED", reason, path, artifact.digest, rule)
    value = artifact.content
    if not isinstance(value, dict):
        return fail("priority_plan_object_required", "", "type")
    for name in ("task", "items", "total"):
        if name not in value:
            return fail("priority_plan_missing_field", name, "required")
    if set(value) != {"task", "items", "total"}:
        return fail("priority_plan_extra_fields", "", "additionalProperties")
    if value["task"] != "priority_plan":
        return fail("priority_plan_wrong_task", "task", "const")
    items = value["items"]
    if not isinstance(items, list) or len(items) != 3:
        return fail("priority_plan_item_count", "items", "exactly_three")
    ids, priorities = [], []
    for index, item in enumerate(items):
        path = "items." + str(index)
        if not isinstance(item, dict) or set(item) != {"id", "priority"}:
            return fail("priority_plan_item_fields", path, "required_and_no_extras")
        if not isinstance(item["id"], str) or item["id"] not in {"A", "B", "C"}:
            return fail("priority_plan_item_id", path + ".id", "enum")
        if type(item["priority"]) is not int:
            return fail("priority_plan_priority_type", path + ".priority", "integer")
        ids.append(item["id"])
        priorities.append(item["priority"])
    if sorted(ids) != ["A", "B", "C"]:
        return fail("priority_plan_duplicate_or_missing_id", "items", "id_permutation")
    if sorted(priorities) != [1, 2, 3]:
        return fail("priority_plan_priorities_not_permutation", "items", "priority_permutation")
    if type(value["total"]) is not int or value["total"] != len(items):
        return fail("priority_plan_total_mismatch", "total", "total_matches_item_count")
    return CheckEvidence("external_tests", "PASSED", "priority_plan_all_rules_matched", reference=artifact.digest)
