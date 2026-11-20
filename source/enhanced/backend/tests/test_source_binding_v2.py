"""V2 source references, perturbations and actual engine tests. Explicitly no network."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from futurecup.adapters.knowledge import KnowledgeStore
from futurecup.adapters.source_binding import bind,resolve
from futurecup.adapters.source_verification import source_evidence
from futurecup.adapters.llm import MockLLMProvider
from futurecup.domain.rule_contract import source_schema
from jsonschema import Draft202012Validator
from test_workspace_11a import application,create,confirm

RULE="Order lock: Order service must lock the item for 15 minutes, other users must not place duplicate orders."
TOPICS=[{"id":"lock","query":"Order lock","required":True}]


def fixture(scope="one"):
    store=KnowledgeStore(":memory:")
    kb=store.create("SIMULATED source")["id"];store.import_document(kb,"rules.md",RULE.encode())
    record=asyncio.run(store.retrieve([kb],json.dumps({"request":{"retrieval_topics":TOPICS,"messages":["Order lock"]},
        "rule_contract":"staged-rules-v2","knowledge_stage":"requirements"})))
    record=bind(record,scope)
    refs=record["source_catalog"]
    value={"artifact_type":"requirements","title":"MOCK","summary":"Mock analysis","items":["Mock"],
        "source_refs":[{"source_ref":x["source_ref"],"document_version":x["document_version"]} for x in refs],
        "requirements":[{"source_ref":x["source_ref"],"analysis":"Proposed requirement: "+x["quote"]+"."} for x in refs]}
    store.connection.close()
    return record,value


def test_valid_reference_is_not_a_model_generated_quote_or_answer():
    r,v=fixture()
    assert Draft202012Validator(source_schema("requirements")).is_valid(v)
    evidence=source_evidence(v,[r],"digest","requirements",[])
    assert evidence.status=="PASSED"
    assert "citations" not in v and "direct_quotes" not in v
    for s in evidence.details["resolved_sources"]:
        h=next(h for h in r["hits"] if h["id"]==s["chunk_id"])
        assert h["text"][s["start"]:s["end"]]==s["quote"]
    assert evidence.details["computed_counts"]["requirements"]==len(v["requirements"])


@pytest.mark.parametrize("mutation",["crlf","lf","terminal","bold","italic","table","list","field_order"])
def test_business_presentation_perturbations_preserve_proven_constraints(mutation):
    r,v=fixture()
    for item in v["requirements"]:
        if mutation=="crlf":item["analysis"]=item["analysis"].replace(" ","\r\n")
        if mutation=="lf":item["analysis"]=item["analysis"].replace(" ","\n")
        if mutation=="terminal":item["analysis"]+="。"
        if mutation=="bold":item["analysis"]="**"+item["analysis"]+"**"
        if mutation=="italic":item["analysis"]="*"+item["analysis"]+"*"
        if mutation=="table":item["analysis"]="| ID | Requirement |\n|---|---|\n| R | "+item["analysis"]+" |"
        if mutation=="list":item["analysis"]="- "+item["analysis"]
    if mutation=="field_order":v=dict(reversed(list(v.items())))
    assert source_evidence(v,[r],"digest","requirements",[]).status=="PASSED"


@pytest.mark.parametrize("mutation",["unknown","other_execution","version","missing","empty_analysis","number","unit","negation","conflict","summary_count"])
def test_correct_ids_never_authorize_wrong_or_empty_business_analysis(mutation):
    r,v=fixture()
    if mutation=="unknown":v["source_refs"][0]["source_ref"]="unknown"
    if mutation=="other_execution":v["source_refs"][0]["source_ref"]="src:another:other"
    if mutation=="version":v["source_refs"][0]["document_version"]+=1
    if mutation=="missing":v["requirements"].pop()
    if mutation=="empty_analysis":v["requirements"][0]["analysis"]="A requirement is acknowledged."
    if mutation=="number":v["requirements"][0]["analysis"]=v["requirements"][0]["analysis"].replace("15","30")
    if mutation=="unit":v["requirements"][0]["analysis"]=v["requirements"][0]["analysis"].replace("minutes","seconds")
    if mutation=="negation":v["requirements"][0]["analysis"]=v["requirements"][0]["analysis"].replace("must lock","must not lock")
    if mutation=="conflict":v["requirements"][0]["analysis"]+=" Order service must lock the item for 30 minutes."
    if mutation=="summary_count":v["summary"]="共99条规则"
    checked=source_evidence(v,[r],"digest","requirements",[])
    assert checked.status=="FAILED"
    if mutation in ("number","unit","negation"):
        assert any(e["code"]=={"number":"numeric_conflict","unit":"unit_conflict","negation":"negation_conflict"}[mutation] for e in checked.details["errors"])


@pytest.mark.parametrize("layout",["remove_newlines","crlf_to_lf","wrong_text"])
def test_optional_direct_quote_is_still_strict(layout):
    r,v=fixture()
    # Add a real multi-line source for this focused quote check.
    r["hits"][0]["text"]+="\r\nA separate source line."
    r=bind(r,"one")
    _,v=fixture()  # refs must come from the changed actual snapshot
    v["source_refs"]=[{"source_ref":x["source_ref"],"document_version":x["document_version"]} for x in r["source_catalog"]]
    v["requirements"]=[{"source_ref":x["source_ref"],"analysis":"Proposed requirement: "+x["quote"]+"."} for x in r["source_catalog"]]
    q=r["hits"][0]["text"]
    q=q.replace("\r\n","") if layout=="remove_newlines" else q.replace("\r\n","\n") if layout=="crlf_to_lf" else "fabricated direct quote"
    v["direct_quotes"]=[{**v["source_refs"][0],"quote":q}]
    ev=source_evidence(v,[r],"digest","requirements",[])
    assert ev.status=="FAILED"
    assert any(e["code"]==("direct_quote_not_contiguous" if layout=="wrong_text" else "direct_quote_layout_difference") for e in ev.details["errors"])


def test_tampered_snapshot_is_never_accepted():
    r,v=fixture();r["source_catalog"][0]["quote"]="forged"
    with pytest.raises(ValueError,match="altered"):resolve(v,[r])


class Perturbed(MockLLMProvider):
    def __init__(self,mutation=None,persistent=False):self.mutation=mutation;self.persistent=persistent;self.requests=[]
    async def infer(self,r):
        response=await super().infer(r)
        if r.model=="mock-workspace-worker":
            self.requests.append(r);v=json.loads(response.content)
            if self.mutation and (self.persistent or len(self.requests)==1):
                self.mutation(v)
                return replace(response,content=json.dumps(v))
        return response


def run(provider=None, evidence=None):
    root=Path(__file__).resolve().parents[2]
    with TestClient(application(provider)) as c:
        kb=c.app.state.knowledge.create("SIMULATED eight-topic test")["id"]
        c.app.state.knowledge.import_document(kb,"rules.md",(root/"docs/v1.1/real-14e-business-rules.md").read_bytes())
        topics=json.loads((root/"config/v1.1/staged-rag-task.json").read_text(encoding="utf-8"))["retrieval_topics"]
        t=create(c,request="Extract constraints; design architecture; independently consolidate",
            output_profile="design_document",knowledge_contract="staged-rules-v2",knowledge_base_ids=[kb],retrieval_topics=topics)
        assert t["state"]=="awaiting_confirmation",t
        t=confirm(c,t)
        if evidence is not None:
            evidence["task"]=t;evidence["nodes"]={}
            for n in t["report"]["nodes"]:
                if n["attempt_ids"]:
                    evidence["nodes"][n["node_id"]]={k:c.get("/api/v1/self-loops/"+n["id"]+"/"+k).json() for k in ("attempts","verifications","feedback")}
        return t


def test_full_production_engine_three_stages_source_evidence_stays_separate():
    t=run()
    assert t["state"]=="succeeded",t
    assert t["knowledge_enhanced"] and t["report"]["verified"]
    assert t["usage"]["model_calls"]==4 and t["usage"]["feedback_loops"]==0
    assert len(t["delivery_sources"])==13
    assert "citations" not in t["delivery"]
    nodes=t["report"]["nodes"]
    assert nodes[1]["input"]["upstream"]["s1"]==nodes[0]["output"]
    assert nodes[2]["input"]["upstream"]["s2"]==nodes[1]["output"]
    assert t["knowledge_metrics"]["s1"]["requirements"]==13


def test_real_failure_feedback_is_not_replaced_by_program_answers():
    def missing(v):
        if v["artifact_type"]=="requirements":v["requirements"][0]["analysis"]="Acknowledged requirement without actual content"
    provider=Perturbed(missing)
    t=run(provider)
    assert t["state"]=="succeeded" and t["usage"]["model_calls"]==5 and t["usage"]["feedback_loops"]==1
    recovery=json.loads(provider.requests[1].messages[1]["content"])["recovery"]
    assert recovery["previous_artifact"]["content"]["requirements"][0]["analysis"].startswith("Acknowledged")
    assert "semantic_uncertain" in json.dumps(recovery["verification"])


@pytest.mark.parametrize("bad",["missing_body","numeric_conflict","quote_only","forged","negation"])
def test_final_prose_remains_independent_even_with_resolved_sources(bad):
    class Failure(Perturbed):
        async def infer(self,r):
            result=await super().infer(r)
            if r.model=="mock-workspace-worker":
                v=json.loads(result.content)
                if v["artifact_type"]=="delivery":
                    if bad=="missing_body":v["markdown"]="[MOCK] Explanatory text without adopted rules. "*40
                    if bad=="numeric_conflict":v["markdown"]+="\n订单创建后必须锁定商品30分钟"
                    if bad=="quote_only":v["markdown"]="# 来源引用\n"+v["markdown"]
                    if bad=="forged":v["source_refs"][0]["source_ref"]="forged"
                    if bad=="negation":v["markdown"]=v["markdown"].replace("必须锁定商品15分钟","无需锁定商品15分钟")
                    return replace(result,content=json.dumps(v))
            return result
    t=run(Failure())
    assert t["state"]=="failed" and not t["report"]["verified"] and t["delivery"] is None
    assert t["usage"]["model_calls"]==5 and t["usage"]["feedback_loops"]==1


def test_cross_task_reference_and_changed_source_version_are_rejected():
    r,v=fixture("current")
    old=bind(r,"old-execution")
    v["source_refs"][0]["source_ref"]=old["source_catalog"][0]["source_ref"]
    assert any(x["code"]=="source_not_retrieved" for x in source_evidence(v,[r],"d","requirements",[]).details["errors"])
    r,v=fixture()
    r["hits"][0]["document_version"]+=1
    with pytest.raises(ValueError):resolve(v,[r])


def test_condition_reversal_remains_a_business_error():
    from futurecup.adapters.coverage import retrieve_topics
    rule="订单取消：买家必须能在未付款时取消订单"
    hit={"id":"c","score":0,"text":rule,"doc_id":"d","document_version":1,"kb_id":"k","knowledge_version":1,"document_name":"mock"}
    record=retrieve_topics(lambda *args:[hit],__import__("futurecup.adapters.knowledge",fromlist=["terms"]).terms,["k"],"",[{"id":"cancel","query":"订单取消","required":True}])
    r=bind(record,"condition")
    source=r["source_catalog"][0]
    v={"artifact_type":"requirements","summary":"Mock","source_refs":[{"source_ref":source["source_ref"],"document_version":1}],
       "requirements":[{"source_ref":source["source_ref"],"analysis":"需求为买家必须能在已付款时取消订单"}]}
    assert source_evidence(v,[r],"d","requirements",[]).status=="FAILED"


def test_actual_callback_refuses_tampered_upstream_before_resolving_sources():
    from types import SimpleNamespace as N
    from futurecup.adapters.staged_knowledge import staged_check
    async def ready(value):return value
    value={"artifact_type":"design"}
    node=N(id="node",node_id="s2",input={"upstream":{"s1":{"tampered":True}},"knowledge_stage":"design","rule_contract":"staged-rules-v2"})
    parent=N(node_id="s1",verified=True,state=N(value="succeeded"),output={"actual":True})
    repo=N(get_report=lambda _:ready(N(nodes=[node,parent])))
    safety=N(session=lambda _:ready({"current_attempt":"a","run_id":"r"}),
             attempt=lambda _:ready({"artifact":{"digest":"digest","content":value}}))
    artifact=N(ref=N(id="node:artifact"),digest="digest",content=value)
    result=asyncio.run(staged_check(repo,safety,None)(artifact))
    assert result.status=="ERROR" and result.reason=="knowledge_upstream_binding_error"


def test_one_direct_quote_root_error_keeps_all_affected_topics():
    r,v=fixture()
    ref=v["source_refs"][0]
    v["direct_quotes"]=[{**ref,"quote":"invalid literal"},{**ref,"quote":"invalid literal"}]
    problems=source_evidence(v,[r],"d","requirements",[]).details["errors"]
    roots=[x for x in problems if x["code"]=="direct_quote_not_contiguous"]
    assert len(roots)==1 and len(roots[0]["paths"])==2 and roots[0]["affected_topics"]==["lock"]


def test_confirmed_narrow_design_scope_does_not_weaken_final_global_rules():
    root=Path(__file__).resolve().parents[2]
    with TestClient(application()) as c:
        kb=c.app.state.knowledge.create("SIMULATED scoped design")["id"]
        c.app.state.knowledge.import_document(kb,"rules.md",(root/"docs/v1.1/real-14e-business-rules.md").read_bytes())
        topics=json.loads((root/"config/v1.1/staged-rag-task.json").read_text(encoding="utf-8"))["retrieval_topics"]
        task=create(c,request="Extract constraints; design architecture; consolidate",
            knowledge_base_ids=[kb],retrieval_topics=topics,output_profile="design_document",knowledge_contract="staged-rules-v2")
        plan=deepcopy(task["plan"]);plan["steps"][1]["knowledge_topics"]=["lock"]
        response=c.put("/api/v1/workspace/tasks/"+task["id"]+"/plan",json={"revision":task["revision"],"plan":plan})
        assert response.status_code==200,response.text
        task=confirm(c,response.json())
        assert task["state"]=="succeeded" and task["report"]["verified"]
        assert len(task["report"]["nodes"][1]["output"]["source_refs"])==2
        assert len(task["delivery_sources"])==13
