// Synthetic UI fixtures only; no model requests or historical data edits.
import { createApp, h, nextTick, type App } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { benchmarkOption, violationText, type Metric } from '../src/presentation'
import CaseFlow from '../src/components/CaseFlow.vue'
import Runs from '../src/pages/Runs.vue'
import type { DemoInfo, Verification } from '../src/types'
let app: App | undefined
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})
const groups = ['A_uniform', 'B_heterogeneous', 'C_dynamic_fault', 'D_bursty'].flatMap((scenario) =>
  ['fixed', 'round_robin', 'caas'].map((strategy) => ({
    scenario,
    strategy,
    capacity_violations: 0,
    metrics: {
      success_rate: { mean: 0.8 },
      mean_latency_ms: { mean: 50 },
      p95_latency_ms: { mean: 90 },
    },
  })),
)
it.each<Metric>(['success_rate', 'mean_latency_ms', 'p95_latency_ms'])(
  'renders all four scenarios and all strategies for %s',
  (metric) => {
    const option = benchmarkOption(groups, metric)
    expect(option.xAxis.data).toHaveLength(4)
    expect(option.series.map((s) => s.name)).toEqual(['fixed', 'round_robin', 'caas'])
    expect(option.series.every((s) => s.data.length === 4)).toBe(true)
    expect(option.series[0].data[0]).toBe(
      metric === 'success_rate' ? 80 : metric === 'mean_latency_ms' ? 50 : 90,
    )
    expect(option.animation).toBe(false)
  },
)
it('keeps missing values unknown instead of plotting zero', () => {
  const data = JSON.parse(JSON.stringify(groups))
  data[0].metrics.success_rate.mean = null
  expect(benchmarkOption(data, 'success_rate').series[0].data[0]).toBeNull()
})
it('explains actual rule fields in Chinese without inventing repair results', () => {
  const evidence = (reason: string, violations: unknown[]) =>
    ({ status: 'FAILED', reason, details: { violations } }) as Verification['evidence'][number]
  expect(
    violationText(
      evidence('order09c_dependencies_violation', [
        { before: 'A', after: 'D', before_position: 6, after_position: 3 },
      ]),
    )[0],
  ).toContain('A（第 6 位）必须先于 D（第 3 位）')
  expect(
    violationText(
      evidence('order09c_windows_violation', [{ task: 'C', actual_position: 4, min: 2, max: 3 }]),
    )[0],
  ).toContain('允许范围为 2～3')
  expect(
    violationText(
      evidence('order09c_completion_violation', [{ task: 'C', actual_completion: 7, latest: 4 }]),
    )[0],
  ).toContain('累计完成时间 7，最迟应为 4')
})
const makeRun = (verdicts: string[]) =>
  ({
    execution_order: ['schedule'],
    nodes: [
      {
        node_id: 'schedule',
        state: 'succeeded',
        attempts: verdicts.map((verdict, i) => ({
          id: 'a' + i,
          ordinal: i + 1,
          verdict,
          model: 'SIMULATED',
          resolved_model: 'SIMULATED',
          input_tokens: 3,
          output_tokens: 2,
        })),
        feedback_bindings: verdicts.length > 1 ? [{ attempt_id: 'a1', confirmed: true }] : [],
      },
    ],
  }) as DemoInfo['runs'][number]
it('renders recorded repair success and failure distinctly', async () => {
  const root = document.createElement('div')
  document.body.append(root)
  const run = makeRun(['fail', 'fail'])
  app = createApp({ render: () => h(CaseFlow, { run }) })
  app.mount(root)
  await nextTick()
  expect(root.textContent).toContain('Feedback')
  expect(root.textContent).not.toContain('验证通过')
  app.unmount()
  app = createApp({ render: () => h(CaseFlow, { run: makeRun(['fail', 'pass']) }) })
  app.mount(root)
  await nextTick()
  expect(root.textContent).toContain('验证通过')
})
it('does not add feedback to first-pass records', async () => {
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(CaseFlow, { run: makeRun(['pass']) }) })
  app.mount(root)
  await nextTick()
  expect(root.textContent).not.toContain('Feedback')
})
it('derives monitoring totals only from returned runs', async () => {
  const records = [true, false].map((verified, i) => ({
    id: 'fixture-' + i,
    workflow: { id: 'workflow-' + i, version: '1' },
    state: 'succeeded',
    verified,
    simulated: i === 1,
    elapsed_ms: 10,
    started_at: null,
    ended_at: null,
    nodes: 1,
    failed_nodes: 0,
  }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(records))),
  )
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Runs },
      { path: '/runs/:id', component: Runs },
    ],
  })
  await router.push('/')
  await router.isReady()
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp(Runs)
  app.use(router)
  app.mount(root)
  await new Promise((r) => setTimeout(r, 40))
  await nextTick()
  expect([...root.querySelectorAll('.monitor-summary strong')].map((e) => e.textContent)).toEqual([
    '2',
    '2',
    '1',
    '1',
  ])
  expect(root.querySelectorAll('tbody tr')).toHaveLength(2)
})
