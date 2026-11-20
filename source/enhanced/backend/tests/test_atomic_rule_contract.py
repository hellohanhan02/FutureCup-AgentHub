"""Atomic knowledge contract regression: all models are explicitly Mock."""
import json
import pytest
from fastapi.testclient import TestClient
from futurecup.adapters.coverage import retrieve_topics, knowledge_evidence
from futurecup.adapters.knowledge import terms
from futurecup.adapters.rule_contract import atoms
from test_workspace_11a import application, create, confirm

RULE="订单锁定：订单创建后必须锁定商品15分钟，锁定期间不得被其他用户重复下单"
TOPIC={"id":"lock","query":"订单锁定","required":True}


def record(strict=False):
    hit={"id":"chunk","text":RULE,"score":0}
    r=retrieve_topics(lambda *a:[hit],terms,["kb"],"",[TOPIC])
    if strict:r["rule_contract"]="atomic-body-v1"
    return r


def value(strict=False):
    body="| 编号 | 业务要求 |\n|---|---|\n"
    clauses=atoms(RULE,TOPIC["query"])
    for i,c in enumerate(clauses):body+=f"| R{i} | {c['text']} |\n"
    v={"markdown":body,"citations":[{"chunk_id":"chunk","quote":RULE}]}
    if strict:v["rule_coverage"]=[{"topic_id":"lock","clause_id":c["id"],"chunk_id":"chunk","body_quote":c["text"]} for c in clauses]
    return v


def test_historical_complete_atomic_constraints_across_rows_are_accepted():
    assert knowledge_evidence(value(),[record()],"digest").status=="PASSED"


def test_new_explicit_links_are_independently_bound_to_the_body():
    v=value(True);r=record(True)
    assert knowledge_evidence(v,[r],"digest").status=="PASSED"
    del v["rule_coverage"]
    assert knowledge_evidence(v,[r],"digest").status=="FAILED"


@pytest.mark.parametrize("mutation",["missing_body","quote_only","wrong_number","wrong_unit","negation","missing_clause","forged_source","forged_clause","wrong_body_quote","invented_topic","extra_conflict"])
def test_structured_claims_never_override_missing_or_wrong_body(mutation):
    v=value(True)
    if mutation=="missing_body":v["markdown"]="文档只有模块列表"
    if mutation=="quote_only":v["markdown"]="## 引用\n"+RULE
    if mutation=="wrong_number":v["markdown"]=v["markdown"].replace("15","30")
    if mutation=="wrong_unit":v["markdown"]=v["markdown"].replace("分钟","秒")
    if mutation=="negation":v["markdown"]=v["markdown"].replace("必须锁定","无需锁定")
    if mutation=="missing_clause":v["markdown"]=v["markdown"].split("| R1")[0]
    if mutation=="forged_source":v["citations"][0]["chunk_id"]="forged"
    if mutation=="forged_clause":v["rule_coverage"][0]["clause_id"]="forged"
    if mutation=="wrong_body_quote":v["rule_coverage"][0]["body_quote"]="不存在的正文"
    if mutation=="invented_topic":v["rule_coverage"].append({**v["rule_coverage"][0],"topic_id":"invented"})
    if mutation=="extra_conflict":v["markdown"]+="\n订单锁定：必须锁定商品30分钟"
    assert knowledge_evidence(v,[record(True)],"digest").status=="FAILED"


def test_condition_is_not_detached_from_predicate():
    assert len(atoms("支付条件：如果未付款，允许取消订单","支付条件"))==1
    assert len(atoms("If unpaid: Buyer must cancel the order","If unpaid"))==1


def test_out_of_scope_does_not_allow_conflicting_assertions():
    r=record();r["coverage"][0]["required"]=False
    v=value();v["markdown"]+="\n订单锁定：必须锁定商品30分钟"
    assert knowledge_evidence(v,[r],"digest").status=="FAILED"


def test_document_schema_prompt_scope_and_real_engine_mock_end_to_end():
    with TestClient(application()) as c:
        kb=c.app.state.knowledge.create("SIMULATED source")["id"]
        c.app.state.knowledge.import_document(kb,"rules.md",RULE.encode())
        task=confirm(c,create(c,request="Order lock requirements; architecture design; independent review",knowledge_base_ids=[kb],
            retrieval_topics=[TOPIC],output_profile="design_document"))
        assert task["state"]=="succeeded",task
        assert task["simulated"] and task["report"]["verified"]
        assert task["usage"]["model_calls"]<=6 and task["usage"]["feedback_loops"]<=1
        for n in task["report"]["nodes"]:
            assert n["input"]["rule_contract"]=="atomic-body-v1"
            assert n["output"]["rule_coverage"]

@pytest.mark.parametrize("scope,accepted",[(["lock"],True),(["invented"],False),([],True)])
def test_only_confirmed_valid_middle_scope_can_narrow_role(scope,accepted):
    from futurecup.application.planner import TaskPlanner
    from futurecup.domain.errors import RuntimeFault
    planner=TaskPlanner.__new__(TaskPlanner)
    steps=[{"id":"s1","role":"requirements","input_from":[]},
           {"id":"s2","role":"design","input_from":["s1"]},
           {"id":"s3","role":"review","input_from":["s1","s2"]}]
    if scope:steps[1]["knowledge_topics"]=scope
    task={"output_profile":"design_document","plan":{"steps":steps},"retrieval_topics":[TOPIC]}
    if accepted:planner.validate_document_plan(task)
    else:
        with pytest.raises(RuntimeFault,match="Plan topic scope"):
            planner.validate_document_plan(task)


@pytest.mark.parametrize("always_fail",[False,True])
def test_explicit_body_link_failure_uses_real_feedback_and_shared_limit(always_fail):
    from dataclasses import replace
    from futurecup.adapters.llm import MockLLMProvider
    class BodyFailure(MockLLMProvider):
        def __init__(self):self.workers=[]
        async def infer(self,r):
            result=await super().infer(r)
            if r.model=="mock-workspace-worker":
                self.workers.append(r)
                if len(self.workers)==1 or always_fail:
                    v=json.loads(result.content)
                    v["markdown"]="[MOCK missing operational assertions; actual source links alone cannot prove implementation.] "*2
                    return replace(result,content=json.dumps(v))
            return result
    provider=BodyFailure()
    with TestClient(application(provider)) as c:
        kb=c.app.state.knowledge.create("SIMULATED controlled failure")["id"]
        c.app.state.knowledge.import_document(kb,"rules.md",RULE.encode())
        task=confirm(c,create(c,request="Order lock requirements; architecture design; independent review",
            knowledge_base_ids=[kb],retrieval_topics=[TOPIC],output_profile="design_document"))
        assert task["state"]==("failed" if always_fail else "succeeded")
        assert task["usage"]["model_calls"]==(3 if always_fail else 5)
        assert task["usage"]["feedback_loops"]==1
        assert len(provider.workers)==(2 if always_fail else 4)
        recovery=json.loads(provider.workers[1].messages[1]["content"])["recovery"]
        assert recovery["previous_artifact"]["content"]["markdown"].startswith("[MOCK missing")
        detail=next(e["details"] for e in recovery["verification"]["evidence"] if
                    e.get("details",{}).get("verification_contract")=="knowledge-rules-v3")
        assert detail["citation_check"]["status"]=="PASSED"
        assert len(detail["coverage"])==1
        assert len(detail["coverage"][0]["missing_clauses"])==2
        assert "excluded_regions" not in json.dumps(detail)
        assert detail["coverage"][0]["topic_id"]=="lock"
