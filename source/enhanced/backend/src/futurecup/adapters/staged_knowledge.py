"""Role-specific independent knowledge verification; no model self-grading."""
from __future__ import annotations

from futurecup.adapters.coverage import knowledge_evidence
from futurecup.adapters.knowledge import check_citations, terms
from futurecup.adapters.rule_body import canonical, extract, inspect_rule
from futurecup.adapters.rule_contract import atoms
from futurecup.domain.verification import CheckEvidence


def expected_rules(records: list[dict]) -> tuple[dict, list[dict]]:
    expected, errors = {}, []
    for record in records:
        hits = {h["id"]: h for h in record["hits"]}
        for topic in record.get("coverage", []):
            if not topic["required"]:
                continue
            chunk, rule = topic.get("rule_chunk_id"), topic.get("critical_rule")
            if (topic["status"] != "COVERED" or topic.get("search_state") != "QUERIED"
                    or chunk not in hits or not rule or rule not in hits[chunk]["text"]):
                errors.append({"topic_id": topic["id"], "code": "knowledge_coverage_insufficient"})
                continue
            for clause in atoms(rule, topic["query"]):
                key = (topic["id"], clause["id"], chunk)
                expected[key] = clause["text"]
    if not expected:
        errors.append({"code": "knowledge_coverage_insufficient"})
    return expected, errors


def source_statement_matches(actual: str, expected: str, source_text: str) -> bool:
    """A complete clause, optionally including its real source sentence terminator."""
    if not isinstance(actual,str):
        return False
    if canonical(actual)==canonical(expected):
        return True
    # Source substring proof is mandatory for extra punctuation; never remove protected content.
    return actual in source_text and canonical(actual).rstrip("。.;；")==canonical(expected)


def staged_evidence(value: dict, records: list[dict], digest: str, stage: str,
                    upstream: dict) -> CheckEvidence:
    """Upstream is authoritative published workflow output, checked separately against input."""
    expected, errors = expected_rules(records)
    if value.get("artifact_type") != stage:
        errors.append({"code": "knowledge_stage_mismatch", "expected_stage": stage})
    if not check_citations(value, records):
        errors.append({"code": "knowledge_citation_invalid"})
    hits = {h["id"]: h for r in records for h in r["hits"]}
    for r in records:
        for t in r.get("coverage", []):
            if t["required"] and t.get("critical_rule") and not any(
                    c.get("chunk_id") == t["rule_chunk_id"] and t["critical_rule"] in c.get("quote", "")
                    and c["quote"] in hits[t["rule_chunk_id"]]["text"]
                    for c in value.get("citations", []) if isinstance(c, dict) and t["rule_chunk_id"] in hits):
                errors.append({"topic_id": t["id"], "code": "knowledge_citation_invalid"})

    requirements = [v for v in upstream.values() if v.get("artifact_type") == "requirements"]
    designs = [v for v in upstream.values() if v.get("artifact_type") == "design"]
    if stage != "requirements":
        if len(requirements) != 1 or (stage == "delivery" and len(designs) != 1):
            errors.append({"code": "knowledge_upstream_type_mismatch"})
        source_rules = {
            (x.get("topic_id"), x.get("clause_id"), x.get("chunk_id")): x.get("statement")
            for v in requirements for x in v.get("requirements", [])
        }
        for key, statement in expected.items():
            if not source_statement_matches(source_rules.get(key), statement, hits[key[2]]["text"]):
                errors.append({"topic_id": key[0], "clause_id": key[1], "code": "knowledge_upstream_rule_mismatch"})

    if stage == "requirements":
        supplied = value.get("requirements", [])
        seen = set()
        for item in supplied:
            key = (item.get("topic_id"), item.get("clause_id"), item.get("chunk_id"))
            if key in seen or key not in expected or not source_statement_matches(item.get("statement"), expected[key], hits[key[2]]["text"]):
                errors.append({"topic_id": key[0], "clause_id": key[1], "code": "knowledge_extraction_invalid",
                               "expected": expected.get(key), "actual": item.get("statement")})
            seen.add(key)
        for key in expected.keys() - seen:
            errors.append({"topic_id": key[0], "clause_id": key[1], "code": "knowledge_extraction_missing",
                           "expected": expected[key]})
    elif stage == "design":
        body = extract(value)
        seen = set()
        for link in value.get("design_links", []):
            key = (link.get("topic_id"), link.get("clause_id"), link.get("chunk_id"))
            quote = canonical(link.get("body_quote", ""))
            if key in seen or key not in expected or not quote or not any(quote in f["normalized"] for f in body["fragments"]):
                errors.append({"topic_id": key[0], "clause_id": key[1], "code": "knowledge_design_link_invalid"})
            seen.add(key)
        for key in expected.keys() - seen:
            errors.append({"topic_id": key[0], "clause_id": key[1], "code": "knowledge_design_link_missing"})
        # Check actual design prose, not source statements or the fact that a link exists.
        for r in records:
            for t in r.get("coverage", []):
                if t.get("critical_rule"):
                    check = inspect_rule(t["critical_rule"], t["query"], body, terms)
                    if check["conflicts"]:
                        errors.append({"topic_id": t["id"], "code": "knowledge_rule_conflict",
                                       "conflicts": check["conflicts"][:2]})
    elif stage == "delivery":
        # Never let the structured list replace final prose: use the unchanged strict body verifier.
        checked = knowledge_evidence(value, [{**r, "rule_contract": "atomic-body-v1"} for r in records], digest)
        if checked.status != "PASSED":
            errors.extend({"code": x} for x in checked.details["reason_codes"])
        body_evidence = checked.details
    else:
        errors.append({"code": "knowledge_stage_unknown"})
    details = {"verification_contract": "staged-rules-v1", "stage": stage, "errors": errors,
               "final_body_checked": stage == "delivery",
               "repair_hint": {
                   "requirements": "Repair only the listed missing/incorrect complete source clauses in requirements. Do not generate final prose or body_quote at this extraction stage.",
                   "design": "Use actual upstream requirements unchanged. Link each relevant clause to a real architecture decision in visible markdown; remove actual conflicts. Links alone do not prove design correctness.",
                   "delivery": "Repair listed clauses in operational final markdown, not source/reference columns. Preserve complete conditions, values and negation; bind exact body_quote. Requirements lists never replace final prose."
               }.get(stage, "Use the declared server stage.")}
    if stage == "delivery":
        details["body_verification"] = body_evidence
    return CheckEvidence("external_tests", "FAILED" if errors else "PASSED",
                         errors[0]["code"] if errors else "knowledge_stage_valid", reference=digest, details=details)


def staged_check(workflows, safety, runtime):
    async def check(artifact):
        node_id = artifact.ref.id.rsplit(":", 1)[0]
        session = await safety.session(node_id)
        attempt = await safety.attempt(session["current_attempt"])
        report = await workflows.get_report(session["run_id"])
        node = next(n for n in report.nodes if n.id == node_id)
        root = node.input
        # The immutable resolved workflow input, not a model-declared stage, selects the verifier.
        if attempt["artifact"]["digest"] != artifact.digest or attempt["artifact"]["content"] != artifact.content:
            return CheckEvidence("external_tests", "ERROR", "workspace_binding_error")
        upstream = root.get("upstream", {})
        for key, output in upstream.items():
            parent = next((n for n in report.nodes if n.node_id == key), None)
            if parent is None or not parent.verified or parent.state.value != "succeeded" or parent.output != output:
                return CheckEvidence("external_tests", "ERROR", "knowledge_upstream_binding_error")
        execution = await runtime.get(attempt["execution_id"])
        if root.get("rule_contract")=="staged-rules-v2":
            from futurecup.adapters.source_verification import source_evidence
            parents=[]
            for key,output in upstream.items():
                parent=next(n for n in report.nodes if n.node_id==key)
                found=None
                for identifier in parent.attempt_ids:
                    prior=await safety.attempt(identifier)
                    if prior.get("artifact") and prior["artifact"]["content"]==output and prior["artifact"]["ref"]=={"id":parent.current_artifact_ref.id,"version":parent.current_artifact_ref.version}:
                        found=prior
                if found is None:
                    return CheckEvidence("external_tests","ERROR","knowledge_upstream_binding_error")
                if any(r.get("source_scope")!=found["execution_id"] for r in found["retrieval_records"]):
                    return CheckEvidence("external_tests","ERROR","source_snapshot_invalid")
                parents.append({"value":output,"records":found["retrieval_records"]})
            if any(r.get("source_scope")!=attempt["execution_id"] for r in execution.retrieval_records):
                return CheckEvidence("external_tests","ERROR","source_snapshot_invalid")
            return source_evidence(artifact.content,execution.retrieval_records,artifact.digest,root["knowledge_stage"],parents)
        return staged_evidence(artifact.content, execution.retrieval_records, artifact.digest,
                               root["knowledge_stage"], upstream)
    return check
