"""Serializable body/source binding contract; independent of parsing adapters."""
SCHEMA = {"type":"array","maxItems":64,"items":{"type":"object","additionalProperties":False,
    "required":["topic_id","clause_id","chunk_id","body_quote"],
    "properties":{k:{"type":"string","minLength":1,"maxLength":700} for k in
                  ("topic_id","clause_id","chunk_id","body_quote")}}}

STAGES = ("requirements", "design", "delivery")


def staged_schema(stage: str) -> dict:
    """Typed intermediate artifacts; final delivery keeps mandatory visible-body bindings."""
    if stage not in STAGES:
        raise ValueError("Unknown knowledge stage")
    props = {
        "artifact_type": {"const": stage},
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "items": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string", "maxLength": 500}},
        "citations": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
            "type": "object", "additionalProperties": False, "required": ["chunk_id", "quote"],
            "properties": {"chunk_id": {"type": "string"}, "quote": {"type": "string", "minLength": 1, "maxLength": 8000}}}},
    }
    if stage == "requirements":
        props["requirements"] = {"type": "array", "minItems": 1, "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["topic_id", "clause_id", "chunk_id", "statement"],
            "properties": {k: {"type": "string", "minLength": 1, "maxLength": 700}
                           for k in ("topic_id", "clause_id", "chunk_id", "statement")}}}
    else:
        props["markdown"] = {"type": "string", "minLength": 600 if stage == "delivery" else 80, "maxLength": 12000}
        props["rule_coverage" if stage == "delivery" else "design_links"] = SCHEMA
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}

def source_schema(stage: str) -> dict:
    """V2 separates source attribution from optional literal quotation and analysis."""
    schema=staged_schema(stage)
    props=schema["properties"]
    del props["citations"]
    ref={"type":"object","additionalProperties":False,"required":["source_ref","document_version"],
         "properties":{"source_ref":{"type":"string","minLength":1,"maxLength":128},
                       "document_version":{"type":"integer","minimum":1}}}
    props["source_refs"]={"type":"array","minItems":1,"maxItems":64,"items":ref}
    props["direct_quotes"]={"type":"array","maxItems":16,"items":{
        **ref,"required":["source_ref","document_version","quote"],
        "properties":{**ref["properties"],"quote":{"type":"string","minLength":1,"maxLength":8000}}}}
    if stage=="requirements":
        props["requirements"]={"type":"array","minItems":1,"maxItems":64,"items":{
            "type":"object","additionalProperties":False,"required":["source_ref","analysis"],
            "properties":{"source_ref":ref["properties"]["source_ref"],
                          "analysis":{"type":"string","minLength":12,"maxLength":1200}}}}
    elif stage=="design":
        props["design_links"]={"type":"array","minItems":1,"maxItems":64,"items":{
            "type":"object","additionalProperties":False,"required":["source_ref","body_quote"],
            "properties":{"source_ref":ref["properties"]["source_ref"],
                          "body_quote":{"type":"string","minLength":1,"maxLength":700}}}}
    schema["required"]=[k for k in props if k!="direct_quotes"]
    return schema
