"""Local lexical retrieval, immutable source snapshots, no embeddings or network."""
import base64
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree
from futurecup.domain.errors import RuntimeFault

def digest(value):return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()
def terms(text):
    words=re.findall('[a-z0-9_]+',text.lower())
    for run in re.findall('[\u4e00-\u9fff]+',text):
        words.extend(run[i:i+2] for i in range(len(run)-1))
        if len(run)==1:words.append(run)
    return sorted(set(words))[:512]

class KnowledgeStore:
    def snapshot_current(self, records: tuple[dict,...]) -> bool:
        try:
            for record in records:
                for hit in record['hits']:
                    kb=self.require(hit['kb_id'])
                    doc=next(d for d in self.documents(hit['kb_id']) if d['id']==hit['doc_id'])
                    if kb['version']!=hit['knowledge_version'] or doc['version']!=hit['document_version'] or doc['sha256']!=hit['document_sha256']: return False
            return True
        except (RuntimeFault,KeyError,StopIteration): return False
    def bind_sources(self,record:dict,scope:str)->dict:
        from futurecup.adapters.source_binding import bind
        return bind(record,scope)
    def context(self,record:dict)->dict:
        from futurecup.adapters.coverage import compact_context
        return compact_context(record)
    def validate_topics(self,topics):
        from futurecup.adapters.coverage import validate_topics
        return validate_topics(topics)
    def __init__(self,path):self.path=path
    def db(self):
        if self.path!=':memory:':Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        # A persistent single connection also supports isolated in-memory tests.
        if not hasattr(self,'connection'):
            self.connection=sqlite3.connect(self.path,check_same_thread=False)
            self.connection.row_factory=sqlite3.Row
            self.connection.executescript('''CREATE TABLE IF NOT EXISTS kb(id TEXT PRIMARY KEY,name TEXT,description TEXT,version INTEGER,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,kb_id TEXT,name TEXT,version INTEGER,sha256 TEXT,status TEXT,error TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,doc_id TEXT,kb_id TEXT,locator TEXT,text TEXT,sha256 TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(chunk_id UNINDEXED,kb_id UNINDEXED,tokens);''')
        return self.connection
    def list(self):return [dict(r) for r in self.db().execute("SELECT k.*,(SELECT count(*) FROM documents d WHERE d.kb_id=k.id AND d.deleted=0 AND d.status='indexed') document_count FROM kb k WHERE k.deleted=0")]
    def require(self,id):
        row=self.db().execute('SELECT * FROM kb WHERE id=? AND deleted=0',(id,)).fetchone()
        if row is None:raise RuntimeFault('knowledge_not_found','知识库不存在或已删除',404)
        return dict(row)
    def create(self,name,description=''):
        id=uuid4().hex
        with self.db() as db:db.execute('INSERT INTO kb(id,name,description,version) VALUES(?,?,?,1)',(id,name,description))
        return self.require(id)
    def edit(self,id,name,description):
        self.require(id)
        with self.db() as db:db.execute('UPDATE kb SET name=?,description=?,version=version+1 WHERE id=?',(name,description,id))
        return self.require(id)
    def delete(self,id):
        self.require(id)
        for d in self.documents(id):self.delete_document(id,d['id'])
        with self.db() as db:db.execute('UPDATE kb SET deleted=1,version=version+1 WHERE id=?',(id,))
    def documents(self,id):
        self.require(id)
        return [dict(r) for r in self.db().execute('SELECT * FROM documents WHERE kb_id=? AND deleted=0',(id,))]
    def delete_document(self,kb,id):
        self.require(kb)
        with self.db() as db:
            db.execute('DELETE FROM knowledge_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=? AND kb_id=?)',(id,kb))
            db.execute('DELETE FROM chunks WHERE doc_id=? AND kb_id=?',(id,kb))
            db.execute('UPDATE documents SET deleted=1 WHERE id=? AND kb_id=?',(id,kb))
            db.execute('UPDATE kb SET version=version+1 WHERE id=?',(kb,))
    def chunks(self,kb,doc):
        self.require(kb)
        return [dict(r) for r in self.db().execute('SELECT * FROM chunks WHERE kb_id=? AND doc_id=?',(kb,doc))]
    def import_document(self,kb,name,raw):
        self.require(kb)
        if len(self.documents(kb))>=30:raise RuntimeFault('knowledge_limit','每库最多30个文档',422)
        if not name or len(name)>128 or '/' in name or '\\' in name or ':' in name or '..' in name or len(raw)>2*1024*1024:
            raise RuntimeFault('knowledge_file','文件名或大小不合法（最多2MB）',422)
        extension=Path(name).suffix.lower()
        if extension not in ('.txt','.md','.pdf','.docx'):raise RuntimeFault('knowledge_type','仅支持 TXT/MD/PDF/DOCX',422)
        id=uuid4().hex;db=self.db()
        with db:db.execute('INSERT INTO documents(id,kb_id,name,version,sha256,status) VALUES(?,?,?,1,?,?)',(id,kb,name,digest(raw),'parsing'))
        try:
            if extension in ('.txt','.md'):
                if b'\x00' in raw:raise ValueError()
                parts=[('paragraph '+str(i+1),p) for i,p in enumerate(raw.decode('utf-8-sig').split('\n\n')) if p.strip()]
            elif extension=='.pdf':
                if not raw.startswith(b'%PDF-'):raise ValueError()
                from pypdf import PdfReader
                pdf=PdfReader(io.BytesIO(raw))
                if pdf.is_encrypted or len(pdf.pages)>100:raise ValueError()
                parts=[('page '+str(i+1),p.extract_text() or '') for i,p in enumerate(pdf.pages)]
            else:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    info=z.infolist()
                    if len(info)>500 or sum(i.file_size for i in info)>10*1024*1024 or any(i.flag_bits&1 or i.file_size>2*1024*1024 or '..' in i.filename or i.filename.startswith('/') or '\\' in i.filename for i in info):raise ValueError()
                    xml=z.read('word/document.xml')
                    if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise ValueError()
                    tree=ElementTree.fromstring(xml);ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                    parts=[('paragraph '+str(i+1),''.join(p.itertext())) for i,p in enumerate(tree.findall('.//w:p',ns))]
            if sum(len(t) for _,t in parts)>200000:raise ValueError()
            chunks=[]
            for locator,text in parts:
                for start in range(0,len(text),600):
                    chunk=text[start:start+700].strip()
                    if chunk:chunks.append((uuid4().hex,id,kb,locator+':'+str(start),chunk,digest(chunk)))
            if not chunks:raise RuntimeFault('knowledge_no_text','未提取到文本；扫描PDF需要OCR，本阶段不支持',422)
            if len(chunks)>400:raise ValueError()
            with db:
                db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?)',chunks)
                db.executemany('INSERT INTO knowledge_fts VALUES(?,?,?)',[(c[0],kb,' '.join(terms(c[4]))) for c in chunks])
                db.execute('UPDATE documents SET status=? WHERE id=?',('indexed',id))
                db.execute('UPDATE kb SET version=version+1 WHERE id=?',(kb,))
        except Exception as e:
            with db:db.execute('UPDATE documents SET status=?,error=? WHERE id=?',('failed',e.code if isinstance(e,RuntimeFault) else 'knowledge_parse_failed',id))
            if isinstance(e,RuntimeFault):raise
            raise RuntimeFault('knowledge_parse_failed','文档解析失败或超出安全限制',422) from None
        return dict(db.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone())
    def search(self,scope,query,limit=4):
        if not 1<=len(scope)<=5 or not 1<=limit<=8 or len(query)>8000:raise RuntimeFault('knowledge_query','检索范围或数量超限',422)
        for id in scope:self.require(id)
        tokens=terms(query)
        if not tokens:return []
        match=' OR '.join('"'+t+'"' for t in tokens)
        placeholders=','.join('?' for _ in scope)
        rows=self.db().execute(f'''SELECT c.*,d.name document_name,d.version document_version,d.sha256 document_sha256,k.version knowledge_version,bm25(knowledge_fts) score
FROM knowledge_fts JOIN chunks c ON c.id=knowledge_fts.chunk_id JOIN documents d ON c.doc_id=d.id JOIN kb k ON c.kb_id=k.id
WHERE knowledge_fts MATCH ? AND c.kb_id IN ({placeholders}) AND d.deleted=0 AND k.deleted=0 ORDER BY score,c.id LIMIT ?''',(match,*scope,limit)).fetchall()
        return [dict(r) for r in rows]
    async def retrieve(self,scope,query):
        topics=None; contract=None; rule_scope=None; stage=None
        try:
            value=json.loads(query);value=value.get('original_input',value)
            if isinstance(value,dict) and isinstance(value.get('request'),dict):
                topics=value['request'].get('retrieval_topics')
                contract=value.get('rule_contract'); stage=value.get('knowledge_stage')
                rule_scope=value.get('rule_scope')
                if contract in ('atomic-body-v1','staged-rules-v1','staged-rules-v2') and isinstance(topics,list) and isinstance(rule_scope,list):
                    topics=[{**t,'required':t.get('required',False) and t['id'] in rule_scope} for t in topics]
                query='\n'.join([*value['request'].get('messages',[]),value.get('role',''),value.get('responsibilities',''),value.get('deliverable','')])
        except (ValueError,TypeError,AttributeError):pass
        from futurecup.adapters.coverage import retrieve_topics
        result=retrieve_topics(self.search,terms,scope,query[:8000],topics)
        return {**result,'knowledge_stage':stage,'rule_contract':contract,'rule_scope':rule_scope,'query':query[:8000],'knowledge_base_ids':list(scope),'query_version':'user-role-v2','context_sha256':digest(json.dumps(result['hits'],sort_keys=True,ensure_ascii=False))}

def check_citations(value,records):
    hits={h['id']:h for r in records for h in r['hits']}
    citations=value.get('citations') if isinstance(value,dict) else None
    if not hits or not isinstance(citations,list) or not citations:return False
    return all(isinstance(c,dict) and set(c)=={'chunk_id','quote'} and c['chunk_id'] in hits and isinstance(c['quote'],str) and bool(c['quote']) and c['quote'] in hits[c['chunk_id']]['text'] for c in citations)
