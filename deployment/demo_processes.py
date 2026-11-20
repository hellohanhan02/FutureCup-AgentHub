"""Delivery-local service ownership, bounded readiness and loopback-only launch."""
from pathlib import Path
import argparse,ctypes,json,os,socket,subprocess,sys,time,urllib.request,shutil
ROOT=Path(__file__).resolve().parents[1]
PORTS={'stable':(19320,15320),'enhanced':(19321,15321),'replay':(19322,15322),'real':(19323,15323)}
def env_base():
    env={k:os.environ.get(k,'') for k in ('SystemRoot','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','APPDATA','LOCALAPPDATA')}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',FUTURECUP_ENABLE_REAL_LLM='false',FUTURECUP_LLM_PROVIDERS_JSON='[]')
    return env

def created(pid):
    # Only process creation metadata. Never inspect process memory/environment.
    from ctypes import wintypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
    h=k.OpenProcess(0x1000,False,pid)
    if not h:return None
    a,b,c,d=(wintypes.FILETIME() for _ in range(4));k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)];k.CloseHandle.argtypes=[wintypes.HANDLE]
    try:
        if not k.GetProcessTimes(h,ctypes.byref(a),ctypes.byref(b),ctypes.byref(c),ctypes.byref(d)):return None
        return a.dwLowDateTime+(a.dwHighDateTime<<32)
    finally:k.CloseHandle(h)

def request(port,path,body=None):
    import urllib.error
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=None if body is None else json.dumps(body).encode(),headers={} if body is None else {'Content-Type':'application/json'})
    with opener.open(req,timeout=3) as response:
        data=response.read();return json.loads(data) if response.headers.get_content_type()=='application/json' else data.decode('utf-8')

def stop(mode):
    path=ROOT/'.runtime'/('services-'+mode+'.json')
    if not path.exists():print('No owned session for',mode);return
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('root')!=str(ROOT):raise ValueError('Session belongs to another directory')
    for p in record['processes']:
        if created(p['pid'])==p['created'] and p['created'] is not None:
            subprocess.run(['taskkill','/PID',str(p['pid']),'/T','/F'],capture_output=True,timeout=15,check=False)
    record['stopped']=True;path.write_text(json.dumps(record,indent=2));print('Stopped only matching owned processes for',mode)

def start(mode,bp=None,fp=None,real_env=None):
    bp=bp or PORTS[mode][0];fp=fp or PORTS[mode][1]
    if bp==fp or not all(1024<=x<=65535 for x in [bp,fp]):raise ValueError('Invalid distinct ports')
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True);session=runtime/('services-'+mode+'.json')
    if session.exists():
        old=json.loads(session.read_text(encoding='utf-8'))
        if any(created(x['pid'])==x['created'] and x['created'] is not None for x in old['processes']):raise ValueError('Owned session already running; inspect or Stop-Demo first')
    for port in [bp,fp]:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:raise ValueError('Port in use; no existing service was stopped') from None
    project=ROOT/'source'/('enhanced' if mode=='enhanced' else 'stable');py=ROOT/'.venv/Scripts/python.exe';vite=project/'frontend/node_modules/vite/bin/vite.js'
    if not py.is_file() or not vite.is_file():raise ValueError('Run Install.ps1 first, including enhanced dependencies if selected')
    data=runtime/mode;data.mkdir(exist_ok=True)
    env=env_base();env.update(PYTHONPATH=str(project/'backend/src'),FUTURECUP_DATABASE_URL='sqlite:///'+str(data/'workflow.sqlite3'),FUTURECUP_KNOWLEDGE_DATABASE=str(data/'knowledge.sqlite3'))
    factory='futurecup.api.app:create_app'
    if mode=='enhanced':factory='futurecup.api.platform_demo:create_platform_app'
    if mode=='real':
        if not real_env:raise ValueError('Real launch requires newly supplied configuration')
        env.update(real_env)
    if mode=='replay':
        factory='futurecup.api.replay:create_replay_app';source=ROOT/'evidence/history-replay';target=project/'.data/competition-evidence-10b'
        for item in source.rglob('*'):
            if not item.is_file():continue
            dest=target/item.relative_to(source)
            if dest.exists() and dest.read_bytes()!=item.read_bytes():raise ValueError('Replay package differs; refusing overwrite')
            if not dest.exists():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(item,dest)
    commands=[('backend',[str(py),'-B','-m','uvicorn',factory,'--factory','--host','127.0.0.1','--port',str(bp),'--workers','1','--no-access-log'],project,env),('frontend',['node',str(vite),'--host','127.0.0.1','--port',str(fp),'--strictPort'],project/'frontend',{**env_base(),'FUTURECUP_API_TARGET':'http://127.0.0.1:'+str(bp)})]
    record={'root':str(ROOT),'mode':mode,'backend_port':bp,'frontend_port':fp,'processes':[],'stopped':False}
    try:
        for label,cmd,cwd,child_env in commands:
            with (runtime/(mode+'-'+label+'.log')).open('ab') as log:proc=subprocess.Popen(cmd,cwd=cwd,env=child_env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            record['processes'].append({'component':label,'pid':proc.pid,'created':created(proc.pid)})
            session.write_text(json.dumps(record,indent=2))
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:
                request(bp,'/api/v1/health');request(fp,'/api/v1/health');break
            except Exception:time.sleep(.25)
        else:raise TimeoutError('Readiness deadline exceeded; inspect .runtime logs')
        if mode=='stable':
            from seed_demo import seed
            seed(bp)
        page='demo' if mode=='replay' else ('runs' if mode=='enhanced' else 'workspace')
        record['url']=f'http://127.0.0.1:{fp}/#/{page}';record['ready']=True;session.write_text(json.dumps(record,indent=2));print(record['url'],flush=True)
        return record
    except Exception:
        stop(mode);raise

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop']);p.add_argument('--mode',choices=list(PORTS),default='stable');p.add_argument('--backend-port',type=int);p.add_argument('--frontend-port',type=int);args=p.parse_args()
    try:
        if args.action=='stop':stop(args.mode)
        elif args.mode=='real':raise ValueError('Use Start-Real.ps1 for real mode')
        else:start(args.mode,args.backend_port,args.frontend_port)
    except Exception as e:print(type(e).__name__+': '+str(e));raise SystemExit(1)
if __name__=='__main__':main()