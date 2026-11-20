// DOM fixtures and simulated HTTP only; no live model calls.
import { createApp, nextTick, type App } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import Workspace from '../src/pages/Workspace.vue'
import { deliveryMarkdown, workspaceApi, type WorkspaceTask } from '../src/workspace'

let app: App | undefined
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
const plan = {
  name: '审查计划',
  goal: '用户目标',
  expected_delivery: '方案文档',
  steps: [
    {
      id: 's1',
      title: '需求分析',
      role: '分析师',
      responsibilities: '分析需求',
      skills: ['architecture'],
      depends_on: [],
      input_from: [],
      tools: [],
      verification: 'schema',
      deliverable: '方案',
    },
  ],
}
const fixture = () => ({
  id: 'ws-fixture',
  messages: ['原始需求'],
  state: 'awaiting_confirmation',
  scenario: 'architecture',
  mode: 'mock',
  planning: 'dynamic',
  simulated: true,
  revision: 1,
  plan,
  run_id: null,
  data: {},
  usage: { model_calls: 1 },
  max_calls: 6,
  budget_cny: 0.5,
  estimated_cny_reserved: 0,
  events: [],
})
async function mount(
  path = '/workspace',
  fail = false,
  real = false,
  taskOverride: Record<string, unknown> = {},
) {
  const calls: { url: string; body: any }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input, init) => {
      const url = String(input),
        body = init?.body ? JSON.parse(init.body) : null
      calls.push({ url, body })
      if (url.endsWith('/knowledge-bases'))
        return new Response(JSON.stringify((taskOverride as any).__libraries ?? []), {
          status: 200,
        })
      if (url.endsWith('/budget-preview'))
        return new Response(
          JSON.stringify({
            estimated_admission_upper_cny: 1.769472,
            budget_cny: body.budget_cny,
            server_budget_cny: 5,
            admitted: body.budget_cny >= 1.769472 && body.budget_cny <= 5,
          }),
          { status: 200 },
        )
      if (fail)
        return new Response(
          JSON.stringify({ error: { code: 'workspace_capacity', message: 'capacity' } }),
          { status: 409 },
        )
      const result = url.endsWith('/capabilities')
        ? { real_available: real, models: [] }
        : url.endsWith('/tasks') && !body
          ? []
          : url.endsWith('/confirm')
            ? { ...fixture(), state: 'executing', run_id: 'run-fixture' }
            : { ...fixture(), ...taskOverride }
      return new Response(JSON.stringify(result), { status: 200 })
    }),
  )
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workspace/:id?', component: Workspace },
      { path: '/professional', component: { template: '<p>专业编排</p>' } },
      { path: '/demo', component: { template: '<p>只读回放</p>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp(Workspace)
  app.use(router)
  app.mount(root)
  await flush()
  return { root, calls, router }
}
async function flush() {
  await nextTick()
  await new Promise((r) => setTimeout(r, 20))
  await nextTick()
}
function button(root: Element, text: string) {
  return [...root.querySelectorAll('button')].find((b) => b.textContent?.includes(text))!
}
it('shows three supported scenes, default Mock, and disabled real mode', async () => {
  const { root } = await mount()
  expect(root.textContent).toContain('Mock 本地模拟')
  expect(root.querySelector('option[value="real"]')?.hasAttribute('disabled')).toBe(true)
  expect(button(root, '生成可审查的计划').disabled).toBe(true)
  expect(root.textContent).toContain('专业')
})
it('requires explicit confirmation after actual create response and sends default Mock', async () => {
  const { root, calls } = await mount()
  const input = root.querySelector('textarea')!
  input.value = '规划架构'
  input.dispatchEvent(new Event('input'))
  await flush()
  button(root, '生成可审查的计划').click()
  await flush()
  const create = calls.find((c) => c.url.endsWith('/tasks') && c.body)
  expect(create?.body.mode).toBe('mock')
  expect(create?.body.request).toBe('规划架构')
  expect(calls.some((c) => c.url.endsWith('/confirm'))).toBe(false)
})
it('edits disable confirmation until saved and shows upstream role configuration', async () => {
  const { root, calls } = await mount('/workspace/ws-fixture')
  expect((root.querySelectorAll('.workspace-step input')[1] as HTMLInputElement).value).toBe(
    '分析师',
  )
  const input = root.querySelector('.workspace-step input') as HTMLInputElement
  input.value = '修改后的子任务'
  input.dispatchEvent(new Event('input'))
  await flush()
  expect(button(root, '确认计划').disabled).toBe(true)
  button(root, '保存计划修改').click()
  await flush()
  expect(
    calls.some((c) => c.url.endsWith('/plan') && c.body.plan.steps[0].title === '修改后的子任务'),
  ).toBe(true)
})
it('passes supplement and revision to backend rather than inventing another plan', async () => {
  const { root, calls } = await mount('/workspace/ws-fixture')
  const input = [...root.querySelectorAll('textarea')].find((t) =>
    t.parentElement?.textContent?.includes('补充需求'),
  )!
  input.value = '增加审查'
  input.dispatchEvent(new Event('input'))
  await flush()
  button(root, '补充并重新规划').click()
  await flush()
  expect(calls.find((c) => c.url.endsWith('/requirements'))?.body).toMatchObject({
    request: '增加审查',
    revision: 1,
  })
})
it('displays HTTP failures without synthesizing progress or results', async () => {
  const { root } = await mount('/workspace', true)
  expect(root.textContent).toContain('capacity')
  expect(root.querySelector('.workspace-step')).toBeNull()
})
it('exports readable verified results only, with explicit Mock label', () => {
  const task = {
    ...fixture(),
    delivery: { title: '交付成果', summary: '结构化方案', items: ['模块一'], order: ['A', 'B'] },
    report: { verified: true },
  } as unknown as WorkspaceTask
  expect(deliveryMarkdown(task)).toContain('# 交付成果')
  expect(deliveryMarkdown(task)).toContain('MOCK')
  expect(deliveryMarkdown({ ...task, report: { ...task.report!, verified: false } })).toBe('')
})

it('requests the explicit document profile without silently enabling real mode', async () => {
  const { root, calls } = await mount()
  const select = root.querySelector('option[value="design_document"]')!
    .parentElement as HTMLSelectElement
  select.value = 'design_document'
  select.dispatchEvent(new Event('change'))
  const input = root.querySelector('textarea')!
  input.value = '校园二手平台需求、架构、数据库、技术与审查'
  input.dispatchEvent(new Event('input'))
  await flush()
  expect(root.querySelector('option[value="template"]')?.hasAttribute('disabled')).toBe(true)
  button(root, '生成可审查的计划').click()
  await flush()
  expect(calls.find((c) => c.url.endsWith('/tasks') && c.body)?.body).toMatchObject({
    output_profile: 'design_document',
    planning: 'dynamic',
    mode: 'mock',
    max_calls: 6,
    authorize_real: false,
  })
})

it('keeps 0.5 default, previews costs without execution, and requires explicit two-yuan consent', async () => {
  const { root, calls } = await mount('/workspace', false, true)
  const mode = root.querySelector('option[value="real"]')!.parentElement as HTMLSelectElement
  mode.value = 'real'
  mode.dispatchEvent(new Event('change'))
  await flush()
  await new Promise((r) => setTimeout(r, 300))
  await flush()
  const presets = root.querySelector('[aria-label="任务预算档位"]') as HTMLSelectElement
  expect(presets.value).toBe('0.5')
  expect(root.textContent).toContain('1.769472')
  expect(calls.some((c) => c.url.endsWith('/tasks') && c.body)).toBe(false)
  presets.value = '2'
  presets.dispatchEvent(new Event('change'))
  const input = root.querySelector('textarea')!
  input.value = '校园设计'
  input.dispatchEvent(new Event('input'))
  await flush()
  await new Promise((r) => setTimeout(r, 300))
  await flush()
  expect(button(root, '生成可审查的计划').disabled).toBe(true)
  const consent = [...root.querySelectorAll('input[type="checkbox"]')].find((x) =>
    x.parentElement?.textContent?.includes('我确认价格'),
  ) as HTMLInputElement
  consent.checked = true
  consent.dispatchEvent(new Event('change'))
  await flush()
  button(root, '生成可审查的计划').click()
  await flush()
  expect(calls.find((c) => c.url.endsWith('/tasks') && c.body)?.body).toMatchObject({
    budget_cny: 2,
    authorize_real: true,
    max_calls: 6,
    mode: 'real',
  })
})

it('supports custom budget and clears previous consent when its amount changes', async () => {
  const { root, calls } = await mount('/workspace', false, true)
  const mode = root.querySelector('option[value="real"]')!.parentElement as HTMLSelectElement
  mode.value = 'real'
  mode.dispatchEvent(new Event('change'))
  await flush()
  const consent = [...root.querySelectorAll('input[type="checkbox"]')].find((x) =>
    x.parentElement?.textContent?.includes('我确认价格'),
  ) as HTMLInputElement
  consent.checked = true
  consent.dispatchEvent(new Event('change'))
  await flush()
  const presets = root.querySelector('[aria-label="任务预算档位"]') as HTMLSelectElement
  expect([...presets.options].map((x) => x.value)).toEqual(['0.5', '1', '2', '5', 'custom'])
  presets.value = 'custom'
  presets.dispatchEvent(new Event('change'))
  await flush()
  const amount = root.querySelector('input[type="number"]') as HTMLInputElement
  amount.value = '3.25'
  amount.dispatchEvent(new Event('input'))
  await flush()
  await new Promise((r) => setTimeout(r, 300))
  await flush()
  expect(consent.checked).toBe(false)
  expect(calls.filter((c) => c.url.endsWith('/budget-preview')).at(-1)?.body.budget_cny).toBe(3.25)
  expect(calls.some((c) => c.url.endsWith('/tasks') && c.body)).toBe(false)
})

it('explains the actual real planning failure without a Mock disclaimer', async () => {
  const { root } = await mount('/workspace/ws-fixture', false, true, {
    state: 'failed',
    mode: 'real',
    simulated: false,
    error_code: 'workspace_validator',
    error_details: [
      {
        path: 'steps.1.verification',
        rule: 'scenario_validator_allowlist',
        message: '架构方案节点必须使用 schema，不能使用排序验证器。',
      },
    ],
    planning_verifications: [{ status: 'FAILED' }],
    planning_attempts: [],
    planning_feedback: [],
  })
  expect(root.textContent).toContain('真实模型动态规划')
  expect(root.textContent).not.toContain('Mock 仅验证接口与流程')
  expect(root.textContent).not.toContain('Mock 模拟规划，仅验证接口与流程')
  expect(root.textContent).toContain('steps.1.verification')
  expect(root.textContent).toContain('不能使用排序验证器')
  expect(root.textContent).toContain('查看规划校验与反馈修复审计')
})

it('distinguishes execution failure from planning failure and keeps actual cause visible', async () => {
  const { root } = await mount('/workspace/ws-fixture', false, true, {
    state: 'failed',
    mode: 'real',
    simulated: false,
    run_id: 'historical-run',
    error_code: 'workflow_node_failed',
    error_phase: 'execution',
    error_details: [
      {
        path: 'nodes.s1',
        rule: 'provider_incomplete',
        message: '供应商响应未正常结束，旧记录未保留具体结束原因。',
      },
    ],
  })
  expect(root.textContent).toContain('协作执行阶段失败；规划已通过')
  expect(root.textContent).toContain('供应商响应未正常结束')
  expect(root.textContent).not.toContain('任务规划阶段失败')
  expect(root.textContent).not.toContain('任务规划未通过安全校验')
})

it('distinguishes same-name libraries by document count, version and short ID', async () => {
  const { root } = await mount('/workspace', false, false, {
    __libraries: [
      { id: '13ebda9f-full', name: '校园业务库', version: 2, document_count: 1 },
      { id: 'b797a5d9-empty', name: '校园业务库', version: 1, document_count: 0 },
    ],
  })
  const options = [...root.querySelectorAll('select[multiple] option')] as HTMLOptionElement[]
  expect(options[0].textContent).toContain('1 文档 · v2 · 13ebda9f')
  expect(options[0].disabled).toBe(false)
  expect(options[1].textContent).toContain('0 文档 · v1 · b797a5d9')
  expect(options[1].disabled).toBe(true)
})

it('refetches current route delivery for export rather than using stale rendered text', async () => {
  const old = { title: 'OLD', summary: 'old cached content', items: ['old'] }
  const fresh = { title: 'LATEST', summary: 'latest real content', items: ['current'] }
  const { root } = await mount('/workspace/ws-fixture', false, false, {
    state: 'succeeded',
    delivery: old,
    report: { verified: true },
  })
  vi.spyOn(workspaceApi, 'task').mockResolvedValue({
    ...fixture(),
    state: 'succeeded',
    delivery: fresh,
    report: { verified: true },
  } as unknown as WorkspaceTask)
  const blobs: any[] = []
  vi.stubGlobal(
    'Blob',
    class {
      constructor(parts: any[]) {
        blobs.push(parts)
      }
    },
  )
  const OriginalURL = URL
  vi.stubGlobal(
    'URL',
    class extends OriginalURL {
      static createObjectURL() {
        return 'blob:test'
      }
      static revokeObjectURL() {}
    },
  )
  let filename = ''
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    filename = this.download
  })
  button(root, '导出 Markdown').click()
  await flush()
  expect(blobs[0][0]).toContain('LATEST')
  expect(blobs[0][0]).not.toContain('old cached content')
  expect(filename).toBe('ws-fixture.md')
})

it('ignores a late confirmation response after switching tasks', async () => {
  const { root, router } = await mount('/workspace/ws-fixture')
  let finish!: (value: WorkspaceTask) => void
  vi.spyOn(workspaceApi, 'confirm').mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  button(root, '确认计划').click()
  await flush()
  vi.spyOn(workspaceApi, 'task').mockResolvedValue({
    ...fixture(),
    id: 'ws-new',
    messages: ['NEW TASK'],
  } as unknown as WorkspaceTask)
  await router.push('/workspace/ws-new')
  await flush()
  finish({ ...fixture(), state: 'executing', messages: ['OLD TASK'] } as unknown as WorkspaceTask)
  await flush()
  expect(root.querySelector('[data-task-id]')?.getAttribute('data-task-id')).toBe('ws-new')
  expect(root.textContent).toContain('NEW TASK')
  expect(root.textContent).not.toContain('OLD TASK')
})

it('uses explicit staged knowledge contract for a document with a selected library', async () => {
  const { root, calls } = await mount('/workspace', false, false, {
    __libraries: [{ id: 'kb-source', name: 'SIMULATED rules', version: 1, document_count: 1 }],
  })
  const profile = root.querySelector('option[value="design_document"]')!.parentElement as HTMLSelectElement
  profile.value = 'design_document'
  profile.dispatchEvent(new Event('change'))
  const option = root.querySelector('select[multiple] option') as HTMLOptionElement
  option.selected = true
  option.parentElement!.dispatchEvent(new Event('change'))
  const input = root.querySelector('textarea')!
  input.value = 'Extract constraints; design components; consolidate the document'
  input.dispatchEvent(new Event('input'))
  await flush()
  button(root, '生成可审查的计划').click()
  await flush()
  expect(calls.find(c => c.url.endsWith('/tasks') && c.body)?.body).toMatchObject({
    knowledge_contract: 'staged-rules-v1',
    knowledge_base_ids: ['kb-source'],
    output_profile: 'design_document',
    mode: 'mock',
    authorize_real: false,
    max_calls: 6,
  })
})

it('explicitly selects v2 without changing real consent or budgets', async () => {
  const { root, calls } = await mount('/workspace', false, false, {
    __libraries: [{ id: 'kb-source', name: 'SIMULATED rules', version: 1, document_count: 1 }],
  })
  const profile = root.querySelector('option[value="design_document"]')!.parentElement as HTMLSelectElement
  profile.value = 'design_document'
  profile.dispatchEvent(new Event('change'))
  const option = root.querySelector('select[multiple] option') as HTMLOptionElement
  option.selected = true
  option.parentElement!.dispatchEvent(new Event('change'))
  await flush()
  const contract = root.querySelector('[data-testid="source-contract"]') as HTMLSelectElement
  contract.value = 'staged-rules-v2'
  contract.dispatchEvent(new Event('change'))
  const input = root.querySelector('textarea')!
  input.value = 'Extract requirements; design architecture; deliver'
  input.dispatchEvent(new Event('input'))
  await flush()
  button(root, '生成可审查的计划').click()
  await flush()
  expect(calls.find(c => c.url.endsWith('/tasks') && c.body)?.body).toMatchObject({
    knowledge_contract: 'staged-rules-v2', mode: 'mock', authorize_real: false, max_calls: 6,
  })
})

it('exports server evidence separately without replacing model Markdown', () => {
  const task = {
    ...fixture(), simulated: true, report: { verified: true },
    delivery: { title: 'MOCK document', summary: 'Mock summary', items: ['Mock'], markdown: 'ACTUAL MODEL BODY' },
    delivery_sources: [{ source_ref: 'ref', quote: 'AUTHORITATIVE SOURCE', document_name: 'mock.md', document_version: 1, start: 0, end: 20 }],
  } as unknown as WorkspaceTask
  const before = task.delivery!.markdown
  const output = deliveryMarkdown(task)
  expect(task.delivery!.markdown).toBe(before)
  expect(output).toContain('ACTUAL MODEL BODY')
  expect(output).toContain('Program-restored source evidence (not model design)')
  expect(output).toContain('AUTHORITATIVE SOURCE')
  expect(deliveryMarkdown({ ...task, report: { ...task.report!, verified: false } })).toBe('')
})
