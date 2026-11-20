// Component interactions in jsdom only. These tests do not verify browser layout/dragging.
import { createApp, h, nextTick, type App, type Component } from 'vue'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Workflows from '../src/pages/Workflows.vue'
import StatusBadge from '../src/components/StatusBadge.vue'
import Agents from '../src/pages/Agents.vue'
import Dashboard from '../src/pages/Dashboard.vue'
import RunDetail from '../src/pages/RunDetail.vue'

let app: App | undefined
afterEach(() => {
  app?.unmount()
  app = undefined
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
async function settle() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 25))
  await nextTick()
}
async function mount(component: Component, props: Record<string, unknown> = {}) {
  const root = document.createElement('div')
  document.body.append(root)
  const path = component === RunDetail ? '/runs/:id' : '/'
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path, component, props: () => props }],
  })
  await router.push(component === RunDetail ? '/runs/dom-run' : '/')
  await router.isReady()
  app = createApp({
    render: () =>
      h(NConfigProvider, null, {
        default: () =>
          h(NMessageProvider, null, {
            default: () => h(NDialogProvider, null, { default: () => h(RouterView) }),
          }),
      }),
  })
  app.use(router)
  app.mount(root)
  await settle()
  return root
}
function button(text: string) {
  const element = [...document.querySelectorAll('button')].find((b) =>
    b.textContent?.trim().startsWith(text),
  )
  expect(element, `Missing button ${text}`).toBeTruthy()
  return element!
}
function input(label: string) {
  const element = [...document.querySelectorAll('label')]
    .find((l) => l.textContent?.includes(label))
    ?.querySelector('input,textarea,select') as
    HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
  expect(element, `Missing input ${label}`).toBeTruthy()
  return element
}
async function fill(
  element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string,
) {
  element.value = value
  element.dispatchEvent(
    new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }),
  )
  await nextTick()
}

describe('honest status and form behavior (DOM simulation)', () => {
  it('Dashboard uses empty states and never fabricates a success rate', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response({
          scope: 'latest_200_root_workflow_runs',
          sample_size: 0,
          agents: { total: 0, enabled: 0, available: 0 },
          workflows: { total: 0, versions: 0 },
          runs: { total: 0, terminal: 0, succeeded: 0, active: 0 },
          verification: { PASSED: 0, FAILED: 0, INCONCLUSIVE: 0, ERROR: 0 },
          strategies: { fixed: 0, round_robin: 0, caas: 0 },
          recent: [],
          exceptions: [],
        }),
      ),
    )
    const root = await mount(Dashboard)
    expect(root.textContent).toContain('还没有执行记录')
    expect(root.textContent).toContain('尚无调度数据')
    expect(root.textContent).not.toContain('100%')
  })
  it('visually labels unknown verification separately from execution success', async () => {
    const root = await mount(StatusBadge, { state: 'INCONCLUSIVE' })
    expect(root.textContent).toContain('无法判断')
    expect(root.querySelector('.badge.warning')).not.toBeNull()
    expect(root.textContent).not.toContain('验证通过')
  })
  it('Agent creation submits actual form fields and never accepts an API key', async () => {
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options: RequestInit) => {
        if (path === '/api/v1/agents' && options.method === 'POST') {
          const body = JSON.parse(String(options.body))
          bodies.push(body)
          return response({ ...body, system_prompt: undefined }, 201)
        }
        return response([])
      }),
    )
    await mount(Agents)
    button('创建 Agent').click()
    await settle()
    for (const [label, value] of [
      ['Agent ID', 'ui-test-agent'],
      ['名称', 'DOM 模拟测试 Agent'],
      ['角色', 'research'],
      ['职责说明', '明确的组件自动化测试'],
      ['System Prompt', 'Explicit Mock test prompt'],
      ['技能标签', 'analysis, writing'],
    ])
      await fill(input(label), value)
    expect(document.querySelector('input[type=password]')).toBeNull()
    document
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({
      id: 'ui-test-agent',
      provider: 'mock',
      model: 'mock-echo',
      skills: ['analysis', 'writing'],
      system_prompt: 'Explicit Mock test prompt',
    })
  })
})

describe('causal chain rendering from explicit test fixtures', () => {
  it('preserves failed evidence, repair feedback and a separate passed artifact', async () => {
    const node = {
      id: 'node',
      node_id: 'repair',
      state: 'succeeded',
      input: { topic: 'Mock' },
      output: { answer: 42 },
      elapsed_ms: 10,
      agent_ref: { id: 'fc-repair', version: '1' },
      execution_id: 'execution',
      error_code: null,
      simulated: true,
      verified: true,
      attempt_ids: ['try-1', 'try-2'],
      verification_ids: ['v1', 'v2'],
      feedback_ids: ['f1'],
      child_run_ids: [],
      waiting_ms: 0,
      scheduling_decision_id: 'decision',
    }
    const report = {
      run: {
        id: 'dom-run',
        definition_ref: { id: 'demo', version: '1' },
        state: 'succeeded',
        parent_run_id: null,
        revision: 10,
      },
      input: { topic: 'Mock' },
      output: { repair: { answer: 42 } },
      nodes: [node],
      elapsed_ms: 10,
      started_at: null,
      ended_at: null,
      simulated: true,
      verified: true,
      events: [],
      resource_usage: { model_calls: 2, tool_calls: 0 },
    }
    const attempts = [1, 2].map((i) => ({
      id: 'try-' + i,
      ordinal: i,
      state: 'completed',
      input_ref: { id: 'input', version: String(i) },
      input_digest: 'digest',
      input_value: { topic: 'Mock' },
      agent_ref: { id: 'fc-repair', version: '1' },
      provider: 'mock',
      model: 'mock-repair-demo',
      decision_id: 'decision',
      execution_id: 'execution-' + i,
      simulated: true,
      error_code: null,
      started_at: null,
      ended_at: null,
      resources: { model_calls: 1 },
      artifact: {
        ref: { id: 'artifact', version: String(i) },
        digest: 'digest-' + i,
        content: i === 1 ? { wrong: 0 } : { answer: 42 },
        simulated: true,
        execution_error: null,
      },
    }))
    const verifications = [1, 2].map((i) => ({
      record: {
        id: 'v' + i,
        verdict: i === 1 ? 'fail' : 'pass',
        reason_codes: i === 1 ? ['schema_mismatch'] : [],
        verifier_ref: { id: 'policy', version: '1' },
      },
      attempt_id: 'try-' + i,
      status: i === 1 ? 'FAILED' : 'PASSED',
      artifact_ref: { id: 'artifact', version: String(i) },
      artifact_digest: 'digest-' + i,
      policy_digest: 'policy-digest',
      origin: 'artifact',
      elapsed_ms: 1,
      evidence: [
        {
          kind: 'external_tests',
          status: 'INCONCLUSIVE',
          reason: 'order09c_not_evaluated_precondition',
          path: 'order',
          reference: null,
          details: { evaluation: 'NOT_EVALUATED', blocked_by: 'order09c_membership' },
        },
        {
          kind: 'json_schema',
          status: i === 1 ? 'FAILED' : 'PASSED',
          reason: i === 1 ? 'schema_mismatch' : 'schema_matched',
          path: null,
          reference: null,
          details:
            i === 1 ? { violations: [{ task: 'C', latest: 4, actual_completion: 6 }] } : null,
        },
      ],
    }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        response(
          path.endsWith('/attempts')
            ? attempts
            : path.endsWith('/verifications')
              ? verifications
              : path.endsWith('/feedback')
                ? [
                    {
                      record: { id: 'f1', action: 'retry', reason: 'OUTPUT_FORMAT_ERROR' },
                      attempt_id: 'try-1',
                      category: 'OUTPUT_FORMAT_ERROR',
                      origin: 'artifact',
                      repair_action: 'prompt_retry',
                      actual_budget_cost: { feedback_loops: 1 },
                      evidence_refs: ['v1'],
                    },
                  ]
                : path.includes('/scheduling/') || path.endsWith('/human-interventions')
                  ? []
                  : report,
        ),
      ),
    )
    const root = await mount(RunDetail)
    await settle()
    expect(root.querySelectorAll('.attempt-card')).toHaveLength(2)
    expect(root.textContent).toContain('隔离产物')
    expect(root.textContent).toContain('验证失败')
    expect(root.textContent).toContain('验证通过')
    expect(root.textContent).toContain('OUTPUT_FORMAT_ERROR')
    expect(root.textContent).toContain('prompt_retry')
    expect(root.textContent).toContain('约束明细')
    expect(root.textContent).toContain('actual_completion')
    expect(root.textContent).toContain('未评估（前置条件不满足）')
    expect(root.textContent).toContain('latest')
  })
})

describe('real Vue Flow component state with no browser layout claim', () => {
  it('loads sequential semantics, adds a real condition config and posts preflight', async () => {
    const values: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options: RequestInit) => {
        if (path === '/api/v1/workflows/validate') {
          values.push(JSON.parse(String(options.body)))
          return response({ valid: true })
        }
        return response([])
      }),
    )
    await mount(Workflows)
    button('多 Agent 顺序协作').click()
    await settle()
    expect(document.querySelectorAll('.harness-node')).toHaveLength(3)
    button('条件分支').click()
    await settle()
    expect(document.querySelectorAll('.harness-node')).toHaveLength(4)
    button('合法性检查').click()
    await settle()
    expect(values).toHaveLength(1)
    const nodes = values[0].nodes as { kind: string; config: unknown }[]
    expect(nodes.filter((n) => n.kind === 'condition')).toHaveLength(1)
    expect(nodes.find((n) => n.kind === 'condition')!.config).toMatchObject({
      predicate: { left: { $ref: 'input.approved' }, op: 'eq', right: true },
    })
  })
  it('rejects invalid workflow JSON locally without submitting or executing', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => {
        calls.push(path)
        return response([])
      }),
    )
    await mount(Workflows)
    button('JSON 定义').click()
    await settle()
    const field = document.querySelector('[aria-label="工作流 JSON 定义"]') as HTMLTextAreaElement
    await fill(field, 'not json')
    button('合法性检查').click()
    await settle()
    expect(calls.some((c) => c.endsWith('/validate') || c.includes('/executions'))).toBe(false)
    expect(document.querySelector('[role=alert]')).not.toBeNull()
  })
})
