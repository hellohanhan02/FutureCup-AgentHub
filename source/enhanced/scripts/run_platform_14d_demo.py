"""Bounded loopback HTTP system exercise. Requires the explicit Mock-only service."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse
from uuid import uuid4
import httpx
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',default='http://127.0.0.1:18031');p.add_argument('--output',default='results/platform-14d/http-acceptance');args=p.parse_args()
    url=urlparse(args.url)
    if url.scheme!='http' or url.hostname!='127.0.0.1' or url.username or url.password or url.path or url.query or url.fragment:raise ValueError('Loopback origin only')
    folder=(ROOT/args.output).resolve()
    if not folder.is_relative_to(ROOT/'results/platform-14d') or folder.exists():raise ValueError('Choose a NEW results/platform-14d directory')
    folder.mkdir(parents=True)
    with httpx.Client(base_url=args.url,timeout=5,trust_env=False) as client:
        cap=client.get('/api/v1/platform/demo-capabilities');cap.raise_for_status()
        if cap.json()!={'available':True,'mode':'mock_only','real_calls_enabled':False}:raise ValueError('Explicit Mock service required')
        def finish(identifier):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                value=client.get('/api/v1/workflow-runs/'+identifier);value.raise_for_status();value=value.json()
                if value['run']['state'] in ('failed','succeeded','cancelled'):return value
                time.sleep(.05)
            raise TimeoutError('Mock workflow did not finish')
        request_id=uuid4().hex
        start=client.post('/api/v1/platform/mock-demo',json={'request_id':request_id,'authorize_mock':True});start.raise_for_status()
        source=finish(start.json()['run']['id'])
        assert source['run']['state']=='failed' and source['simulated'] is True
        first=client.get('/api/v1/workflow-runs/'+source['run']['id']+'/observability');first.raise_for_status()
        result=client.post('/api/v1/workflow-runs/'+source['run']['id']+'/recover',json={'request_id':uuid4().hex,'expected_revision':source['run']['revision'],'authorize_recovery':True});result.raise_for_status()
        final=finish(result.json()['run']['id'])
        assert final['verified'] and final['simulated'] is True and final['nodes'][0]['reused_from']
        observed=client.get('/api/v1/workflow-runs/'+final['run']['id']+'/observability');observed.raise_for_status()
        export=client.get('/api/v1/workflow-runs/'+final['run']['id']+'/report-export',params={'expected_revision':final['run']['revision']});export.raise_for_status()
        assert export.json()['run_id']==final['run']['id']
        assert observed.json()['ledger']['occupied_calls']==final['resource_usage']['calls']
        saved={'source-observation.json':first.json(),'recovered-observation.json':observed.json(),'exported-report.json':export.json()}
        summary={'mode':'MOCK_ONLY_ACTUAL_ENGINE','source_run_id':source['run']['id'],'recovered_run_id':final['run']['id'],
            'source_state':source['run']['state'],'source_verified':source['verified'],'final_state':final['run']['state'],'final_verified':final['verified'],
            'reused_nodes':final['recovery_audit']['retained_nodes'],'reexecuted_nodes':final['recovery_audit']['reexecuted_nodes'],
            'usage':final['resource_usage'],'ledger':observed.json()['ledger'],'real_requests_sent':0,
            'note':'Injected Mock rule omission; independent verifier and actual feedback/recovery. Token usage unreported, quota units are not CNY billing.'}
        saved['summary.json']=summary
        for name,value in saved.items():(folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
        manifest={'files':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in saved},
            'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (ROOT/'backend/src/futurecup').rglob('*.py')},
            'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
        print(json.dumps({k:v for k,v in summary.items() if k!='ledger'},ensure_ascii=False))
if __name__=='__main__':main()
