"""Independent source-bound verification. Never augments a generated business artifact."""
import json
import re
from futurecup.adapters.source_binding import resolve, origin, error, VERSION
from futurecup.adapters.coverage import assess_output
from futurecup.adapters.rule_body import extract, inspect_rule, canonical, quantities, NEGATIVE
from futurecup.adapters.knowledge import terms
from futurecup.domain.verification import CheckEvidence


def conflict_code(conflicts):
    first=conflicts[0]
    expected,actual=first["expected_clause"],first["actual_clause"]
    a,b=quantities(expected),quantities(actual)
    if first["type"]=="NUMERIC_UNIT_CONFLICT":
        if {q[1] for q in a}!={q[1] for q in b}:return "unit_conflict"
        if {q[0] for q in a}!={q[0] for q in b}:return "numeric_conflict"
        return "comparison_conflict"
    return "negation_conflict" if bool(NEGATIVE.search(expected))!=bool(NEGATIVE.search(actual)) else "condition_conflict"


def source_evidence(value, records, digest, stage, parents):
    resolved, problems = resolve(value, records)
    for record in records:
        for t in record.get("coverage",[]):
            if t["required"] and (t["status"]!="COVERED" or t.get("search_state")!="QUERIED" or not t.get("injected_ids")):
                problems.append(error("knowledge_coverage_insufficient","retrieval",t["status"],"Required topic actually retrieved and injected",[t["id"]]))
    if not resolved:
        problems.append(error("required_source_missing","source_refs",None,"At least one actual resolved source"))
    expected = {k:v for k,v in resolved.items() if v["required"]}
    expected_origins = {origin(v) for v in expected.values()}
    if value.get("artifact_type") != stage:
        problems.append(error("knowledge_stage_mismatch","artifact_type",value.get("artifact_type"),stage))
    if stage != "requirements":
        requirements = [p for p in parents if p["value"].get("artifact_type")=="requirements"]
        designs = [p for p in parents if p["value"].get("artifact_type")=="design"]
        if len(requirements)!=1 or (stage=="delivery" and len(designs)!=1):
            problems.append(error("knowledge_upstream_type_mismatch","upstream",None,"Verified requirements/design stages"))
        for parent in parents:
            refs, faults = resolve(parent["value"],parent["records"])
            if faults or (parent["value"].get("artifact_type")=="requirements" and not expected_origins <= {origin(v) for v in refs.values()}):
                problems.append(error("knowledge_upstream_rule_mismatch","upstream",None,"Same immutable source origins in published upstream artifacts"))
    field = "requirements" if stage=="requirements" else "design_links" if stage=="design" else "rule_coverage"
    entries = value.get(field,[])
    seen = set()
    for i, item in enumerate(entries):
        path = f"{field}.{i}"
        if stage=="delivery":
            key=(item.get("topic_id"),item.get("clause_id"),item.get("chunk_id"))
            matching=[v for v in expected.values() if (v["topic_id"],v["clause_id"],v["chunk_id"])==key]
            if matching: seen.add(matching[0]["source_ref"])
            continue
        identifier=item.get("source_ref")
        if identifier not in resolved:
            problems.append(error("source_not_retrieved",path+".source_ref",identifier,"Validated current source reference"))
            continue
        source=resolved[identifier]
        if identifier in seen:
            problems.append(error("source_reference_duplicate",path+".source_ref",identifier,"One analysis/association per required clause"))
        seen.add(identifier)
        if stage=="requirements":
            # This is model business analysis, not a claimed verbatim quote.
            body=extract({"markdown":item.get("analysis","")})
            topic=next(t for r in records for t in r["coverage"] if t["id"]==source["topic_id"])
            check=inspect_rule(source["quote"],topic["query"],body,terms)
            if check["conflicts"]:
                problems.append(error(conflict_code(check["conflicts"]),path+".analysis",item.get("analysis"),
                                      {"clause":source["quote"],"conflicts":check["conflicts"]},[source["topic_id"]]))
            elif not any(canonical(source["quote"]) in f["normalized"] for f in body["fragments"]):
                problems.append(error("semantic_uncertain",path+".analysis",item.get("analysis"),
                                      "A substantive assertion preserving the complete source constraint; generic acknowledgement is insufficient",[source["topic_id"]]))
        elif stage=="design":
            body=extract(value);quote=canonical(item.get("body_quote",""))
            if not quote or not any(quote in f["normalized"] for f in body["fragments"]):
                problems.append(error("knowledge_design_link_invalid",path+".body_quote",item.get("body_quote"),"Actual visible design passage",[source["topic_id"]]))
    for identifier,source in expected.items():
        if identifier not in seen:
            problems.append(error("required_clause_missing",field,identifier,source["quote"],[source["topic_id"]]))
    # Relevant conflict guards run on actual model prose even when valid source references exist.
    if stage=="design":
        body=extract(value)
        for r in records:
            for t in r["coverage"]:
                if t.get("critical_rule"):
                    check=inspect_rule(t["critical_rule"],t["query"],body,terms)
                    if check["conflicts"]:
                        problems.append(error(conflict_code(check["conflicts"]),"markdown",check["conflicts"],t["critical_rule"],[t["id"]]))
    body_checks=[]
    if stage=="delivery":
        keys={(v["topic_id"],v["chunk_id"]) for v in resolved.values()}
        body_checks=assess_output(value,[{**r,"rule_contract":"atomic-body-v1"} for r in records],source_keys=keys)
        for c in body_checks:
            if c["status"] not in ("RULE_REFLECTED","SOURCE_UNAVAILABLE_ADVICE"):
                problems.append(error(conflict_code(c["evidence"]["conflicts"]) if c["evidence"].get("conflicts") else c["validation_type"].lower(),"markdown",c["evidence"],c["expected_rule"],[c["topic_id"]]))
    # Count is authoritative server metadata, never repeated model output.
    count=len(entries)
    for match in re.finditer(r"(?:共\s*(\d+)\s*条|\b(\d+)\s+(?:rules|requirements)\b)",value.get("summary",""),re.I):
        actual=int(next(x for x in match.groups() if x is not None))
        if actual!=count:
            problems.append(error("summary_count_mismatch","summary",actual,count))
    unique={}
    for p in problems:
        key=json.dumps({k:p[k] for k in ("code","actual","expected")},sort_keys=True,ensure_ascii=False)
        if key in unique:
            old=unique[key];old["paths"].append(p["path"])
            old["affected_topics"]=sorted(set(old["affected_topics"]+p["affected_topics"]))
        else: unique[key]={**p,"paths":[p["path"]]}
    problems=list(unique.values())
    details={"verification_contract":VERSION,"stage":stage,"errors":problems,
        "resolved_sources":list(resolved.values()),"computed_counts":{field:count,"sources":len(resolved)},
        "final_body_checked":stage=="delivery","body_checks":body_checks,
        "repair_hint":"Repair only these exact field differences. Source references are evidence, not your business answer. Optional direct quotes remain verbatim; omit them when only source attribution is needed."}
    return CheckEvidence("external_tests","FAILED" if problems else "PASSED",
        problems[0]["code"] if problems else "source_bound_stage_valid",reference=digest,details=details)
