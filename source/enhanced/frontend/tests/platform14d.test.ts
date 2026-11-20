// Explicit simulated HTTP / DOM fixtures. Browser acceptance is recorded separately.
import { createApp, h, nextTick, type App, type Component } from 'vue'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import { afterEach, expect, it, vi } from 'vitest'
import RunDetail from '../src/pages/RunDetail.vue'
import LedgerPanel from '../src/components/LedgerPanel.vue'
import KnowledgeCoverage from '../src/components/KnowledgeCoverage.vue'
import SchedulingExplanation from '../src/components/SchedulingExplanation.vue'
import { layoutGraph, validateExport } from '../src/platform'

let app: App | undefined
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status})
const settle=async()=>{await nextTick();await new Promise(r=>setTimeout(r,30));await nextTick()}
afterEach(()=>{app?.unmount();app=undefined;document.body.innerHTML='';vi.unstubAllGlobals();vi.restoreAllMocks()})
async function mount(component:Component,props:Record<string,unknown>={}) {
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/runs/:id',component,props:()=>props}]})
  await router.push('/runs/A');await router.isReady()
  app=createApp({render:()=>h(NConfigProvider,null,{default:()=>h(NMessageProvider,null,{default:()=>h(NDialogProvider,null,{default:()=>h(RouterView)})})})})
  app.use(router);app.mount(document.body);await settle();return router
}
function record(id:string,label=id+'-flow',simulated=true) {
 return {run:{id,state:'failed',definition_ref:{id:label,version:'1'},revision:7},simulated,verified:false,input:'[MOCK]',output:null,
  started_at:null,ended_at:null,elapsed_ms:1,error_code:'verification_failed',resource_usage:{model_calls:1},events:[],
  nodes:[{id:id+'-node',node_id:'work',state:'failed',input:{},output:null,agent_ref:null,execution_id:null,
    attempt_ids:[],verification_ids:[],feedback_ids:[],child_run_ids:[],model_calls:[],input_tokens:0,output_tokens:0,
    started_at:null,ended_at:null,elapsed_ms:1,waiting_ms:0,verified:false,simulated,error_code:'verification_failed'}]}
}
function evidence(id:string,simulated=true) {
 return {schema_version:'platform-14d-v1',run_id:id,revision:7,report:record(id,undefined,simulated),graph:{nodes:[{id:'work',kind:'task'}],edges:[]},
 node_configs:{},audits:{},decisions:[],ledger:{available:false},links:{source_run_id:null,recovery_run_id:null},
 recovery:{requestable:simulated,reasons:simulated?[]:['recovery_real_budget_unprovable'],expected_revision:7},redactions:{}}
}
function mockApi(overrides:(path:string,init?:RequestInit)=>Promise<Response>|Response|undefined=()=>undefined) {
 const fetcher=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const path=String(input);const custom=overrides(path,init);if(custom)return custom
  if(path.includes('/observability')){const id=path.split('/workflow-runs/')[1].split('/')[0];return response(evidence(id))}
  if(path.includes('/scheduling/')||path.endsWith('/human-interventions'))return response([])
  if(path.match(/\/workflow-runs\/[^/]+$/)){const id=path.split('/').pop()!;return response(record(id))}
  return response([])
 });vi.stubGlobal('fetch',fetcher);return fetcher
}
const button=(text:string)=>[...document.querySelectorAll('button')].find(b=>b.textContent?.includes(text))!

it('lays out actual dependencies and rejects stale report identity/version',()=>{
 const positioned=layoutGraph({nodes:[{id:'b',kind:'task'},{id:'a',kind:'task'}],edges:[{source:'a',target:'b'}]})
 expect(positioned.find(p=>p.id==='b')!.x).toBeGreaterThan(positioned.find(p=>p.id==='a')!.x)
 const valid={run_id:'A',revision:7,evidence:evidence('A'),sha256:'fixture'}
 expect(validateExport(valid as any,'A',7)).toBe(valid)
 expect(()=>validateExport(valid as any,'B',7)).toThrow()
 expect(()=>validateExport(valid as any,'A',8)).toThrow()
})

it('renders actual UNKNOWN as occupied quota and no supplier invoice',async()=>{
 await mount(LedgerPanel,{value:{available:true,unit:'resource_units',max_calls:6,confirmed_calls:2,reserved_calls:1,unknown_calls:1,
  max_units:100,confirmed_units:20,reserved_units:10,unknown_units:30,remaining_units:40,remaining_calls:2,
  tools:[{id:'tool',node_id:'work',tool_name:'effect',state:'UNKNOWN',side_effect_class:'unknown',idempotency_summary:'hash-only',replay_count:0}],findings:[{code:'tool_outcome_unknown',action:'manual_review'}]}})
 expect(document.body.textContent).toContain('UNKNOWN 未确认')
 expect(document.body.textContent).toContain('不代表免费')
 expect(document.body.textContent).toContain('tool_outcome_unknown')
 expect(document.body.textContent).not.toContain('最终费用：')
})

it('shows only recorded scheduling weights/capacity and labels old missing data',async()=>{
 await mount(SchedulingExplanation,{decisions:[{id:'d',strategy:'adaptive_caas',selected:{id:'worker'},outcome:'selected',requirements:{required_skills:['analysis']},
 candidates:[{agent_id:'worker',eligible:true,reasons:[],components:{match:.8,reliability:.6},score:.7,profile:{capacity:{active:2,capacity:4}},adaptation:{weights:{match:.3},effective_samples:2,cooldown_remaining_seconds:0,reason:'highest_adaptive_score_then_id'}},
 {agent_id:'old',eligible:false,reasons:['agent_unavailable'],components:{},score:null,profile:null}]}]})
 expect(document.body.textContent).toContain('0.300')
 expect(document.body.textContent).toContain('2 / 4')
 expect(document.body.textContent).toContain('agent_unavailable')
 expect(document.body.textContent).toContain('未记录')
})

it('separates retrieval, source citations and omitted/conflicting rules; escapes source text',async()=>{
 const rule='<script>untrusted</script> Order lock must expire after 15 minutes'
 await mount(KnowledgeCoverage,{records:[{retrieval_calls:2,context_chars:100,coverage:[{id:'t',query:'order lock',status:'PARTIAL',search_state:'QUERIED',required:true,requirement_origin:'caller'}],
 queries:[{phase:'initial',query:'order lock',hit_ids:[]},{phase:'supplement',query:'order lock',hit_ids:['chunk']}],hits:[{id:'chunk',document_name:'Synthetic specification',locator:'paragraph 1',text:rule}]}],
 artifact:{citations:[{chunk_id:'chunk',quote:rule}]},evidence:[{details:{coverage:[{status:'SOURCE_NOT_REFLECTED'},{status:'POSSIBLE_CONFLICT'}]}}]})
 expect(document.body.textContent).toContain('初次')
 expect(document.body.textContent).toContain('补检')
 expect(document.body.textContent).toContain('来源存在但成果遗漏规则')
 expect(document.body.textContent).toContain('部分数值可能与来源冲突')
 expect(document.querySelector('script')).toBeNull()
 button('chunk').click();await settle()
 expect((document.getElementById('knowledge-chunk') as HTMLDetailsElement).open).toBe(true)
})

it('does not let an old run response overwrite the newly selected run',async()=>{
 let resolveA!:(r:Response)=>void
 mockApi(path=>path==='/api/v1/workflow-runs/A'?new Promise(r=>{resolveA=r}):undefined)
 const router=await mount(RunDetail)
 await router.push('/runs/B');await settle()
 expect(document.body.textContent).toContain('B-flow')
 resolveA(response(record('A')));await settle()
 expect(document.body.textContent).toContain('B-flow')
 expect(document.body.textContent).not.toContain('A-flow')
})

it('navigation generations reject an old A response after A-B-A',async()=>{
 let resolveOld!:(r:Response)=>void,count=0
 mockApi(path=>{if(path==='/api/v1/workflow-runs/A')return ++count===1?new Promise(r=>{resolveOld=r}):response(record('A','fresh-A'))})
 const router=await mount(RunDetail)
 await router.push('/runs/B');await settle();await router.push('/runs/A');await settle()
 resolveOld(response(record('A','stale-A')));await settle()
 expect(document.body.textContent).toContain('fresh-A')
 expect(document.body.textContent).not.toContain('stale-A')
})

it('export is single-flight and rejects a late response after switching runs',async()=>{
 let finishExport!:(r:Response)=>void
 const fetcher=mockApi(path=>path.includes('/report-export')?new Promise(r=>{finishExport=r}):undefined)
 const make=vi.fn(()=> 'blob:fixture');vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:make,revokeObjectURL:vi.fn()}))
 const router=await mount(RunDetail)
 button('导出脱敏运行报告').click();button('导出脱敏运行报告').click();await settle()
 expect(fetcher.mock.calls.filter(c=>String(c[0]).includes('/report-export'))).toHaveLength(1)
 await router.push('/runs/B');await settle()
 finishExport(response({run_id:'A',revision:7,evidence:evidence('A'),sha256:'fixture'}));await settle()
 expect(make).not.toHaveBeenCalled()
})

it('real-run recovery remains disabled even with an available observation API',async()=>{
 const fetcher=mockApi(path=>path.endsWith('/observability')?response(evidence('A',false)):path==='/api/v1/workflow-runs/A'?response(record('A',undefined,false)):undefined)
 await mount(RunDetail)
 expect(button('申请 Mock 局部恢复').disabled).toBe(true)
 expect(document.body.textContent).toContain('跨运行预算尚不可证明')
 button('申请 Mock 局部恢复').click();await settle()
 expect(fetcher.mock.calls.some(c=>String(c[0]).endsWith('/recover'))).toBe(false)
})

it('an old recovery confirmation cannot act on a different run',async()=>{
 const fetcher=mockApi();const router=await mount(RunDetail)
 button('申请 Mock 局部恢复').click();await settle()
 await router.push('/runs/B');await settle()
 button('确认申请恢复').click();await settle()
 expect(fetcher.mock.calls.some(c=>String(c[0]).endsWith('/recover'))).toBe(false)
})


it('distinguishes uncertain body semantics from legacy missing citations',async()=>{
 await mount(KnowledgeCoverage,{records:[],artifact:{citations:[]},evidence:[
  {details:{coverage:[{topic_id:'new',status:'SEMANTIC_UNCERTAIN',validation_type:'SEMANTIC_UNCERTAIN'},
                     {topic_id:'old',status:'SOURCE_NOT_CITED'}]}}
 ]})
 expect(document.body.textContent).toContain('无法确认正文与完整规则一致')
 expect(document.body.textContent).toContain('缺少本轮实际来源引用')
 expect(document.body.textContent).not.toContain('已引用并体现关键规则')
})
