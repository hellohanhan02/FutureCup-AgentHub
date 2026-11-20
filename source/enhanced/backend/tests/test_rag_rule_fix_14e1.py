"""14-E1-Fix deterministic format/constraint tests. No model or network."""
import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.coverage import retrieve_topics, assess_output, knowledge_evidence
from futurecup.adapters.knowledge import terms
from futurecup.adapters.rule_body import canonical, extract
from test_rag_14a import Capture
from test_workspace_11a import application, create, confirm

RULE = "订单锁定：订单创建后必须锁定商品15分钟"
TOPIC = {"id":"lock","query":"订单锁定","required":True}


def record(rule=RULE, topic=TOPIC):
    hit = {"id":"unit-chunk","text":rule+"。","score":0,"doc_id":"unit-doc","document_version":1}
    return retrieve_topics(lambda *args:[hit],terms,["unit-kb"],"",[topic])


def value(markdown, rule=RULE):
    return {"markdown":markdown,"citations":[{"chunk_id":"unit-chunk","quote":rule}]}


def state(markdown, rule=RULE, topic=TOPIC):
    return assess_output(value(markdown,rule),[record(rule,topic)])[0]


@pytest.mark.parametrize("body", [
    RULE, "**订单锁定**：订单创建后必须锁定商品15分钟",
    "*订单锁定*：订单创建后必须锁定商品15分钟",
    "__订单锁定__：订单创建后必须锁定商品15分钟",
    "_订单锁定_：订单创建后必须锁定商品15分钟",
    "- **订单锁定**：订单创建后必须锁定商品15分钟",
    "1. 订单锁定：订单创建后必须锁定商品15分钟",
    "订单锁定：订单创建后必须\n锁定商品15分钟",
    "订单锁定：订单创建后必须锁定商品15 分钟",
    "|主题|要求|\n|---|---|\n|订单锁定|订单创建后必须锁定商品15分钟|",
    "[订单锁定](https://untrusted.invalid)：订单创建后必须锁定商品15分钟",
])
def test_visible_markdown_format_compatibility_has_trace(body):
    r=state(body)
    assert r["status"]=="RULE_REFLECTED",r
    witness=r["evidence"]["body_matches"][0]
    assert body[witness["start"]:witness["end"]]==witness["raw"]
    assert witness["normalized"] and witness["line_start"]>=1
    assert r["evidence"]["expected_normalized"]==canonical(RULE)


@pytest.mark.parametrize("body", [
    RULE.replace("15","30"), RULE.replace("分钟","秒"),
    "订单锁定：无需锁定商品15分钟",
    RULE+"。\n订单锁定：必须锁定商品30分钟",
    RULE+"。\n订单锁定：必须锁定商品15秒",
    RULE+"。\n订单锁定：无需锁定商品15分钟",
    RULE+"。\n订单锁定：必须锁定商品至少15分钟",
])
def test_number_unit_polarity_and_additional_contradiction_never_pass(body):
    r=state(body)
    assert r["status"]!="RULE_REFLECTED",r
    assert r["status"]=="POSSIBLE_CONFLICT"
    assert r["evidence"]["conflicts"]


@pytest.mark.parametrize("wrapper", [
    lambda r:"```text\n"+r+"\n```",
    lambda r:"~~~\n"+r+"\n~~~",
    lambda r:"    "+r,
    lambda r:"<!-- "+r+" -->",
    lambda r:"<span hidden>"+r+"</span>",
    lambda r:"<div hidden>\n<div>\n</div>\n"+r+"\n</div>",
    lambda r:"> "+r,
    lambda r:"## 引用\n"+r,
    lambda r:"## Sources\n"+r,
    lambda r:"任务输入：\n"+r,
    lambda r:"反馈：\n"+r,
    lambda r:"~~"+r+"~~",
    lambda r:"|主题|引用原文|\n|---|---|\n|订单锁定|"+r+"|",
    lambda r:"|主题|要求|\n|---|---|\n|订单锁定|<span hidden>"+r+"</span>|",
    lambda r:"<!-- one --> <!-- two\n"+r+"\n-->",
])
def test_quote_hidden_input_and_code_are_not_deliverable_body(wrapper):
    assert state(wrapper(RULE))["status"]!="RULE_REFLECTED"


def test_citations_metadata_summary_and_items_cannot_replace_markdown_body():
    v=value("实际交付只有接口列表")
    v.update({"summary":RULE,"items":[RULE],"task_input":RULE,"prompt":RULE,"feedback":RULE})
    result=knowledge_evidence(v,[record()],"unit-digest")
    assert result.status=="FAILED"
    assert result.reason=="knowledge_rule_not_reflected"
    assert result.details["citation_check"]["status"]=="PASSED"
    assert result.details["coverage"][0]["status"]=="SOURCE_NOT_REFLECTED"


def test_separated_numeric_digits_are_not_reassembled():
    assert state(RULE.replace("15","1 5"))["status"]!="RULE_REFLECTED"


@pytest.mark.parametrize("rule,opposite", [
    ("订单取消：平台允许取消订单","订单取消：平台不允许取消订单"),
    ("订单取消：平台不允许取消订单","订单取消：平台允许取消订单"),
    ("订单取消：买家必须能在未付款时取消订单","订单取消：买家必须能在已付款时取消订单"),
])
def test_opposite_permission_or_condition_cannot_hide_beside_correct_rule(rule,opposite):
    topic={"id":"cancel","query":"订单取消","required":True}
    r=state(rule+"。\n"+opposite,rule,topic)
    assert r["status"]=="POSSIBLE_CONFLICT",r
    assert r["validation_type"]=="RULE_CONFLICT"


def test_forged_reference_remains_a_source_failure():
    v=value(RULE);v["citations"][0]["chunk_id"]="fabricated"
    e=knowledge_evidence(v,[record()],"digest")
    assert e.reason=="knowledge_citation_invalid"
    assert e.details["citation_check"]["status"]=="FAILED"


def test_partial_paraphrase_is_uncertain_not_semantically_proven():
    r=state("订单锁定：锁定商品时长为15分钟")
    assert r["status"]=="SEMANTIC_UNCERTAIN",r
    assert r["evidence"]["related_body"]


def test_sentence_ranking_is_topic_specific_not_document_order():
    text="身份认证：用户必须认证后才能发布商品。\n商品发布：卖家必须提供商品名称和真实图片。"
    topics=[{"id":"listing","query":"商品发布","required":True},{"id":"identity","query":"身份认证","required":True}]
    hit={"id":"same","text":text,"score":0}
    r=retrieve_topics(lambda *a:[hit],terms,["kb"],"",topics)
    assert r["coverage"][0]["critical_rule"].startswith("商品发布：")
    assert r["coverage"][1]["critical_rule"].startswith("身份认证：")
    s=r["coverage"][0]["rule_selection"]
    assert s["selected"]["score"]==1 and s["reason"]=="strongest_actual_sentence"
    assert text[s["selected"]["start"]:s["selected"]["end"]]==s["selected"]["text"]


def test_conflicting_equal_relevance_is_partial_and_repeatable():
    hits=[{"id":"b","text":"Order lock must expire after 30 minutes.","score":0},
          {"id":"a","text":"Order lock must expire after 15 minutes.","score":0}]
    topic={"id":"x","query":"order lock","required":True}
    a=retrieve_topics(lambda *args:hits,terms,["kb"],"",[topic])
    b=retrieve_topics(lambda *args:list(reversed(hits)),terms,["kb"],"",[topic])
    assert a["coverage"]==b["coverage"]
    assert a["coverage"][0]["status"]=="PARTIAL"
    assert a["coverage"][0]["critical_rule"] is None
    assert a["coverage"][0]["rule_selection"]["reason"]=="ambiguous_equal_relevance"


def test_one_actual_rule_may_support_two_related_topics():
    hit={"id":"same","text":"Order lock expiry must be 15 minutes.","score":0}
    topics=[{"id":"a","query":"order lock","required":True},{"id":"b","query":"lock expiry","required":True}]
    r=retrieve_topics(lambda *args:[hit],terms,["kb"],"",topics)
    assert all(t["status"]=="COVERED" for t in r["coverage"])
    assert r["coverage"][0]["critical_rule"]==r["coverage"][1]["critical_rule"]


def test_existing_selfloop_receives_only_actual_missing_body_failure_and_remains_bounded():
    provider=Capture(omit=True)
    with TestClient(application(provider)) as c:
        kb=c.app.state.knowledge.create("SIMULATED test source")["id"]
        c.app.state.knowledge.import_document(kb,"source.txt",b"Order lock must expire after 15 minutes.")
        task=confirm(c,create(c,request="Order lock; design architecture; review",knowledge_base_ids=[kb],
            retrieval_topics=[{"id":"lock","query":"order lock","required":True}]))
        assert task["state"]=="succeeded" and task["simulated"]
        assert task["usage"]["model_calls"]==5 and task["usage"]["feedback_loops"]==1
        requests=[r for r in provider.requests if r.model=="mock-workspace-worker"]
        repair=json.loads(requests[1].messages[1]["content"])["recovery"]
        assert repair["previous_artifact"]["content"]["items"]==["[MOCK intentionally incomplete contribution]"]
        evidence=repair["verification"]["evidence"][0]
        assert evidence["reason"]=="knowledge_rule_not_reflected"
        assert evidence["details"]["citation_check"]["status"]=="PASSED"
        assert all(t["status"]!="RULE_REFLECTED" for t in evidence["details"]["coverage"])



def test_shared_verb_with_different_object_is_not_a_polarity_conflict():
    rule="发布限制：平台禁止发布账号"
    topic={"id":"restriction","query":"发布限制","required":True}
    r=state(rule+"。\n认证：用户必须完成认证后才能发布商品",rule,topic)
    assert r["status"]=="RULE_REFLECTED",r


def test_hidden_table_subject_cannot_supply_a_rule():
    body="|主题|要求|\n|---|---|\n|<span hidden>"+RULE+"</span>|普通描述|"
    assert state(body)["status"]!="RULE_REFLECTED"


def test_wrapped_negation_stays_negative_even_when_a_correct_rule_is_present():
    rule="订单取消：平台允许取消订单"
    topic={"id":"cancel","query":"订单取消","required":True}
    assert state(rule+"。\n订单取消：平台不\n允许取消订单",rule,topic)["status"]=="POSSIBLE_CONFLICT"


def test_opposite_permission_on_an_unpunctuated_newline_is_not_hidden():
    rule="订单取消：平台不允许取消订单"
    topic={"id":"cancel","query":"订单取消","required":True}
    assert state(rule+"\n订单取消：平台允许取消订单",rule,topic)["status"]=="POSSIBLE_CONFLICT"


def test_lazy_blockquote_continuation_is_not_body():
    assert state("> 原始资料\n"+RULE)["status"]!="RULE_REFLECTED"


def test_inline_quoted_normative_source_alone_is_not_body():
    assert state("资料摘录为“"+RULE+"”")["status"]!="RULE_REFLECTED"
