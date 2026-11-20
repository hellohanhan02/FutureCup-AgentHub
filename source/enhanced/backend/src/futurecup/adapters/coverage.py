"""Bounded lexical retrieval and conservative rule-reflection checks (no model calls).

COVERED means a strongly matching normative sentence was injected, never semantic
completeness. Only one key rule per topic is selected; arbitrary inference is not
verified. Required topics may be supplied by the caller, not by source documents.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from futurecup.domain.errors import RuntimeFault

Hit = dict[str, Any]
Search = Callable[[Sequence[str], str, int], list[Hit]]
Tokenizer = Callable[[str], list[str]]
RULE = re.compile(r"\b(?:must|shall|required|prohibited)\b|\u5fc5\u987b|\u4e0d\u5f97|\u7981\u6b62|\u5e94\u5f53|\u4e0d\u5141\u8bb8|\u5141\u8bb8|\u4e0d\u80fd", re.I)
QUANTITY = re.compile(r"(\d+(?:\.\d+)?)\s*(minutes?|hours?|days?|\u5206\u949f|\u5c0f\u65f6|\u5929)", re.I)


def identify(query: str, *, limit: int = 8) -> list[dict]:
    """Language-independent clause extraction, not LLM topic understanding.

    Explicit topics are recommended for compound clauses without separators.
    Long unseparated clauses become bounded windows instead of silently truncating.
    """
    pieces = re.split(r"[\n,;\u3001\uff0c\uff1b\u3002]+|\b(?:and|also)\b|\u4ee5\u53ca|\u5e76\u4e14|\u540c\u65f6", query, flags=re.I)
    values: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        for start in range(0, len(piece), 140):
            text = piece[start:start+160]
            if len(text) >= 2 and text not in values:
                values.append(text)
    return [{"id": f"topic-{i+1}", "query": text, "required": False}
            for i, text in enumerate(values[:limit])]


def validate_topics(topics: object) -> list[dict]:
    if not isinstance(topics, list) or not 1 <= len(topics) <= 8:
        raise RuntimeFault("coverage_topics", "Provide 1..8 bounded topics", 422)
    seen = set()
    result = []
    for topic in topics:
        if (not isinstance(topic, dict) or set(topic)-{"id", "query", "required"}
            or not isinstance(topic.get("id"), str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", topic["id"])
            or topic["id"] in seen or not isinstance(topic.get("query"), str)
            or not 2 <= len(topic["query"].strip()) <= 160
            or type(topic.get("required", False)) is not bool):
            raise RuntimeFault("coverage_topics", "Invalid or duplicate coverage topic", 422)
        seen.add(topic["id"])
        result.append({"id": topic["id"], "query": topic["query"].strip(), "required": topic.get("required", False)})
    return result



def _rules(hit: Hit, tokens: set[str], query: str, tokenize: Tokenizer) -> tuple[float, list[dict]]:
    """Rank actual normative sentences, never the first qualifying document sentence."""
    from futurecup.adapters.rule_body import canonical
    overlap = len(tokens & set(tokenize(hit["text"]))) / max(1, len(tokens))
    rules = []
    for match in re.finditer(r".+?(?=\.(?!\d)|[\n!?\u3002\uff01\uff1f]|$)", hit["text"]):
        raw = match.group()
        sentence = raw.strip()
        if not RULE.search(sentence) or not 1 <= len(sentence) <= 400:
            continue
        score = len(tokens & set(tokenize(sentence))) / max(1, len(tokens))
        if score < .35:
            continue
        label = re.split(r"[:\uff1a]", sentence, maxsplit=1)[0]
        start = match.start()+len(raw)-len(raw.lstrip())
        rules.append({"text":sentence,"score":score,
            "label_match":canonical(label).casefold()==canonical(query).casefold(),
            "phrase_match":canonical(query).casefold() in canonical(sentence).casefold(),
            "chunk_id":hit["id"],"start":start,"end":start+len(sentence),
            "document_id":hit.get("doc_id"),"document_version":hit.get("document_version")})
    rules.sort(key=lambda r:(-r["score"],-r["label_match"],-r["phrase_match"],r["start"],r["text"]))
    return overlap, rules


def _selection(hits: list[Hit], tokens: set[str], query: str, tokenize: Tokenizer) -> tuple[list[Hit], dict | None, dict]:
    from futurecup.adapters.rule_body import canonical
    classified = {h["id"]:_rules(h,tokens,query,tokenize) for h in hits}
    candidates = [r for h in hits for r in classified[h["id"]][1]]
    candidates.sort(key=lambda r:(-r["score"],-r["label_match"],-r["phrase_match"],
        next(h["score"] for h in hits if h["id"]==r["chunk_id"]),r["chunk_id"],r["start"]))
    top = candidates[0] if candidates else None
    ties = [r for r in candidates if top and (r["score"],r["label_match"],r["phrase_match"]) ==
            (top["score"],top["label_match"],top["phrase_match"])]
    ambiguous = len({canonical(r["text"]) for r in ties}) > 1
    strong = bool(top and top["score"]>=.75 and classified[top["chunk_id"]][0]>=.75 and not ambiguous)
    chosen = top if strong else None
    order = sorted(hits,key=lambda h:(h["id"]!=(chosen or {}).get("chunk_id"),
        -bool(classified[h["id"]][1]),-classified[h["id"]][0],h["score"],h["id"]))
    trace = {"version":"topic-rule-v2","method":"topic_token_coverage; exact_label; phrase; bm25; chunk_id; source_offset",
             "minimum_sentence_coverage":.75,"candidate_count":len(candidates),"selected":chosen,
             "reason":"ambiguous_equal_relevance" if ambiguous else "strongest_actual_sentence" if chosen else "insufficient_sentence_evidence",
             "candidates":candidates[:8]}
    return order, chosen, trace



def retrieve_topics(search: Search, tokenize: Tokenizer, scope: Sequence[str], query: str,
                    topics: list[dict] | None = None, *, max_hits: int = 4,
                    max_queries: int = 16, max_chars: int = 6000,
                    timeout: float = .5) -> dict:
    explicit = topics is not None
    selected = validate_topics(topics if explicit else identify(query))
    if (type(max_hits) is not int or not 1 <= max_hits <= 8
        or type(max_queries) is not int or not 1 <= max_queries <= 16
        or type(max_chars) is not int or not 128 <= max_chars <= 16000
        or not 0 < timeout <= 2):
        raise RuntimeFault("coverage_limits", "Invalid coverage resource limits", 422)
    started = time.perf_counter()
    queries: list[dict] = []
    reports: list[dict] = []
    ranked: list[list[Hit]] = []

    def deadline() -> None:
        if time.perf_counter()-started >= timeout:
            raise RuntimeFault("coverage_timeout", "Retrieval deadline exceeded; not proof of absence", 408)

    def run(text: str, limit: int, topic: str, phase: str) -> list[Hit]:
        deadline()
        if len(queries) >= max_queries:
            return []
        try:
            hits = search(scope, text, limit)
        except RuntimeFault:
            raise
        except Exception:
            raise RuntimeFault("coverage_retrieval_error", "Retrieval failed; not proof of absence", 503) from None
        deadline()
        queries.append({"topic_id": topic, "query": text, "phase": phase,
                        "hit_ids": [hit["id"] for hit in hits]})
        return hits

    for topic in selected:
        before = len(queries)
        hits = run(topic["query"], 2, topic["id"], "initial")
        tokens = set(tokenize(topic["query"]))

        order, chosen, selection = _selection(hits,tokens,topic["query"],tokenize)
        if chosen is None:
            extras = run(topic["query"],8,topic["id"],"supplement")
            hits = list({h["id"]:h for h in hits+extras}.values())
            order, chosen, selection = _selection(hits,tokens,topic["query"],tokenize)
        strong = chosen is not None
        queried = len(queries) > before
        reports.append({"id": topic["id"], "query": topic["query"],
            # Automatic topics enforce ONLY strongly relevant discovered rules.
            "required": topic["required"] if explicit else strong,
            "requirement_origin": "caller" if explicit else "lexical_discovery",
            "status": "COVERED" if strong else "PARTIAL" if order else "UNCOVERED",
            "search_state": "QUERIED" if queried else "QUERY_BUDGET_EXHAUSTED",
            "candidate_ids": [h["id"] for h in order],
            "critical_rule": chosen["text"] if chosen else None,
            "rule_chunk_id": chosen["chunk_id"] if chosen else None, "rule_selection":selection})
        ranked.append(order)

    # Topic round robin prevents one broad topic exhausting all context slots.
    merged: list[Hit] = []
    seen = set()
    size = 0
    for depth in range(8):
        for hits in ranked:
            if depth >= len(hits):
                continue
            hit = hits[depth]
            if hit["id"] in seen or len(merged) >= max_hits or size+len(hit["text"]) > max_chars:
                continue
            merged.append(hit)
            seen.add(hit["id"])
            size += len(hit["text"])
    for topic in reports:
        topic["injected_ids"] = [i for i in topic["candidate_ids"] if i in seen]
        if topic["rule_chunk_id"] not in seen:
            topic["status"] = "PARTIAL" if topic["candidate_ids"] else "UNCOVERED"
        if not topic["injected_ids"] and topic["candidate_ids"]:
            topic["search_state"] = "CONTEXT_BUDGET_EXHAUSTED"
    deadline()
    return {"hits": merged, "coverage": reports, "queries": queries,
        "retrieval_calls": len(queries), "context_chars": size,
        "elapsed_ms": (time.perf_counter()-started)*1000,
        "topic_extraction_truncated": not explicit and len(identify(query, limit=9)) > 8,
        "coverage_definition": "chunk and selected sentence overlap >= .75; deterministic topic ranking; ambiguous ties are PARTIAL; not semantic completeness",
        "coverage_version": "topics-v2"}


def compact_context(record: dict) -> dict:
    """Audit keeps full query history; model gets bounded evidence only once."""
    from futurecup.adapters.rule_contract import atoms
    if record.get("rule_contract")=="staged-rules-v2":
        return {"hits":record["hits"],"coverage":[{k:t[k] for k in ("id","query","required","status")} for t in record["coverage"]],
                "source_catalog":record["source_catalog"],"rule_contract":"staged-rules-v2","knowledge_stage":record["knowledge_stage"]}
    keys = ("id", "query", "required", "status", "critical_rule", "rule_chunk_id")
    return {"hits": record["hits"], "coverage": [{**{k:t[k] for k in keys},
        "rule_clauses":atoms(t["critical_rule"],t["query"]) if t.get("critical_rule") else []}
        for t in record.get("coverage",[])], "context_sha256":record["context_sha256"],
        "knowledge_stage":record.get("knowledge_stage"),"rule_contract":record.get("rule_contract","historical")}



def assess_output(value: object, records: Sequence[dict], source_keys=None) -> list[dict]:
    """Visible-body exact reflection plus bounded protected-field conflict checks."""
    from futurecup.adapters.rule_body import extract, inspect_rule
    from futurecup.adapters.knowledge import terms
    value = value if isinstance(value, dict) else {}
    citations = value.get("citations", [])
    citations = citations if isinstance(citations,list) else []
    body = extract(value)
    results = []
    for record in records:
        actual_hits = {h["id"]:h for h in record.get("hits",[])}
        for topic in record.get("coverage",[]):
            if not topic["required"]:
                rule,chunk=topic.get("critical_rule"),topic.get("rule_chunk_id")
                if rule and chunk in actual_hits:
                    optional=inspect_rule(rule,topic["query"],body,terms)
                    if optional["status"]=="POSSIBLE_CONFLICT":
                        results.append({"topic_id":topic["id"],"status":optional.pop("status"),
                            "validation_type":optional.pop("validation_type"),"rule_chunk_id":chunk,
                            "expected_rule":rule,"evidence":optional,"requirement_origin":"out_of_scope_conflict_guard"})
                continue
            rule, chunk = topic.get("critical_rule"), topic.get("rule_chunk_id")
            details = {"normalization_version":body["version"],"body_fields":body["body_fields"]}
            if topic.get("search_state") != "QUERIED":
                state, kind = "COVERAGE_RESOURCE_LIMIT", "RETRIEVAL_COVERAGE_INSUFFICIENT"
            elif not topic.get("injected_ids"):
                advice = any(topic["query"] in f["rendered"] and
                    ("design suggestion" in f["rendered"].lower() or "\u8bbe\u8ba1\u5efa\u8bae" in f["rendered"])
                    for f in body["fragments"])
                state = "SOURCE_UNAVAILABLE_ADVICE" if advice else "SOURCE_UNAVAILABLE_UNMARKED"
                kind = "RETRIEVAL_COVERAGE_INSUFFICIENT"
            elif not rule:
                state, kind = "INSUFFICIENT_EVIDENCE", "RETRIEVAL_COVERAGE_INSUFFICIENT"
            elif chunk not in actual_hits or rule not in actual_hits[chunk]["text"]:
                state, kind = "SOURCE_NOT_CITED", "SOURCE_CITATION_INVALID"
            elif source_keys is not None and (topic["id"],chunk) not in source_keys:
                state, kind = "SOURCE_NOT_CITED", "SOURCE_CITATION_INVALID"
            elif source_keys is None and not any(isinstance(c,dict) and c.get("chunk_id")==chunk and isinstance(c.get("quote"),str)
                         and rule in c["quote"] and c["quote"] in actual_hits[chunk]["text"] for c in citations):
                state, kind = "SOURCE_NOT_CITED", "SOURCE_CITATION_INVALID"
            else:
                details = inspect_rule(rule,topic["query"],body,terms)
                state, kind = details.pop("status"), details.pop("validation_type")
                from futurecup.adapters.rule_contract import binding_proof
                strict=record.get("rule_contract")=="atomic-body-v1" or "rule_coverage" in value
                proof=binding_proof(topic,value,body,strict)
                details["atomic_binding"]=proof
                if not details["conflicts"] and proof["passed"]:
                    state,kind="RULE_REFLECTED","RULE_REFLECTION"
                elif strict and state=="RULE_REFLECTED" and not proof["passed"]:
                    state,kind="SOURCE_NOT_REFLECTED","RULE_NOT_REFLECTED"
            results.append({"topic_id":topic["id"],"status":state,"validation_type":kind,
                "rule_chunk_id":chunk,"requirement_origin":topic.get("requirement_origin","caller"),
                "expected_rule":rule,"selection":topic.get("rule_selection",{}).get("selected"),
                "evidence":details})
    return results


def knowledge_evidence(value: object, records: Sequence[dict], artifact_digest: str):
    """One legacy external-test result, with independently typed citation/body findings."""
    from futurecup.adapters.knowledge import check_citations
    from futurecup.domain.verification import CheckEvidence
    coverage = assess_output(value,records)
    citation_ok = check_citations(value,records)
    failures = [c for c in coverage if c["status"] not in ("RULE_REFLECTED","SOURCE_UNAVAILABLE_ADVICE")]
    codes = {"RETRIEVAL_COVERAGE_INSUFFICIENT":"knowledge_coverage_insufficient",
             "SOURCE_CITATION_INVALID":"knowledge_citation_invalid","RULE_NOT_REFLECTED":"knowledge_rule_not_reflected",
             "RULE_CONFLICT":"knowledge_rule_conflict","NUMERIC_UNIT_CONFLICT":"knowledge_numeric_unit_conflict",
             "SEMANTIC_UNCERTAIN":"knowledge_semantic_uncertain"}
    reasons = list(dict.fromkeys(([] if citation_ok else ["knowledge_citation_invalid"]) +
                                 [codes[c["validation_type"]] for c in failures]))
    links=value.get("rule_coverage",[]) if isinstance(value,dict) else []
    topic_ids={t["id"] for r in records for t in r.get("coverage",[])}
    links_valid=isinstance(links,list) and all(isinstance(x,dict) and x.get("topic_id") in topic_ids for x in links)
    if not links_valid:reasons.append("knowledge_rule_not_reflected")
    valid = citation_ok and not failures and links_valid
    details = {"verification_contract":"knowledge-rules-v3",
        "citation_check":{"status":"PASSED" if citation_ok else "FAILED",
                          "reason":"knowledge_citation_valid" if citation_ok else "knowledge_citation_invalid"},
        "coverage":coverage,"reason_codes":reasons,"binding_check":{"status":"PASSED" if links_valid else "FAILED"},
        "repair_hint":"Repair only the listed failed topics in the actual visible deliverable body. Keep valid citations and unaffected rules. Follow each evidence difference; do not rewrite every rule without evidence."}
    return CheckEvidence("external_tests","PASSED" if valid else "FAILED",
                         "knowledge_citation_valid" if valid else reasons[0],reference=artifact_digest,details=details)
