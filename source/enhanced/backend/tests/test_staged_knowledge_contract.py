"""Staged source extraction, design traceability and strict final prose. All calls are Mock."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from futurecup.adapters.coverage import retrieve_topics
from futurecup.adapters.knowledge import terms
from futurecup.adapters.rule_contract import atoms
from futurecup.adapters.staged_knowledge import staged_evidence
from futurecup.domain.rule_contract import staged_schema
from futurecup.adapters.llm import MockLLMProvider
from test_workspace_11a import application, create, confirm

ROOT = Path(__file__).resolve().parents[2]
RULE = "Order lock must expire after 15 minutes."
TOPIC = {"id": "lock", "query": "order lock", "required": True}


def records():
    r=retrieve_topics(lambda *a:[{"id":"chunk","text":RULE,"score":0}],terms,["kb"],"",[TOPIC])
    r.update(rule_contract="staged-rules-v1")
    return [r]


def outputs():
    t=records()[0]["coverage"][0]
    a=atoms(t["critical_rule"],t["query"])[0]
    link={"topic_id":"lock","clause_id":a["id"],"chunk_id":"chunk","body_quote":RULE}
    base={"title":"SIMULATED","summary":"Mock fixture","items":["Mock trace"],"citations":[{"chunk_id":"chunk","quote":RULE}]}
    extract={**base,"artifact_type":"requirements","requirements":[{k:v for k,v in link.items() if k!="body_quote"} | {"statement":a["text"]}]}
    design={**base,"artifact_type":"design","markdown":"## Architecture\n"+"A local order service manages expirations and durable state. "*2+RULE,"design_links":[link]}
    delivery={**base,"artifact_type":"delivery","markdown":"## Design\n"+"Mock explanatory design text. "*30+RULE,"rule_coverage":[link]}
    return extract,design,delivery


def test_extraction_has_no_final_prose_requirement_but_is_source_faithful():
    e,_,_=outputs()
    assert Draft202012Validator(staged_schema("requirements")).is_valid(e)
    assert staged_evidence(e,records(),"digest","requirements",{}).status=="PASSED"
    assert "markdown" not in e


@pytest.mark.parametrize("bad",["missing","number","unit","negation","forged","duplicate","wrong_type"])
def test_wrong_extraction_never_reaches_design(bad):
    e,_,_=outputs()
    if bad=="missing":e["requirements"]=[]
    if bad=="number":e["requirements"][0]["statement"]=RULE.replace("15","30")
    if bad=="unit":e["requirements"][0]["statement"]=RULE.replace("minutes","seconds")
    if bad=="negation":e["requirements"][0]["statement"]=RULE.replace("must","must not")
    if bad=="forged":e["citations"][0]["chunk_id"]="forged"
    if bad=="duplicate":e["requirements"]*=2
    if bad=="wrong_type":e["artifact_type"]="delivery"
    assert staged_evidence(e,records(),"digest","requirements",{}).status=="FAILED"


def test_design_links_actual_architecture_and_actual_upstream():
    e,d,_=outputs()
    d["markdown"]="## Architecture\n"+"The order service stores deadlines and dispatches expiry jobs. "*2
    d["design_links"][0]["body_quote"]="The order service stores deadlines and dispatches expiry jobs."
    assert staged_evidence(d,records(),"digest","design",{"s1":e}).status=="PASSED"
    # This demonstrates traceability, never final global prose correctness.
    assert staged_evidence(d,records(),"digest","delivery",{"s1":e}).status=="FAILED"
    e["requirements"][0]["statement"]=RULE.replace("15","30")
    assert staged_evidence(d,records(),"digest","design",{"s1":e}).status=="FAILED"


@pytest.mark.parametrize("bad",["missing","number","unit","negation","conflict","quote_only","forged","upstream_missing"])
def test_final_lists_never_replace_required_prose_and_conflicts_fail(bad):
    e,d,v=outputs()
    if bad=="missing":v["markdown"]="Ordinary design text. "*40
    if bad=="number":v["markdown"]=v["markdown"].replace("15","30")
    if bad=="unit":v["markdown"]=v["markdown"].replace("minutes","seconds")
    if bad=="negation":v["markdown"]=v["markdown"].replace("must","must not")
    if bad=="conflict":v["markdown"]+="\n"+RULE.replace("15","30")
    if bad=="quote_only":v["markdown"]="## References\n"+RULE
    if bad=="forged":v["citations"][0]["chunk_id"]="forged"
    up={} if bad=="upstream_missing" else {"s1":e,"s2":d}
    assert staged_evidence(v,records(),"digest","delivery",up).status=="FAILED"


class Capture(MockLLMProvider):
    def __init__(self,mutation=None,persistent=True):
        self.workers=[];self.mutation=mutation;self.persistent=persistent
    async def infer(self,r):
        result=await super().infer(r)
        if r.model=="mock-workspace-worker":
            self.workers.append(r)
            value=json.loads(result.content);stage=value.get("artifact_type")
            count=sum(json.loads(x.messages[1]["content"]).get("knowledge_stage",
                json.loads(x.messages[1]["content"]).get("original_input",{}).get("knowledge_stage"))==stage for x in self.workers)
            if self.mutation and (self.persistent or count==1):
                self.mutation(stage,value)
                return replace(result,content=json.dumps(value))
        return result


def run_case(provider=None,all_topics=False,evidence=None):
    with TestClient(application(provider)) as c:
        store=c.app.state.knowledge;kb=store.create("SIMULATED independent source")["id"]
        source=(ROOT/"docs/v1.1/real-14e-business-rules.md").read_bytes() if all_topics else RULE.encode()
        store.import_document(kb,"source.md",source)
        topics=json.loads((ROOT/"config/v1.1/real-14e-task.json").read_text(encoding="utf-8"))["retrieval_topics"] if all_topics else [TOPIC]
        t=create(c,request="Extract actual business constraints; design architecture and database; independently consolidate",
                 knowledge_base_ids=[kb],retrieval_topics=topics,output_profile="design_document",knowledge_contract="staged-rules-v1")
        assert t["state"]=="awaiting_confirmation",t
        assert t["run_id"] is None
        t=confirm(c,t)
        if evidence is not None:
            evidence['task']=t
            evidence['nodes']={}
            for n in t['report']['nodes']:
                if n['attempt_ids']:
                    evidence['nodes'][n['node_id']]={key:c.get('/api/v1/self-loops/'+n['id']+'/'+key).json()
                        for key in ('attempts','verifications','feedback')}
        return t


def test_actual_three_agent_eight_topic_workflow_has_typed_upstream_and_final_delivery():
    provider=Capture();t=run_case(provider,True)
    assert t["state"]=="succeeded",t
    assert t["simulated"] and t["report"]["verified"] and t["knowledge_enhanced"]
    nodes=t["report"]["nodes"];a,b,c=nodes
    assert [n["output"]["artifact_type"] for n in nodes]==["requirements","design","delivery"]
    assert b["input"]["upstream"]["s1"]==a["output"]
    assert c["input"]["upstream"]=={"s1":a["output"],"s2":b["output"]}
    assert len(a["output"]["requirements"])==13
    assert "15" in t["delivery"]["markdown"] and t["delivery"]["citations"]
    assert t["usage"]["model_calls"]==4 and t["usage"]["feedback_loops"]==0


def test_invalid_extraction_stops_downstream_after_one_bounded_repair():
    def mutate(stage,v):
        if stage=="requirements":v["requirements"]=[]
    provider=Capture(mutate);t=run_case(provider)
    assert t["state"]=="failed" and t["usage"]["model_calls"]==3
    assert t["usage"]["feedback_loops"]==1 and len(provider.workers)==2
    assert [n["state"] for n in t["report"]["nodes"]]==["failed","skipped","skipped"]


@pytest.mark.parametrize("kind",["omission","numeric_conflict","negation","unit","forged"])
def test_actual_engine_rejects_bad_final_even_when_upstream_is_valid(kind):
    def mutate(stage,v):
        if stage!="delivery":return
        if kind=="omission":v["markdown"]="Mock explanatory prose without required rules. "*40
        if kind=="numeric_conflict":v["markdown"]+="\n"+RULE.replace("15","30")
        if kind=="negation":v["markdown"]=v["markdown"].replace("must","must not")
        if kind=="unit":v["markdown"]=v["markdown"].replace("minutes","seconds")
        if kind=="forged":v["citations"][0]["chunk_id"]="forged"
    provider=Capture(mutate);t=run_case(provider)
    assert t["state"]=="failed" and not t["report"]["verified"]
    assert t["usage"]["model_calls"]==5 and t["usage"]["feedback_loops"]==1
    assert [n["state"] for n in t["report"]["nodes"]]==["succeeded","succeeded","failed"]
    assert t["delivery"] is None


def test_final_actual_feedback_is_received_and_repaired_under_same_budget():
    def mutate(stage,v):
        if stage=="delivery":v["markdown"]="Mock explanatory prose without required rules. "*40
    provider=Capture(mutate,False);t=run_case(provider)
    assert t["state"]=="succeeded" and t["report"]["verified"]
    assert t["usage"]["model_calls"]==5 and t["usage"]["feedback_loops"]==1
    repair=json.loads(provider.workers[-1].messages[1]["content"])["recovery"]
    assert repair["previous_artifact"]["content"]["markdown"].startswith("Mock explanatory prose")
    evidence=next(x["details"] for x in repair["verification"]["evidence"]
                  if x.get("details",{}).get("verification_contract")=="staged-rules-v1")
    assert evidence["stage"]=="delivery" and evidence["body_errors"]
    assert evidence["body_errors"][0]["missing_clauses"]


def test_contract_cannot_be_requested_without_knowledge_and_document_profile():
    with TestClient(application()) as c:
        response=c.post("/api/v1/workspace/tasks",json={"request":"A test goal","knowledge_contract":"staged-rules-v1"})
        assert response.status_code==422

@pytest.mark.parametrize("kind",["missing_links","not_body","numeric_conflict"])
def test_actual_engine_rejects_design_traceability_or_conflict_failure(kind):
    def mutate(stage,v):
        if stage!="design":return
        if kind=="missing_links":v["design_links"]=[]
        if kind=="not_body":v["design_links"][0]["body_quote"]="This design text never occurred"
        if kind=="numeric_conflict":v["markdown"]+="\n"+RULE.replace("15","30")
    provider=Capture(mutate);t=run_case(provider)
    assert t["state"]=="failed" and t["usage"]["model_calls"]==4
    assert [n["state"] for n in t["report"]["nodes"]]==["succeeded","failed","skipped"]


def test_unknown_real_cost_still_refuses_admission_without_a_model_call():
    with TestClient(application()) as c:
        kb=c.app.state.knowledge.create("SIMULATED admission")["id"]
        c.app.state.knowledge.import_document(kb,"source.txt",RULE.encode())
        r=c.post("/api/v1/workspace/tasks",json={"request":"Extract; design; deliver","mode":"real",
            "authorize_real":True,"budget_cny":2,"output_profile":"design_document",
            "knowledge_contract":"staged-rules-v1","knowledge_base_ids":[kb],"retrieval_topics":[TOPIC]})
        assert r.status_code==403
        assert c.get("/api/v1/workflow-runs").json()==[]


def test_four_call_shared_ceiling_prevents_a_fifth_model_call():
    def mutate(stage,v):
        if stage=="delivery":v["markdown"]="Mock explanatory prose without required rules. "*40
    provider=Capture(mutate)
    with TestClient(application(provider)) as c:
        kb=c.app.state.knowledge.create("SIMULATED bounded budget")["id"]
        c.app.state.knowledge.import_document(kb,"source.txt",RULE.encode())
        t=confirm(c,create(c,request="Extract; design; deliver",knowledge_base_ids=[kb],
            retrieval_topics=[TOPIC],output_profile="design_document",knowledge_contract="staged-rules-v1",max_calls=4))
        assert t["state"]=="failed" and t["usage"]["model_calls"]==4
        assert len(provider.workers)==3


def test_missing_explicit_topics_are_frozen_by_real_local_retrieval_before_planning():
    with TestClient(application()) as c:
        kb=c.app.state.knowledge.create("SIMULATED automatic topics")["id"]
        c.app.state.knowledge.import_document(kb,"source.txt",RULE.encode())
        t=create(c,request="Order lock; architecture design; independent consolidation",
                 knowledge_base_ids=[kb],output_profile="design_document",knowledge_contract="staged-rules-v1")
        assert t["state"]=="awaiting_confirmation"
        assert t["retrieval_topics"] and all(x["required"] for x in t["retrieval_topics"])
        assert t["usage"]["model_calls"]==1


def test_extraction_can_retain_real_source_sentence_terminator_without_losing_a_clause():
    e,d,v=outputs()
    e["requirements"][0]["statement"]=RULE
    assert staged_evidence(e,records(),"digest","requirements",{}).status=="PASSED"
    assert staged_evidence(d,records(),"digest","design",{"s1":e}).status=="PASSED"
    e["requirements"][0]["statement"]=RULE+"?"
    assert staged_evidence(e,records(),"digest","requirements",{}).status=="FAILED"
