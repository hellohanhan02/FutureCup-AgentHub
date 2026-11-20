// Explicit DOM/HTTP fixtures, not live model or browser inference tests.
import { afterEach, describe, it, expect, vi } from 'vitest'
import { createApp, h, nextTick, type App, type Component } from 'vue'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import Models from '../src/pages/Models.vue'
import ModelUsage from '../src/components/ModelUsage.vue'
import { initializeScenarioMocks, scenarioWorkflow, scenarioInput } from '../src/scenarios'
let app: App | undefined
afterEach(() => {
  app?.unmount()
  app = undefined
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })
async function mount(component: Component, props: Record<string, unknown> = {}) {
  const root = document.createElement('div')
  document.body.append(root)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component, props: () => props }],
  })
  await router.push('/')
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
  await nextTick()
  await new Promise((r) => setTimeout(r, 30))
  await nextTick()
  return root
}
describe('model management privacy and honest telemetry', () => {
  it('shows disabled configuration without a connection probe or key input', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => {
        calls.push(path)
        return response({
          real_calls_enabled: false,
          network_probe_performed: false,
          providers: [
            {
              id: 'deepseek',
              kind: 'deepseek',
              mode: 'real',
              enabled: false,
              configured: true,
              base_url: 'https://api.deepseek.com',
              key_env: 'DEEPSEEK_API_KEY',
              credentials_configured: true,
              connection: { state: 'disabled', error_code: 'real_calls_disabled', at: null },
              models: [
                {
                  id: 'deepseek-flash',
                  supports_tools: false,
                  response_formats: ['text', 'prompt_json'],
                  max_output_tokens: 1024,
                  max_context_bytes: 32768,
                  input_price_per_million: null,
                  output_price_per_million: null,
                  currency: null,
                },
              ],
            },
          ],
        })
      }),
    )
    const root = await mount(Models)
    expect(root.textContent).toContain('真实模型调用默认关闭')
    expect(root.textContent).toContain('DEEPSEEK_API_KEY')
    expect(root.textContent).not.toContain('最近一次请求成功')
    expect(document.querySelector('input[type=password]')).toBeNull()
    expect(calls).toEqual(['/api/v1/model-providers'])
  })
  it('does not present missing usage or pricing as zero cost', async () => {
    const root = await mount(ModelUsage, {
      value: {
        provider: 'mock',
        model: 'mock-scenario08',
        input_tokens: 0,
        output_tokens: 0,
        usage_complete: false,
        estimated_cost: null,
        cost_complete: false,
      },
    })
    expect(root.textContent).toContain('不等同于零消耗')
    expect(root.textContent).toContain('未知')
    expect(root.textContent).toContain('非实付账单')
    expect(root.textContent).not.toContain('0.000000')
  })
  it('displays requested and provider-reported model identities separately', async () => {
    const root = await mount(ModelUsage, {
      value: {
        provider: 'configured',
        model: 'alias',
        resolved_model: 'snapshot-1',
        input_tokens: 3,
        output_tokens: 4,
        usage_complete: true,
        estimated_cost: 0.000011,
        cost_currency: 'CNY',
        cost_complete: true,
      },
    })
    expect(root.textContent).toContain('alias')
    expect(root.textContent).toContain('snapshot-1')
    expect(root.textContent).toContain('0.000011 CNY')
    expect(root.textContent).toContain('非费用硬预算')
  })
})
describe('task 08 scenario boundaries', () => {
  it('loads fixed standards and native workflow/CAAS semantics', () => {
    const a = scenarioWorkflow('requirements'),
      b = scenarioWorkflow('data_analysis'),
      c = scenarioWorkflow('selfloop')
    expect(a.nodes).toHaveLength(3)
    expect(b.scheduling_strategy).toBe('caas')
    expect(c.nodes[0].config.recovery).toMatchObject({ max_attempts: 3, max_feedback_loops: 2 })
    expect(scenarioInput('data_analysis').values).toEqual([10, 20, 30])
    expect(a.nodes[1].config.input).toMatchObject({
      upstream: { plan: { $ref: 'nodes.plan.output' } },
    })
  })
  it('Mock initialization never executes a model and is repeatable', async () => {
    const agents: unknown[] = [],
      policies: unknown[] = [],
      calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options: RequestInit) => {
        calls.push(path)
        if (options.method === 'POST') {
          const body = JSON.parse(String(options.body))
          if (path.endsWith('/agents')) {
            agents.push({ ...body, enabled: true, available: true })
            return response(agents.at(-1))
          }
          policies.push({ ...body, ref: { id: body.id, version: body.version } })
          return response(policies.at(-1))
        }
        return response(path.endsWith('/agents') ? agents : policies)
      }),
    )
    await initializeScenarioMocks()
    await initializeScenarioMocks()
    expect(agents).toHaveLength(7)
    expect(policies).toHaveLength(7)
    expect(calls.some((path) => path.includes('executions'))).toBe(false)
  })
  it('refuses to reuse scenario Agents changed to Real mode', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response([
          {
            id: 'llm08-planner',
            provider: 'deepseek',
            model: 'deepseek-flash',
            version: 'real-1',
            enabled: true,
            available: true,
          },
        ]),
      ),
    )
    await expect(initializeScenarioMocks()).rejects.toThrow('不会覆盖或使用真实模型')
  })
})
