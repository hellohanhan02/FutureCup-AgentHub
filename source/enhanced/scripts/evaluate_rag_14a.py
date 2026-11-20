"""Reproduce v1.0.0 vs v1.1 retrieval. Synthetic, local, no credentials or LLM.

Reference answers are used ONLY in score(), after retrieval. Both versions see
identical user query strings, document ordering and IDs, top-4 and environment.
The actual frozen adapter is loaded from this clone's Git object database.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types
from unittest.mock import patch
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend/src'))
import futurecup.adapters.knowledge as current
from futurecup.adapters.coverage import compact_context

BASE = 'bb8c418baaa7e9dbf210d4aae6895f2e95bf9e3b'


def frozen_module():
    source=(ROOT/'reproduction-baselines/knowledge-v1.0.0.py').read_bytes()
    mod=types.ModuleType('frozen_knowledge_14a')
    exec(compile(source,'<frozen v1.0.0 knowledge adapter>','exec'),mod.__dict__)
    return mod,sha256(source).hexdigest()


def populate(module, documents):
    counter=0
    def deterministic_id():
        nonlocal counter
        counter+=1
        return uuid5(UUID(int=14),str(counter))
    store=module.KnowledgeStore(':memory:')
    scopes={}
    with patch.object(module,'uuid4',deterministic_id):
        for doc in documents:
            if doc['domain'] not in scopes:scopes[doc['domain']]=store.create(doc['domain'])['id']
            store.import_document(scopes[doc['domain']],doc['name'],doc['text'].encode('utf-8'))
    return store,scopes


def score(case,record):
    text='\n'.join(h['text'] for h in record['hits'])
    rules=case['reference_rules']
    topics=case['reference_topics']
    # Same intentionally extractive Mock generator for both versions. These are
    # transport/source checks, not hallucination or real model quality estimates.
    claims=[h['text'] for h in record['hits']]
    citations=[{'chunk_id':h['id'],'quote':h['text']} for h in record['hits']]
    source_map={h['id']:h['text'] for h in record['hits']}
    valid=sum(c['chunk_id'] in source_map and c['quote'] in source_map[c['chunk_id']] for c in citations)
    unsupported=sum(not any(claim in h['text'] for h in record['hits']) for claim in claims)
    injection=compact_context(record) if 'coverage' in record else {'hits':record['hits'],'context_sha256':record['context_sha256']}
    encoded=json.dumps(injection,ensure_ascii=False,sort_keys=True)
    return {
        'key_rule_recall':sum(r in text for r in rules)/len(rules) if rules else None,
        'necessary_knowledge_coverage':sum(all(r in text for r in t['rules']) for t in topics)/len(topics) if topics else None,
        'extractive_mock_citation_validity':valid/len(citations) if citations else None,
        'extractive_mock_unsupported_ratio':unsupported/len(claims) if claims else None,
        'real_model_unsupported_ratio':None,
        'hit_count':len(record['hits']),
        'injected_chars':len(encoded),
        'injected_token_budget_upper_proxy':len(encoded.encode('utf-8')),
        'retrieval_calls':record.get('retrieval_calls',1),
        'retrieved_reference_rules':[r for r in rules if r in text],
        'hit_ids':[h['id'] for h in record['hits']],
    }


def run(dataset,repeats=20):
    frozen,adapter_hash=frozen_module()
    stores={}
    for version,module in [('v1.0.0',frozen),('v1.1',current)]:stores[version]=populate(module,dataset['documents'])
    rows=[]
    try:
        for repeat in range(repeats):
            # Alternate order to reduce cache-order bias. All measurements kept.
            versions=['v1.0.0','v1.1'] if repeat%2==0 else ['v1.1','v1.0.0']
            for case in dataset['cases']:
                for version in versions:
                    store,scope=stores[version]
                    started=time.perf_counter()
                    record=asyncio.run(store.retrieve([scope[s] for s in case['scope']],case['query']))
                    ms=(time.perf_counter()-started)*1000
                    rows.append({'case':case['id'],'version':version,'repeat':repeat,
                        'query':case['query'],'latency_ms':ms,**score(case,record),
                        'coverage':record.get('coverage',[]),'queries':record.get('queries',[])})
    finally:
        for store,_ in stores.values():store.connection.close()
    metrics=['key_rule_recall','necessary_knowledge_coverage','extractive_mock_citation_validity',
             'extractive_mock_unsupported_ratio','latency_ms','injected_token_budget_upper_proxy','retrieval_calls']
    summary={}
    for version in stores:
        summary[version]={}
        for metric in metrics:
            values=[r[metric] for r in rows if r['version']==version and r[metric] is not None]
            summary[version][metric]={'mean':statistics.mean(values),'stdev':statistics.stdev(values) if len(values)>1 else 0,
                                     'min':min(values),'max':max(values)} if values else None
    # Outcome determinism excludes clocks. Identical repeated measurements are
    # NOT independent task samples and must not imply statistical significance.
    deterministic=all(len({json.dumps([r['hit_ids'],r['key_rule_recall'],r['injected_chars']])
                    for r in rows if r['case']==case['id'] and r['version']==version})==1
                    for version in stores for case in dataset['cases'])
    return {'kind':'SYNTHETIC_OFFLINE_RETRIEVAL','base_commit':BASE,'baseline_adapter_sha256':adapter_hash,
        'repeats':repeats,'case_count':len(dataset['cases']),'deterministic_outcomes':deterministic,
        'statistical_significance':'NOT_TESTED: small curated corpus; repeated timings are not independent tasks',
        'token_proxy':'UTF-8 bytes of injected evidence JSON; conservative proxy, not API token usage',
        'model_quality':'No LLM generated answers. Extractive Mock source checks are true by construction; no real hallucination-rate claim.',
        'summary':summary,'raw':rows}


def save(result,out):
    out.mkdir(parents=True,exist_ok=True)
    (out/'raw.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['case','version','repeat','latency_ms','key_rule_recall','necessary_knowledge_coverage',
            'extractive_mock_citation_validity','extractive_mock_unsupported_ratio','real_model_unsupported_ratio',
            'hit_count','injected_chars','injected_token_budget_upper_proxy','retrieval_calls','hit_ids']
    with (out/'raw.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fields);writer.writeheader()
        for r in result['raw']:writer.writerow({k:json.dumps(r[k]) if isinstance(r[k],list) else r[k] for k in fields})
    lines=['# Offline retrieval comparison (synthetic only)','',
           'Same frozen v1.0.0 adapter and current adapter, same indexed documents/query/IDs/top-4. No model calls.',
           '','| Metric (case-weighted) | v1.0.0 | v1.1 |','|---|---:|---:|']
    for metric in result['summary']['v1.0.0']:
        a=result['summary']['v1.0.0'][metric]['mean'];b=result['summary']['v1.1'][metric]['mean']
        lines.append(f'| {metric} | {a:.6f} | {b:.6f} |')
    lines+=['','| Case | v1.0.0 rule recall | v1.1 rule recall |','|---|---:|---:|']
    for case in dict.fromkeys(r['case'] for r in result['raw']):
        values=[]
        for version in ['v1.0.0','v1.1']:
            value=next(r['key_rule_recall'] for r in result['raw'] if r['case']==case and r['version']==version)
            values.append('N/A (no reference rules)' if value is None else f'{value:.4f}')
        lines.append(f'| {case} | {values[0]} | {values[1]} |')
    lines+=['',result['statistical_significance'], '',result['model_quality'],'',result['token_proxy'],
            '',f"Deterministic outcomes: {result['deterministic_outcomes']}. All {len(result['raw'])} measurements retained."]
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # A code-native static SVG, no external plotting dependency.
    a=result['summary']['v1.0.0']['key_rule_recall']['mean']
    b=result['summary']['v1.1']['key_rule_recall']['mean']
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="760" height="260" viewBox="0 0 760 260"><rect width="760" height="260" fill="white"/><g font-family="sans-serif" fill="#173e36"><text x="30" y="35" font-size="20">Synthetic key-rule retrieval recall (no LLM)</text><text x="30" y="95">v1.0.0</text><text x="30" y="165">v1.1</text><rect x="120" y="65" width="{a*480}" height="40" fill="#9aafa8"/><rect x="120" y="135" width="{b*480}" height="40" fill="#26876d"/><text x="620" y="95">{a:.1%}</text><text x="620" y="165">{b:.1%}</text><text x="30" y="225">Top-4 cap; curated corpus; no statistical significance claim.</text></g></svg>'
    (out/'recall.svg').write_text(svg,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=20)
    parser.add_argument('--output',type=Path,default=ROOT/'results/rag-14a')
    args=parser.parse_args()
    if not 1<=args.repeats<=100:parser.error('repeats must be 1..100')
    out=args.output.resolve()
    if not out.is_relative_to(ROOT):parser.error('output must stay inside this independent clone')
    dataset_path=ROOT/'benchmarks/rag-14a/dataset.json'
    result=run(json.loads(dataset_path.read_text('utf-8')),args.repeats)
    result['dataset_sha256']=sha256(dataset_path.read_bytes()).hexdigest()
    result['environment']={'python':sys.version.split()[0],'sqlite':__import__('sqlite3').sqlite_version,
                           'platform':sys.platform}
    save(result,out)
    print(json.dumps({'summary':result['summary'],'deterministic':result['deterministic_outcomes'],
                      'measurements':len(result['raw']),'output':str(out)},indent=2))

if __name__=='__main__':main()
