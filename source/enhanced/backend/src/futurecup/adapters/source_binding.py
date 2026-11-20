"""Deterministic, execution-scoped references into immutable retrieved source snapshots."""
import hashlib
import json
import re

from futurecup.adapters.rule_contract import atoms

VERSION = "staged-rules-v2"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def catalog(record, scope):
    entries = []
    for topic in record.get("coverage", []):
        hit = next((h for h in record["hits"] if h["id"] == topic.get("rule_chunk_id")), None)
        rule = topic.get("critical_rule")
        if not hit or not rule or rule not in hit["text"]:
            continue
        selected = topic.get("rule_selection", {}).get("selected", {})
        start = selected.get("start", hit["text"].find(rule))
        if hit["text"][start:start+len(rule)] != rule:
            raise ValueError("Snapshot rule location mismatch")
        for clause in atoms(rule, topic["query"]):
            a, b = start+clause["start"], start+clause["end"]
            raw=hit["text"][a:b];a+=len(raw)-len(raw.lstrip());b-=len(raw)-len(raw.rstrip())
            entry = {"topic_id":topic["id"], "clause_id":clause["id"], "chunk_id":hit["id"],
                "document_id":hit["doc_id"], "document_version":hit["document_version"],
                "knowledge_base_id":hit["kb_id"], "knowledge_version":hit["knowledge_version"],
                "chunk_sha256":hashlib.sha256(hit["text"].encode()).hexdigest(),
                "start":a, "end":b, "quote":hit["text"][a:b],
                "required":topic["required"], "document_name":hit["document_name"]}
            if entry["quote"] != clause["text"]:
                raise ValueError("Snapshot clause location mismatch")
            entry["source_ref"] = "src:"+hashlib.sha256(scope.encode()).hexdigest()[:12]+":"+fingerprint({"scope":scope,"entry":entry})[:24]
            entries.append(entry)
    return entries


def bind(record, scope):
    return {**record, "source_scope":scope, "source_catalog":catalog(record, scope)}


def origin(entry):
    return tuple(entry[k] for k in ("topic_id","clause_id","chunk_id","document_id",
        "document_version","knowledge_version","chunk_sha256","start","end"))


def error(code, path, actual=None, expected=None, topics=()):
    return {"code":code,"path":path,"actual":actual,"expected":expected,
            "affected_topics":list(topics),"repair_hint":{
                "source_id_not_found":"Use an exact source_ref supplied in this retrieval snapshot.",
                "source_not_retrieved":"Use current execution references, never references from another task/attempt.",
                "source_version_mismatch":"Copy the document_version of this source reference.",
                "direct_quote_not_contiguous":"Supply a genuinely contiguous source substring or use source_refs without claiming a direct quote.",
                "direct_quote_layout_difference":"This is a direct quote: preserve actual source layout, or omit the optional direct quote and use source_refs.",
                "required_clause_missing":"Add the missing requirement analysis/association; references alone are insufficient.",
                "summary_count_mismatch":"Remove the duplicate summary count; the server computes the actual array count."
            }.get(code,"Repair this specific evidence violation without changing source rules or permissions.")}


def resolve(value, records):
    available = {}
    for record in records:
        fresh = catalog(record, record["source_scope"])
        if fresh != record.get("source_catalog"):
            raise ValueError("Source catalog was altered")
        available.update((x["source_ref"],x) for x in fresh)
    problems, resolved = [], {}
    scopes = {hashlib.sha256(r["source_scope"].encode()).hexdigest()[:12] for r in records}
    for i, ref in enumerate(value.get("source_refs", [])):
        identifier = ref.get("source_ref")
        entry = available.get(identifier)
        if not entry:
            code = "source_not_retrieved" if isinstance(identifier,str) and identifier.startswith("src:") and identifier.split(":")[1] not in scopes else "source_id_not_found"
            problems.append(error(code,f"source_refs.{i}.source_ref",identifier,"Current retrieved source reference"))
        elif ref.get("document_version") != entry["document_version"]:
            problems.append(error("source_version_mismatch",f"source_refs.{i}.document_version",
                                  ref.get("document_version"),entry["document_version"],[entry["topic_id"]]))
        elif identifier in resolved:
            problems.append(error("source_reference_duplicate",f"source_refs.{i}",identifier,"Unique reference"))
        else:
            resolved[identifier] = entry
    for identifier, entry in available.items():
        if entry["required"] and identifier not in {x.get("source_ref") for x in value.get("source_refs",[])}:
            problems.append(error("required_source_missing","source_refs",identifier,"Required source reference",[entry["topic_id"]]))
    # Direct quotes are optional, but never silently normalize a claimed verbatim quote.
    hits = {h["id"]:h for r in records for h in r["hits"]}
    for i, quote in enumerate(value.get("direct_quotes", [])):
        entry = resolved.get(quote.get("source_ref"))
        if not entry:
            problems.append(error("source_not_retrieved",f"direct_quotes.{i}.source_ref",quote.get("source_ref"),"Validated source_refs member"))
        elif quote.get("document_version") != entry["document_version"]:
            problems.append(error("source_version_mismatch",f"direct_quotes.{i}.document_version",quote.get("document_version"),entry["document_version"]))
        elif not quote.get("quote") or quote["quote"] not in hits[entry["chunk_id"]]["text"]:
            original = hits[entry["chunk_id"]]["text"]
            layout = bool(quote.get("quote")) and re.sub(r"\s+","",quote["quote"]) in re.sub(r"\s+","",original)
            problems.append(error("direct_quote_layout_difference" if layout else "direct_quote_not_contiguous",
                f"direct_quotes.{i}.quote",quote.get("quote"),{"chunk_id":entry["chunk_id"],"document_version":entry["document_version"],"constraint":"Contiguous literal substring of the retrieved chunk"},[entry["topic_id"]]))
    return resolved, problems
