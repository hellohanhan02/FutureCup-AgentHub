"""Atomic source constraints linked to independently visible deliverable assertions."""
from __future__ import annotations
import hashlib
import re
from futurecup.adapters.rule_body import canonical, excerpt




def atoms(rule: str, query: str | None = None) -> list[dict]:
    from futurecup.adapters.rule_body import MODAL
    label, sep, content = rule.partition("：")
    if not sep: label, sep, content = rule.partition(":")
    conditional=re.search(r"如果|仅当|除非|未|已|之前|之后|\b(?:if|unless|when)\b",label,re.I)
    if not sep or query is None or canonical(label)!=canonical(query) or conditional:
        content=rule
    pieces=list(re.finditer(r"[^，,；;。!?！？]+",content))
    # Never detach a condition-only prefix from its consequent.
    if len(pieces)>1 and not all(MODAL.search(m.group()) or "只能" in m.group() for m in pieces):
        pieces=[re.match(r"[\s\S]+",content)]
    result=[]
    for match in pieces:
        clause=match.group().strip()
        if clause:
            result.append({"id":hashlib.sha256(canonical(clause).encode()).hexdigest()[:20],
                "text":clause,"start":rule.find(content)+match.start(),"end":rule.find(content)+match.end()})
    return result


def binding_proof(topic: dict, value: dict, body: dict, strict: bool) -> dict:
    expected=atoms(topic["critical_rule"],topic["query"])
    links=value.get("rule_coverage")
    links=links if isinstance(links,list) else []
    evidence=[]
    for clause in expected:
        wanted=canonical(clause["text"])
        eligible=[f for f in body["fragments"] if wanted in f["normalized"]]
        matching=[link for link in links if isinstance(link,dict)
                  and link.get("topic_id")==topic["id"] and link.get("clause_id")==clause["id"]
                  and link.get("chunk_id")==topic["rule_chunk_id"] and isinstance(link.get("body_quote"),str)]
        bound=[f for f in eligible if any(canonical(link["body_quote"]) in f["normalized"]
                    and wanted in canonical(link["body_quote"]) for link in matching)]
        witnesses=bound if strict else eligible
        evidence.append({"clause_id":clause["id"],"expected":clause["text"],
                         "status":"BOUND" if witnesses else "UNPROVEN",
                         "body":[excerpt(f) for f in witnesses[:1]],
                         "difference":None if witnesses else "No exact complete atomic assertion linked to actual visible body"})
    # Do not accept invented clause IDs or bindings to excluded/absent text.
    allowed={a["id"] for a in expected}
    invalid=[x for x in links if isinstance(x,dict) and x.get("topic_id")==topic["id"]
             and (x.get("clause_id") not in allowed or x.get("chunk_id")!=topic["rule_chunk_id"])]
    return {"version":"atomic-body-v1","mode":"explicit_links" if strict else "historical_body_only",
            "passed":bool(evidence) and all(x["status"]=="BOUND" for x in evidence) and not invalid,
            "clauses":evidence,"invalid_bindings":len(invalid)}
