"""Task 08: SIMULATED HTTP/Mock generation only. No provider network requests."""

import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.adapters.provider_config import EnvironmentSecretGuard, profiles_from, validate_dns, validate_origin
from futurecup.api.app import create_app
from futurecup.application.ports import ModelRequest
from futurecup.bootstrap import build_runtime
from futurecup.config import Settings
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.llm import ModelPolicy, ProviderProfile, RetryPolicy
from futurecup.domain.models import ExecutionState, RunLimits
from futurecup.domain.verification import ResourceBudget
from runtime_support import agent, service
from test_workflow_api import SETTINGS, completed

KEY = "TASK08_TEST_CREDENTIAL"
FAKE = "task08-fake-credential-not-a-real-key"
ROOT = Path(__file__).resolve().parents[2]


def policy(**changes):
    return ModelPolicy("test-model", **changes)


def profile(**changes):
    return replace(ProviderProfile("configured", "openai_compatible", "https://model.example/v1", KEY,
        (policy(),), ("model.example",), True, 1), **changes)


def completion(content="[SIMULATED HTTP] result", *, usage=True, model="server-snapshot", tools=None):
    value = {"model": model, "id": "simulated-request", "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
    if tools:
        value["choices"][0]["message"]["tool_calls"] = tools
        value["choices"][0]["finish_reason"] = "tool_calls"
    if usage:
        value["usage"] = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    return value


def request(**changes):
    return replace(ModelRequest("test", ({"role": "user", "content": "JSON test"},), 32, 1, "trace", "test-model"), **changes)


def managed(handler, configuration=None):
    configuration = configuration or profile()
    return OpenAICompatibleProvider(configuration.base_url, configuration.key_env, True, httpx.MockTransport(handler), profile=configuration)


def runtime(handler, configuration=None):
    configuration = configuration or profile()
    value = service()
    value.providers[configuration.id] = managed(handler, configuration)
    value.provider_profiles = (configuration,)
    value.real_calls_enabled = True
    value.sensitive_guard = EnvironmentSecretGuard((KEY,))
    return value


def test_environment_config_default_closed_and_secret_free(monkeypatch):
    monkeypatch.setenv("FUTURECUP_ENABLE_REAL_LLM", "false")
    monkeypatch.setenv("FUTURECUP_LLM_PROVIDERS_JSON", json.dumps([asdict(profile())]))
    monkeypatch.setenv(KEY, FAKE)
    settings = Settings.from_environment()
    value = build_runtime(settings)
    assert set(value.providers) == {"mock"}
    assert value.provider_profiles[-1].models[0].id == "test-model"
    assert FAKE not in repr(settings) + repr(value.provider_profiles)


@pytest.mark.parametrize("patch", [
    {"api_key": "DO-NOT-ECHO-AN-ACCIDENTAL-SECRET"}, {"key_env": "bad-key"},
    {"base_url": "http://model.example/v1"}, {"base_url": "https://evil.example/v1"},
    {"base_url": "https://model.example/v1?key=DO-NOT-ECHO-AN-ACCIDENTAL-SECRET"},
    {"base_url": "https://user:password@model.example/v1"}, {"base_url": "https://model.example:8443/v1"},
    {"base_url": "https://127.0.0.1/v1", "allowed_hosts": ["127.0.0.1"]},
    {"models": [{"id": "test-model", "max_context_bytes": 0}]},
    {"models": [{"id": "test-model", "supports_tools": "yes"}]},
    {"retry": {"max_retries": 4}}, {"enabled": "true"},
])
def test_bad_server_configuration_is_rejected_without_echo(patch):
    value = {**asdict(profile()), **patch}
    with pytest.raises(ValueError) as failure:
        profiles_from(Settings("test", "sqlite:///:memory:", provider_config_json=json.dumps([value])))
    assert "DO-NOT-ECHO" not in str(failure.value)


@pytest.mark.parametrize("configuration", [
    ProviderProfile("deepseek", "deepseek", "https://api.deepseek.com/v1", KEY, (policy(),)),
    ProviderProfile("qwen", "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", KEY, (policy(),)),
    ProviderProfile("qwen", "qwen", "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", KEY, (policy(),)),
    profile(base_url="http://127.0.0.1:1234/v1", allowed_hosts=("127.0.0.1",), allow_loopback=True),
])
def test_official_and_explicit_local_endpoint_validation(configuration):
    validate_origin(configuration)


def test_dns_private_address_is_denied_without_http(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        async def private(*args, **kwargs):
            return [(None, None, None, None, ("169.254.169.254", 443))]
        monkeypatch.setattr(loop, "getaddrinfo", private)
        with pytest.raises(RuntimeFault) as failure:
            await validate_dns(profile())
        assert failure.value.code == "provider_address_denied"
    asyncio.run(scenario())


def test_catalog_is_offline_and_does_not_reveal_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE)
    with TestClient(create_app(SETTINGS)) as client:
        result = client.get("/api/v1/model-providers")
        assert result.status_code == 200 and not result.json()["network_probe_performed"]
        assert not result.json()["real_calls_enabled"] and FAKE not in result.text
        entries = {p["id"]: p for p in result.json()["providers"]}
        assert {"mock", "deepseek", "qwen", "openai_compatible"}.issubset(entries)
        assert entries["deepseek"]["connection"]["state"] == "disabled"
        assert entries["deepseek"]["credentials_configured"]
        assert client.post("/api/v1/model-providers/deepseek/estimate", json={"model": "deepseek-flash", "input_tokens_hint": 100, "max_output_tokens": 32}).json()["estimated_cost"] is None


def test_missing_key_and_capability_fail_before_http(monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    seen = []
    provider = managed(lambda r: seen.append(r))
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request()))
    assert failure.value.code == "provider_credentials_missing" and not seen


@pytest.mark.parametrize("change,code", [
    ({"response_format": "json_object"}, "model_format_unsupported"),
    ({"tools": ({"type": "function", "function": {"name": "calculator"}},)}, "model_tools_unsupported"),
    ({"max_output_tokens": 2048}, "model_output_limit"),
    ({"messages": ({"role": "user", "content": "x" * 1000},)}, "model_context_limit"),
])
def test_model_policy_constraints(monkeypatch, change, code):
    monkeypatch.setenv(KEY, FAKE)
    seen = []
    configuration = profile(models=(policy(max_output_tokens=1024, max_context_bytes=512),))
    provider = managed(lambda r: seen.append(r), configuration)
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request(**change)))
    assert failure.value.code == code and not seen


@pytest.mark.parametrize("status,code", [(401,"provider_auth_error"),(403,"provider_auth_error"),(429,"provider_rate_limited"),(503,"provider_server_error"),(302,"provider_http_error")])
def test_managed_error_classification_and_sanitized_status(monkeypatch,status,code):
    monkeypatch.setenv(KEY,FAKE)
    provider=managed(lambda r:httpx.Response(status,text=FAKE+' private provider body',headers={"Location":"https://evil.example"}))
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request()))
    assert failure.value.code==provider.last_outcome["error_code"]==code
    assert FAKE not in str(failure.value)+json.dumps(provider.last_outcome)


@pytest.mark.parametrize("value", ["bad",0,False,{}," "])
def test_invalid_response_model_metadata(monkeypatch,value):
    monkeypatch.setenv(KEY,FAKE)
    payload=completion(model=value)
    if value=='bad':payload['usage']['total_tokens']=999
    with pytest.raises(RuntimeFault):
        asyncio.run(managed(lambda r:httpx.Response(200,json=payload)).infer(request()))


def test_secret_echo_rejected_and_not_persisted(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    async def scenario():
        value=runtime(lambda r:httpx.Response(200,json=completion(FAKE)))
        await value.registry.register(agent(provider='configured',model='test-model'))
        try:
            result=await value.run('analyst','safe input')
            assert result.execution.error_code=='provider_sensitive_response' and result.output is None
            assert FAKE not in repr(result)
            with pytest.raises(RuntimeFault) as failure:
                await value.submit('analyst',FAKE)
            assert failure.value.code=='sensitive_data_rejected'
        finally:await value.close()
    asyncio.run(scenario())


def test_retry_is_counted_and_usage_on_failed_transport_is_unknown(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    observed=[]
    def handler(r):
        observed.append(r)
        return httpx.Response(429) if len(observed)==1 else httpx.Response(200,json=completion())
    async def scenario():
        value=runtime(handler,profile(retry=RetryPolicy(2,0,0)))
        await value.registry.register(agent(provider='configured',model='test-model'))
        budget=ResourceBudget(max_calls=2)
        try:
            initial=await value.submit('analyst','safe input',shared_budget=budget)
            result=await value.wait(initial.execution.id)
            assert result.execution.state==ExecutionState.SUCCEEDED
            assert len(result.model_calls)==budget.calls==2 and [c.retry_index for c in result.model_calls]==[0,1]
            assert result.input_tokens==3 and result.output_tokens==4 and not result.usage_complete
            assert budget.unknown_usage_calls==1 and budget.reported_tokens==7
            assert result.estimated_cost is None and not result.cost_complete
        finally:await value.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('shared',[True,False])
def test_retry_cannot_bypass_call_limits(monkeypatch,shared):
    monkeypatch.setenv(KEY,FAKE)
    calls=[]
    def handler(r):calls.append(r);return httpx.Response(503)
    async def scenario():
        value=runtime(handler,profile(retry=RetryPolicy(3,0,0)))
        await value.registry.register(agent(provider='configured',model='test-model'))
        try:
            initial=await value.submit('analyst','safe input',RunLimits(max_model_calls=1) if not shared else RunLimits(),shared_budget=ResourceBudget(max_calls=1) if shared else None)
            result=await value.wait(initial.execution.id)
            assert result.execution.error_code==('shared_call_budget' if shared else 'call_limit_exceeded')
            assert len(calls)==len(result.model_calls)==1
        finally:await value.close()
    asyncio.run(scenario())


def test_reported_tokens_model_version_and_configured_cost(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    configuration=profile(models=(policy(input_price_per_million=1,output_price_per_million=2,currency='CNY'),))
    async def scenario():
        value=runtime(lambda r:httpx.Response(200,json=completion()),configuration)
        await value.registry.register(agent(provider='configured',model='test-model'))
        try:
            result=await value.run('analyst','safe input')
            assert result.simulated and result.usage_complete and result.input_tokens==3 and result.output_tokens==4
            assert result.resolved_model=='server-snapshot' and result.provider_config_ref.version=='1'
            assert result.estimated_cost==pytest.approx(.000011) and result.cost_currency=='CNY'
            assert result.model_calls[0].usage_reported
        finally:await value.close()
    asyncio.run(scenario())


def test_native_json_and_deepseek_non_thinking_payload(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    observed=[]
    configuration=ProviderProfile('deepseek','deepseek','https://api.deepseek.com',KEY,(policy(response_formats=('text','json_object'),thinking_disabled=True),),enabled=True)
    provider=managed(lambda r:(observed.append(json.loads(r.content)) or httpx.Response(200,json=completion('{"answer":42}'))),configuration)
    result=asyncio.run(provider.infer(request(response_format='json_object')))
    assert result.content=='{"answer":42}' and provider.is_simulated
    assert observed[0]['response_format']=={'type':'json_object'} and observed[0]['thinking']=={'type':'disabled'}


def test_prompt_json_compatibility_and_invalid_json_keeps_reported_usage(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    observed=[]
    def handler(r):observed.append(json.loads(r.content));return httpx.Response(200,json=completion('not JSON'))
    async def scenario():
        value=runtime(handler)
        await value.registry.register(agent(provider='configured',model='test-model',response_format='prompt_json'))
        try:
            result=await value.run('analyst','safe input')
            assert result.execution.error_code=='model_json_invalid' and result.input_tokens==3 and result.output_tokens==4
            assert result.usage_complete and 'response_format' not in observed[0]
            assert 'JSON' in observed[0]['messages'][0]['content']
        finally:await value.close()
    asyncio.run(scenario())


def test_function_call_context_roundtrip_with_real_runtime_tool(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    observed=[]
    def handler(r):
        value=json.loads(r.content);observed.append(value)
        if len(observed)==1:
            return httpx.Response(200,json=completion(None,tools=[{'id':'call','type':'function','function':{'name':'calculator','arguments':'{"operation":"add","a":2,"b":3}'}}]))
        assert [m['role'] for m in value['messages']]==['system','user','assistant','tool']
        assert json.loads(value['messages'][-1]['content'])=={'result':5}
        return httpx.Response(200,json=completion())
    async def scenario():
        from futurecup.domain.models import VersionRef
        value=runtime(handler,profile(models=(policy(supports_tools=True),)))
        await value.registry.register(agent(provider='configured',model='test-model',tool_refs=(VersionRef('calculator','1'),)))
        try:
            result=await value.run('analyst','calculate 2+3')
            assert result.execution.state==ExecutionState.SUCCEEDED and result.call_count==3
            assert result.input_tokens==6 and result.output_tokens==8 and result.tool_calls[0].output=={'result':5}
        finally:await value.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel',[True,False])
def test_retry_backoff_obeys_cancellation_and_total_timeout(monkeypatch,cancel):
    monkeypatch.setenv(KEY,FAKE)
    async def scenario():
        called=asyncio.Event()
        def handler(r):called.set();return httpx.Response(503)
        value=runtime(handler,profile(retry=RetryPolicy(3,1,1)))
        await value.registry.register(agent(provider='configured',model='test-model'))
        try:
            initial=await value.submit('analyst','safe input',RunLimits(timeout_seconds=.03 if not cancel else 2))
            await called.wait()
            result=await value.cancel(initial.execution.id) if cancel else await value.wait(initial.execution.id)
            assert result.execution.state==(ExecutionState.CANCELLED if cancel else ExecutionState.TIMED_OUT)
            assert len(result.model_calls)==1
        finally:await value.close()
    asyncio.run(scenario())


def test_agent_model_switch_catalog_and_estimate_api(monkeypatch):
    monkeypatch.setenv(KEY,FAKE)
    configuration=profile(models=(policy(input_price_per_million=1,output_price_per_million=2,currency='CNY'),ModelPolicy('other-model')))
    seen=[]
    def handler(r):seen.append(json.loads(r.content)['model']);return httpx.Response(200,json=completion())
    value=runtime(handler,configuration)
    with TestClient(create_app(SETTINGS,value)) as client:
        from test_workflow_api import AGENT
        dto={**AGENT,'provider':'configured','model':'test-model'}
        assert client.post('/api/v1/agents',json=dto).status_code==201
        created=client.post('/api/v1/agents/analyst/executions',json={'input':'safe input'})
        first=client.portal.call(value.wait,created.json()['execution']['id'])
        assert first.model=='test-model'
        fields={k:v for k,v in dto.items() if k not in {'id','system_prompt'}}
        assert client.patch('/api/v1/agents/analyst',json={**fields,'model':'other-model','version':'2','expected_version':'1'}).status_code==200
        created=client.post('/api/v1/agents/analyst/executions',json={'input':'safe input'})
        second=client.portal.call(value.wait,created.json()['execution']['id'])
        assert second.model=='other-model' and first.model=='test-model' and seen==['test-model','other-model']
        result=client.post('/api/v1/model-providers/configured/estimate',json={'model':'test-model','input_tokens_hint':100,'max_output_tokens':50,'calls':2})
        assert result.json()['estimated_cost']==pytest.approx(.0004) and not result.json()['exact_cost_hard_budget']
        catalog=client.get('/api/v1/model-providers')
        assert FAKE not in catalog.text and catalog.json()['providers'][-1]['connection']['state']=='protocol_simulated'


@pytest.mark.parametrize('name',['requirements','data_analysis','selfloop'])
def test_task08_scenarios_use_workflow_caas_and_independent_verification(name):
    folder=ROOT/'frontend/src/examples/llm08'
    with TestClient(create_app(SETTINGS)) as client:
        for value in json.loads((folder/'agents.json').read_text('utf-8')):
            assert client.post('/api/v1/agents',json=value).status_code==201
        for value in json.loads((folder/'policies.json').read_text('utf-8')):
            assert client.post('/api/v1/verification-policies',json=value).status_code==201
        flow=json.loads((folder/(name+'.json')).read_text('utf-8'))
        assert client.post('/api/v1/workflows/validate',json=flow).status_code==200
        assert client.post('/api/v1/workflows',json=flow).status_code==201
        inputs=json.loads((folder/'inputs.json').read_text('utf-8'))
        submitted=client.post('/api/v1/workflows/'+flow['id']+'/executions',json={'input':inputs[name]})
        report=completed(client,submitted.json()['run']['id'])
        assert report['run']['state']=='succeeded' and report['verified'] and report['simulated']
        assert all(n['provider']=='mock' and n['model']=='mock-scenario08' and not n['usage_complete'] for n in report['nodes'])
        decisions=client.get('/api/v1/scheduling/decisions?run_id='+report['run']['id']).json()
        assert decisions and all(d['strategy']=='caas' for d in decisions)
        if name=='selfloop':
            values=client.get('/api/v1/self-loops/'+report['nodes'][0]['id']+'/verifications').json()
            assert [v['status'] for v in values]==['FAILED','PASSED']
            assert len(report['nodes'][0]['attempt_ids'])==2 and report['resource_usage']['unknown_usage_calls']==2
        elif name=='data_analysis':
            assert report['nodes'][-1]['output']['statistics']=={'count':3,'sum':60,'mean':20}
        else:
            assert report['nodes'][1]['input']['upstream']['plan']==report['nodes'][0]['output']


def test_simulated_http_full_workflow_caas_and_schema_recovery(monkeypatch):
    """Actual compatible adapter + Runtime + CAAS + Workflow + Verifier, no live LLM."""
    monkeypatch.setenv(KEY,FAKE)
    from futurecup.bootstrap import build_workflows
    from futurecup.domain.models import NodeKind,VersionRef,WorkflowDefinition,WorkflowNode,WorkflowEdge
    from futurecup.domain.verification import VerificationPolicy,VerificationCheck
    calls=[]
    def handler(r):
        body=json.loads(r.content);value=json.loads(body['messages'][-1]['content']);calls.append(value)
        corrected='recovery' in value or 'answer' in value
        return httpx.Response(200,json=completion(json.dumps({'answer':42} if corrected else {'wrong':0})))
    async def scenario():
        value=runtime(handler)
        engine=build_workflows(SETTINGS,value)
        try:
            for identifier in ('primary','reviewer'):
                await value.registry.register(agent(identifier,provider='configured',model='test-model',response_format='prompt_json'))
            await engine.selfloop.verifier.register(VerificationPolicy(VersionRef('http-policy','1'),'Simulated HTTP schema',
                (VerificationCheck('json_schema',{'schema':{'type':'object','required':['answer'],'properties':{'answer':{'const':42}}}}),)))
            config={'candidate_agents':['primary'],'output_mode':'json','verification':{'id':'http-policy','version':'1'},
                    'recovery':{'max_attempts':2,'max_feedback_loops':1,'on_failure':'prompt_retry'}}
            flow=WorkflowDefinition(VersionRef('protocol-workflow','1'),(WorkflowNode('generate',NodeKind.TASK,config),
                WorkflowNode('review',NodeKind.TASK,{'candidate_agents':['reviewer'],'output_mode':'json','input':{'$ref':'nodes.generate.output'},
                    'verification':{'id':'http-policy','version':'1'}})),(WorkflowEdge('generate','review'),),{},{},4,name='SIMULATED HTTP protocol workflow')
            await engine.register(flow)
            result=await engine.run(flow.ref,{'stage':'generate','mode':'SIMULATED HTTP'})
            assert result.run.state.value=='succeeded' and result.verified and result.simulated
            assert len(calls)==3 and calls[-1]=={'answer':42}
            assert result.nodes[0].input_tokens==6 and result.nodes[0].output_tokens==8 and result.nodes[0].usage_complete
            assert result.nodes[1].input_tokens==3 and result.nodes[1].resolved_model=='server-snapshot'
            assert result.resource_usage['reported_tokens']==21 and result.resource_usage['unknown_usage_calls']==0
            assert len(await engine.scheduler.repository.decisions(run_id=result.run.id))==3
            audits=await engine.selfloop.repository.attempts(result.nodes[0].id)
            assert [a['input_tokens'] for a in audits]==[3,3] and all(a['provider_config_ref']['id']=='configured' for a in audits)
        finally:
            await engine.close();await value.close()
    asyncio.run(scenario())


def test_changed_dataset_fails_frozen_numeric_standard():
    folder=ROOT/'frontend/src/examples/llm08'
    with TestClient(create_app(SETTINGS)) as client:
        for value in json.loads((folder/'agents.json').read_text('utf-8')):assert client.post('/api/v1/agents',json=value).status_code==201
        for value in json.loads((folder/'policies.json').read_text('utf-8')):assert client.post('/api/v1/verification-policies',json=value).status_code==201
        before=client.get('/api/v1/verification-policies/llm08-analysis').json()
        flow=json.loads((folder/'data_analysis.json').read_text('utf-8'))
        assert client.post('/api/v1/workflows',json=flow).status_code==201
        submitted=client.post('/api/v1/workflows/'+flow['id']+'/executions',json={'input':{'values':[10,20,99]}})
        result=completed(client,submitted.json()['run']['id'])
        assert result['run']['state']=='failed' and not result['verified']
        assert result['nodes'][1]['state']=='failed' and result['nodes'][1]['output'] is None and result['nodes'][2]['state']=='skipped'
        assert client.get('/api/v1/verification-policies/llm08-analysis').json()==before


@pytest.mark.parametrize('thinking',[True,False])
def test_unsupported_tool_format_combinations_fail_without_http(monkeypatch,thinking):
    monkeypatch.setenv(KEY,FAKE)
    if thinking:
        configuration=ProviderProfile('deepseek','deepseek','https://api.deepseek.com',KEY,
            (policy(supports_tools=True,thinking_disabled=False),),enabled=True)
        mode='text'
    else:
        configuration=profile(models=(policy(supports_tools=True,response_formats=('text','json_object'),tools_with_json=False),))
        mode='json_object'
    calls=[]
    provider=managed(lambda r:calls.append(r),configuration)
    with pytest.raises(RuntimeFault) as failure:
        asyncio.run(provider.infer(request(response_format=mode,tools=({'type':'function','function':{'name':'calculator'}},))))
    assert failure.value.code=='model_combination_unsupported' and not calls
