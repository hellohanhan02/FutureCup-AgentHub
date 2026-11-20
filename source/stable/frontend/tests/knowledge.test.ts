import {createApp,nextTick} from 'vue'
import {expect,it,vi} from 'vitest'
import Knowledge from '../src/pages/Knowledge.vue'

it('manages local knowledge and displays actual search source responses',async()=>{
  const calls:string[]=[]
  vi.stubGlobal('fetch',vi.fn(async(input,init)=>{
    const url=String(input);calls.push(url+':'+(init?.method??'GET'))
    const data=url.endsWith('/search')?[{id:'chunk',document_name:'规范.md',locator:'paragraph 1:0',text:'校园认证必须完成',score:-.2}]:url.endsWith('/documents')?[]:[{id:'kb',name:'模拟规范',version:1}]
    return new Response(JSON.stringify(data),{status:200})
  }))
  const root=document.createElement('div');const app=createApp(Knowledge);app.mount(root)
  const flush=async()=>{await nextTick();await new Promise(r=>setTimeout(r,15));await nextTick()}
  try{
    await flush();expect(root.textContent).toContain('非向量语义搜索')
    const select=root.querySelector('select')!;select.value='kb';select.dispatchEvent(new Event('change'));await flush()
    const input=root.querySelector('input[placeholder]') as HTMLInputElement;input.value='校园认证';input.dispatchEvent(new Event('input'));await flush()
    const search=[...root.querySelectorAll('button')].find(b=>b.textContent?.includes('检索 Top'))!;search.click();await flush()
    expect(root.textContent).toContain('校园认证必须完成');expect(root.textContent).toContain('paragraph 1:0')
    expect(calls).toContain('/api/v1/knowledge-bases/kb/search:POST')
  }finally{app.unmount();vi.unstubAllGlobals()}
})
