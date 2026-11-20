"""Bounded delivery-local installation; no original project or credential configuration."""
from pathlib import Path
import argparse,json,os,subprocess,time,sys
ROOT=Path(__file__).resolve().parents[1]
OS_NAMES=('SystemRoot','WINDIR','COMSPEC','PATH','PATHEXT','APPDATA','LOCALAPPDATA')
def clean_env():
    env={k:os.environ.get(k,'') for k in OS_NAMES}
    local=ROOT/'.validation';(local/'tmp').mkdir(parents=True,exist_ok=True);(local/'home').mkdir(exist_ok=True)
    env.update(TEMP=str(local/'tmp'),TMP=str(local/'tmp'),HOME=str(local/'home'),USERPROFILE=str(local/'home'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PIP_CONFIG_FILE='NUL',PIP_CACHE_DIR=str(local/'pip-cache'),NPM_CONFIG_USERCONFIG=str(local/'npmrc'),NPM_CONFIG_GLOBALCONFIG=str(local/'npmrc-global'),NPM_CONFIG_CACHE=str(local/'npm-cache'),NPM_CONFIG_REGISTRY='https://registry.npmjs.org/',NPM_CONFIG_AUDIT='false',NPM_CONFIG_FUND='false')
    (local/'npmrc').write_text('registry=https://registry.npmjs.org/\naudit=false\nfund=false\n');(local/'npmrc-global').write_text('')
    return env

def main():
    if sys.version_info[:2]!=(3,11):raise SystemExit('Python 3.11 is required; no dependency installation ran')
    p=argparse.ArgumentParser();p.add_argument('--check-only',action='store_true');p.add_argument('--enhanced',action='store_true');p.add_argument('--timeout',type=int,default=300);args=p.parse_args()
    if not 30<=args.timeout<=600:raise SystemExit('Installation timeout must be 30..600 seconds')
    env=clean_env();folder=ROOT/'.validation';results=[]
    versions={}
    for name,cmd in [('node',['node','--version']),('npm',['npm.cmd','--version'])]:
        result=subprocess.run(cmd,capture_output=True,env=env,timeout=10,check=True);versions[name]=result.stdout.decode().strip()
    pieces=versions['node'].lstrip('v').split('.');major,minor=int(pieces[0]),int(pieces[1])
    if not ((major==22 and minor>=12) or major==24 or major>=26):raise SystemExit('Node engines requirement not met; use the package.json supported release')
    (folder/'environment.json').write_text(json.dumps({'python':sys.version.split()[0],**versions,'supported':True},indent=2))
    if args.check_only:print(json.dumps(versions));return
    def step(name,command,cwd,limit):
        print('START',name,flush=True);start=time.monotonic()
        with (folder/(name+'.log')).open('wb') as log:
            child=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:code=child.wait(timeout=limit);expired=False
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=10)
                child.wait(timeout=10);code=None;expired=True
        row={'step':name,'exit_code':code,'timeout':expired,'seconds':round(time.monotonic()-start,2),'log':'.validation/'+name+'.log'};results.append(row)
        (folder/'install.json').write_text(json.dumps({'scope':'FRESH_INSTALL_NO_ORIGINAL_ENVIRONMENT','python':sys.version.split()[0],'steps':results},indent=2));print(json.dumps(row),flush=True)
        return code==0
    py=ROOT/'.venv/Scripts/python.exe'
    if py.exists():raise SystemExit('STOP: .venv already exists. Inspect the previous attempt; do not silently repeat installation.')
    ready=step('venv',[sys.executable,'-m','venv',str(ROOT/'.venv')],ROOT,60)
    if ready:
        step('pip-install',[str(py),'-m','pip','install','-e',str(ROOT/'source/stable/backend')+'[dev]','-c',str(ROOT/'deployment/constraints-tested.txt'),'--index-url','https://pypi.org/simple','--disable-pip-version-check','--no-input','--retries','0','--timeout','20'],ROOT,args.timeout)
    for version in (['stable','enhanced'] if args.enhanced else ['stable']):
        step('npm-ci-'+version,['npm.cmd','ci','--no-audit','--no-fund'],ROOT/'source'/version/'frontend',args.timeout)
    success=ready and all(x['exit_code']==0 for x in results)
    final=json.loads((folder/'install.json').read_text());final.update(success=success,finished=True);(folder/'install.json').write_text(json.dumps(final,indent=2))
    raise SystemExit(0 if success else 1)
if __name__=='__main__':main()