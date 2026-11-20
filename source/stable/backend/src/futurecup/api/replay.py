"""Standalone, local GET-only replay. Does not import app/runtime/config/providers."""
from copy import deepcopy
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from futurecup.adapters.competition_evidence import DEFAULT_PACKAGE, ROOT, CHARTS, sha


def create_replay_app(directory: Path = DEFAULT_PACKAGE) -> FastAPI:
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT/'.data'):
        raise ValueError('Replay package must be project-local .data')
    service = FastAPI(title='FutureCup Historical Read-only Replay', docs_url=None, redoc_url=None, openapi_url=None)
    package, summary, benchmark, charts = {}, {}, None, {}
    try:
        manifest = json.loads((directory/'manifest.json').read_text('utf-8'))
        for name in ('replay.json','public/summary.json','benchmark.json'):
            if sha((directory/name).read_bytes()) != manifest[name]:
                raise ValueError('Package integrity mismatch')
        package = json.loads((directory/'replay.json').read_text('utf-8'))
        summary = json.loads((directory/'public/summary.json').read_text('utf-8'))
        benchmark = json.loads((directory/'benchmark.json').read_text('utf-8'))
        for name in CHARTS:
            if name in manifest:
                content = (directory/name).read_bytes()
                if sha(content) != manifest[name]: raise ValueError('Chart integrity mismatch')
                charts[name] = content
    except (OSError, ValueError, KeyError):
        package = {}

    @service.middleware('http')
    async def read_only(request, call_next):
        if request.method not in {'GET','HEAD'}:
            return JSONResponse({'error':{'code':'replay_read_only','message':'Historical replay rejects all mutations.'}},405)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-FutureCup-Mode'] = 'historical-read-only'
        return response

    def unavailable():
        return JSONResponse({'error':{'code':'replay_unavailable','message':'Verified local evidence package unavailable.'}},503)

    def missing():
        return JSONResponse({'error':{'code':'replay_record_not_found','message':'Record not in this evidence package.'}},404)

    def summaries():
        return [{'id':r['run']['id'],'workflow':r['run']['definition_ref'],'state':r['run']['state'],
            'started_at':r['started_at'],'ended_at':r['ended_at'],'elapsed_ms':r['elapsed_ms'],
            'simulated':r['simulated'],'verified':r['verified'],'error_code':r['error_code'],
            'nodes':len(r['nodes']),'failed_nodes':sum(n['state']=='failed' for n in r['nodes'])}
            for r in package.get('reports',{}).values()]

    @service.get('/api/v1/health')
    def health():
        return {'status':'ok','scope':'historical_read_only','evidence_ready':bool(package)}

    @service.get('/api/v1/demo-info')
    def info():
        if not package: return unavailable()
        return {'mode':'historical_read_only','real_calls_enabled':False,'runtime_build_version':None,
                'runs':deepcopy(summary['runs']),'redactions':summary['redactions'],
                'benchmark':benchmark,'notice':'Sanitized historical views, no re-execution or provider access.'}

    @service.get('/api/v1/workflow-runs')
    def runs():
        return summaries() if package else unavailable()

    @service.get('/api/v1/workflow-runs/{run_id}')
    def report(run_id: str):
        r = package.get('reports',{}).get(run_id)
        if r is None: return missing() if package else unavailable()
        return {**deepcopy(r),'replay':{'read_only':True,'sanitized':True,'runtime_build_version':None}}

    @service.get('/api/v1/workflow-runs/{run_id}/nodes')
    def nodes(run_id: str):
        r=package.get('reports',{}).get(run_id)
        return deepcopy(r['nodes']) if r else missing()

    @service.get('/api/v1/self-loops/{node_id}/{kind}')
    def audit(node_id: str, kind: str):
        if kind not in {'attempts','verifications','feedback'}: return missing()
        a=package.get('audits',{}).get(node_id)
        return deepcopy(a[kind]) if a else missing()

    @service.get('/api/v1/scheduling/decisions')
    def decisions(run_id: str):
        if run_id not in package.get('reports',{}): return missing()
        return deepcopy([d for d in package['decisions'] if d['workflow_run_id']==run_id])

    @service.get('/api/v1/human-interventions')
    def interventions():
        return []  # History demonstration cannot approve/retry a run.

    @service.get('/api/v1/workflows')
    def definitions():
        return deepcopy(package.get('definitions',[]))

    @service.get('/api/v1/demo-structure/{run_id}')
    def structure(run_id: str):
        r=package.get('reports',{}).get(run_id)
        if r is None: return missing()
        return deepcopy(next(d for d in package['definitions'] if d['ref']==r['run']['definition_ref']))

    @service.get('/api/v1/demo-charts/{name}')
    def chart(name: str):
        if not package or name not in charts: return missing()
        return Response(charts[name],media_type='image/svg+xml',headers={
            'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; sandbox",
            'X-Content-Type-Options':'nosniff'})

    return service


app = create_replay_app()
