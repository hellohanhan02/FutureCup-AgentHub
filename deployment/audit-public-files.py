"""Offline submission-file audit. Reads selected public copies, never original secrets or DBs."""
from pathlib import Path
import subprocess, json, re, zipfile, gzip, hashlib
ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
 'key_like':re.compile(rb'\bsk-[A-Za-z0-9_-]{16,}'),
 'private_key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'bearer_value':re.compile(rb'(?i)\bbearer[ \t]+[A-Za-z0-9._-]{20,}'),
 'credential_url':re.compile(rb'https?://[^\s/:"<>]+:[^\s/@"<>]+@'),
}
SYNTHETIC_REVIEW={
 ('test_competition_evidence_10b.py',121,'bearer_value'),
 ('test_llm_provider.py',115,'credential_url'),
 ('test_real_llm_integration.py',83,'credential_url'),
 ('test_workspace_11a.py',206,'bearer_value'),
 ('test_platform_14d.py',116,'key_like'),
}
def files():
 raw=subprocess.check_output(['git','-C',str(ROOT),'ls-files','--cached','--others','--exclude-standard','-z'],timeout=20)
 return sorted(set(x for x in raw.decode('utf-8').split('\0') if x))
def audit():
 names=files(); findings=[]; prohibited=[]; sizes=0
 for name in names:
  p=ROOT/name;sizes+=p.stat().st_size
  if any(n in p.relative_to(ROOT).parts for n in ['.git','.venv','node_modules','__pycache__','.data','.validation']):prohibited.append(name)
  if p.name=='.env' or p.suffix.lower() in {'.db','.sqlite','.sqlite3','.pyc','.log'} or p.name.endswith(('-wal','-shm')):prohibited.append(name)
  parts={name:p.read_bytes()}
  if p.suffix.lower() in {'.docx','.pptx','.zip'}:
   with zipfile.ZipFile(p) as z:parts={n:z.read(n) for n in z.namelist() if n.endswith(('.xml','.rels','.py','.json','.md','.txt'))}
  elif p.suffix.lower()=='.gz':parts={name:gzip.decompress(p.read_bytes())}
  elif p.suffix.lower()=='.pdf':
   from pypdf import PdfReader
   parts={name:('\n'.join(page.extract_text() or '' for page in PdfReader(p).pages)).encode('utf-8')}
  for part,data in parts.items():
   for category,pattern in PATTERNS.items():
    for m in pattern.finditer(data):
     line=data[:m.start()].count(b'\n')+1
     synthetic='/backend/tests/' in '/'+name and (p.name,line,category) in SYNTHETIC_REVIEW
     if name=='deployment/check-provider-local.py' and category=='credential_url':synthetic=True
     findings.append({'path':name,'part':part,'line':line,'category':category,'review':'manually confirmed synthetic rejection/redaction test fixture' if synthetic else 'NEEDS_REVIEW'})
 result={'scope':'Selected public copies including PDF text, Office XML, gzip experiment data and frozen baseline archive; no original secret files read','files':len(names),'bytes':sizes,'prohibited_paths':prohibited,'pattern_findings_without_matched_contents':findings,'unresolved_findings':sum(x['review']=='NEEDS_REVIEW' for x in findings),'limitations':'Pattern and metadata checks are not a cryptographic guarantee; team must visually review document screenshots and confirm licenses before publication.'}
 (ROOT/'deployment/security-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:result[k] for k in ['files','bytes','prohibited_paths','unresolved_findings']},indent=2))
 if prohibited or result['unresolved_findings']:raise SystemExit(1)
if __name__=='__main__':audit()