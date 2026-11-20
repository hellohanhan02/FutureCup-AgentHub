// DOM fixtures only; these flags test labeling, never claim live-model execution.
import { createApp, h, nextTick, type App, type Component } from 'vue'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import { afterEach, describe, it, expect, vi } from 'vitest'
import Demo from '../src/pages/Demo.vue'
import RunDetail from '../src/pages/RunDetail.vue'

let app: App | undefined
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})
async function mount(component: Component, path = '/demo') {
  const root = document.createElement('div')
  document.body.append(root)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/demo', component },
      { path: '/runs/:id', component },
    ],
  })
  await router.push(path)
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
  await new Promise((r) => setTimeout(r, 40))
  await nextTick()
  return root
}
const run = {
  run_id: 'fixture',
  state: 'succeeded',
  simulated: false,
  verified: true,
  elapsed_ms: 3252,
  started_at: '2026-11-12T14:03:33+00:00',
  execution_order: ['analyse', 'design', 'review'],
  resource_usage: { model_calls: 2, reported_tokens: 2119, feedback_loops: 1 },
  data_mappings: [],
}
const info = {
  mode: 'historical_read_only',
  runs: [run],
  redactions: {},
  benchmark: {
    groups: ['A_uniform', 'B_heterogeneous', 'C_dynamic_fault', 'D_bursty'].flatMap((scenario) =>
      ['fixed', 'round_robin', 'caas'].map((strategy) => ({
        scenario,
        strategy,
        metrics: {
          success_rate: { mean: 0.9 },
          mean_latency_ms: { mean: 50 },
          p95_latency_ms: { mean: 60 },
        },
      })),
    ),
    paired_comparisons: [{ significant_05: false }],
  },
}

describe('safe historical demonstration', () => {
  it('does not invent statistics when the benchmark is missing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ...info, benchmark: null }))),
    )
    const root = await mount(Demo)
    expect(root.textContent).toContain('统计摘要尚未载入，不推断显著性')
    expect(root.textContent).toContain('未导入 10-A 基准数据')
    expect(root.querySelectorAll('tbody tr')).toHaveLength(0)
  })
  it('renders all 12 benchmark groups, historical links, and performs only GETs', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options: RequestInit) => {
        expect(options.method ?? 'GET').toBe('GET')
        calls.push(path)
        return new Response(
          JSON.stringify(path.endsWith('/demo-info') ? info : { nodes: [], edges: [] }),
        )
      }),
    )
    const root = await mount(Demo)
    expect(root.querySelectorAll('tbody tr')).toHaveLength(12)
    expect(root.querySelector('.raw-benchmark')?.hasAttribute('open')).toBe(false)
    ;[...root.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'P95 延迟')!.click()
    await nextTick()
    expect(root.querySelector('[role=img]')?.getAttribute('aria-label')).toContain('p95_latency_ms')
    expect(root.textContent).toContain('真实模型执行记录')
    expect(root.textContent).toContain('不重新调用 DeepSeek')
    expect(root.textContent).toContain('Holm 校正后无显著优势')
    expect(root.textContent).toContain('运行时构建版本：未知')
    expect(root.querySelector('a')?.getAttribute('href')).toContain('/runs/fixture')
    ;[...root.querySelectorAll('button')]
      .find((b) => b.textContent?.includes('查看已保存结构'))!
      .click()
    await new Promise((r) => setTimeout(r, 20))
    expect(calls).toContain('/api/v1/demo-structure/fixture')
  })

  it('never labels a simulated record as a real record', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => new Response(JSON.stringify({ ...info, runs: [{ ...run, simulated: true }] })),
      ),
    )
    const root = await mount(Demo)
    expect(root.textContent).toContain('MOCK 模拟记录')
    expect(root.textContent).not.toContain('真实模型执行记录')
  })

  it('shows a recovery instruction on a normal backend or missing package', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ mode: 'interactive' }))),
    )
    const root = await mount(Demo)
    expect(root.querySelector('[role=alert]')?.textContent).toContain('专用回放服务')
  })

  it('marks the existing detail page as read-only with real/unknown-version labels', async () => {
    const report = {
      run: {
        id: 'fixture',
        definition_ref: { id: 'workflow', version: '1' },
        state: 'succeeded',
        revision: 1,
        parent_run_id: null,
      },
      replay: { read_only: true, sanitized: true },
      resource_usage: run.resource_usage,
      nodes: [],
      events: [],
      simulated: false,
      verified: true,
      elapsed_ms: 3252,
      started_at: run.started_at,
      error_code: null,
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (path: string) =>
          new Response(
            JSON.stringify(
              path.includes('/scheduling/') || path.endsWith('/human-interventions') ? [] : report,
            ),
          ),
      ),
    )
    const root = await mount(RunDetail, '/runs/fixture')
    expect(root.textContent).toContain('历史记录只读回放')
    expect(root.textContent).toContain('运行时构建版本未知')
    expect(root.textContent).toContain('真实模型执行记录')
    expect(root.textContent).toContain('2119 / 1')
    expect(
      [...root.querySelectorAll('button')].some((b) => b.textContent?.includes('取消执行')),
    ).toBe(false)
  })
})
