"""Offline protocol acceptance: actual adapter -> loopback HTTP fixture, not a model."""
from pathlib import Path
import argparse,asyncio,http.server,json,os,threading,time,sys
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--version',choices=['stable','enhanced'],default='stable');args=p.parse_args()
sys.path.insert(0,str(ROOT/'source'/args.version/'backend/src'))
from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.adapters.provider_config import validate_origin,profiles_from
from futurecup.config import Settings
from dataclasses import asdict
from futurecup.domain.llm import ProviderProfile,ModelPolicy
from futurecup.application.ports import ModelRequest
from futurecup.domain.errors import RuntimeFault
SYNTHETIC='LOCAL_PROTOCOL_FIXTURE_NOT_A_REAL_KEY';observations=[]
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.path=='/custom/v1/chat/completions'
        assert self.headers.get('Authorization')=='Bearer '+SYNTHETIC
        assert body['model']=='judge-protocol-model' and body['max_tokens']==64
        assert body['stream'] is False
        mode=body['messages'][-1]['content'];observations.append({'path':self.path,'model':body['model'],'max_tokens':body['max_tokens'],'authorization_exact':True,'case':mode})
        if mode=='timeout':time.sleep(.3)
        status=401 if mode=='auth_error' else 429 if mode=='rate_limit' else 200
        message={'role':'assistant','content':'protocol fixture response'};finish='stop'
        if mode=='tool':
            assert body['tools'][0]['function']['name']=='calculator'
            message={'role':'assistant','content':None,'tool_calls':[{'id':'local-call','type':'function','function':{'name':'calculator','arguments':'{"a":2,"b":3}'}}]};finish='tool_calls'
        value={'choices':[{'message':message,'finish_reason':finish}],'model':'judge-protocol-model','usage':{'prompt_tokens':8,'completion_tokens':3}}
        if status!=200:value={'error':{'message':SYNTHETIC+' do not expose error body'}}
        raw=b'not-json' if mode=='bad_json' else json.dumps(value).encode()
        try:self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
async def main():
    base='http://127.0.0.1:'+str(server.server_port)+'/custom/v1'
    profile=ProviderProfile('local_fixture','openai_compatible',base,'JUDGE_PROTOCOL_FIXTURE_KEY',(ModelPolicy('judge-protocol-model',supports_tools=True,max_output_tokens=64),),allowed_hosts=('127.0.0.1',),enabled=True,allow_loopback=True)
    profile=next(x for x in profiles_from(Settings('judge_protocol','sqlite:///:memory:',provider_config_json=json.dumps([asdict(profile)]))) if x.id=='local_fixture')
    provider=OpenAICompatibleProvider(base,'JUDGE_PROTOCOL_FIXTURE_KEY',True,profile=profile)
    checks=[]
    for mode in ['success','tool','auth_error','rate_limit','bad_json','timeout']:
        tools=({'type':'function','function':{'name':'calculator','description':'synthetic test','parameters':{'type':'object'}}},) if mode=='tool' else ()
        request=ModelRequest('fixture',({'role':'user','content':mode},),64,.05 if mode=='timeout' else 2,'local-trace',model='judge-protocol-model',tools=tools)
        try:
            result=await provider.infer(request)
            assert mode in ['success','tool'] and result.input_tokens==8 and result.output_tokens==3
            if mode=='tool':assert result.tool_calls[0]['name']=='calculator'
            checks.append({'case':mode,'passed':True,'usage_reported':result.usage_reported})
        except RuntimeFault as error:
            expected={'auth_error':'provider_auth_error','rate_limit':'provider_rate_limited','bad_json':'provider_response_invalid','timeout':'model_timeout'}
            assert mode in expected and error.code==expected[mode]
            assert SYNTHETIC not in str(error) and 'do not expose' not in str(error)
            checks.append({'case':mode,'passed':True,'error_code':error.code,'http_status':error.provider_http.status_code if error.provider_http else None})
    for endpoint in ['https://127.0.0.1/v1','http://external.example/v1','https://user:password@approved.example/v1','https://approved.example/v1?secret=fixture']:
        try:validate_origin(ProviderProfile('invalid','openai_compatible',endpoint,'JUDGE_PROTOCOL_FIXTURE_KEY',profile.models,allowed_hosts=('approved.example',)))
        except ValueError:checks.append({'case':'unsafe_endpoint_rejected','passed':True})
        else:raise AssertionError('Unsafe endpoint accepted')
    return checks
os.environ['JUDGE_PROTOCOL_FIXTURE_KEY']=SYNTHETIC
try:checks=asyncio.run(main())
finally:os.environ.pop('JUDGE_PROTOCOL_FIXTURE_KEY',None);server.shutdown();server.server_close()
result={'scope':'LOCAL_HTTP_FIXTURE_NOT_REAL_LLM','version':args.version,'external_requests_sent':0,'loopback_requests':len(observations),'authorization_values_logged':False,'configuration_loaded_through_server_parser':True,'checks':checks,'observations':observations}
folder=ROOT/'.validation';folder.mkdir(exist_ok=True);(folder/('provider-protocol-'+args.version+'.json')).write_text(json.dumps(result,indent=2));print(json.dumps({'version':args.version,'passed':len(checks),'external_requests_sent':0}))