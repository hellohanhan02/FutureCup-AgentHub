// Explicit DOM/HTTP fixture; no browser layout or model inference claim.
import { createApp, h, nextTick, type App } from 'vue'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import { afterEach, expect, it, vi } from 'vitest'
import Dashboard from '../src/pages/Dashboard.vue'
vi.mock('../src/components/StrategyChart.vue', () => ({ default: { render: () => null } }))
let app: App | undefined
afterEach(() => { app?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals() })
it('labels Adaptive CAAS separately from Round Robin in actual overview data', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    scope: 'latest_200_root_workflow_runs', sample_size: 1,
    agents: { total: 1, enabled: 1, available: 1 }, workflows: { total: 1, versions: 1 },
    runs: { total: 1, terminal: 1, succeeded: 1, active: 0 },
    verification: { PASSED: 0, FAILED: 0, INCONCLUSIVE: 0, ERROR: 0 },
    strategies: { fixed: 0, round_robin: 0, caas: 0, adaptive_caas: 1 }, recent: [], exceptions: [],
  }), { status: 200 })))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Dashboard }] })
  await router.push('/'); await router.isReady()
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(NConfigProvider, null, { default: () => h(NMessageProvider, null, {
    default: () => h(NDialogProvider, null, { default: () => h(RouterView) }),
  }) }) })
  app.use(router); app.mount(root); await nextTick()
  await new Promise(resolve => setTimeout(resolve, 50)); await nextTick()
  const legend = root.querySelector('.chart-legend')!
  expect(legend).not.toBeNull()
  expect(legend.textContent).toContain('Adaptive CAAS')
  expect(legend.textContent?.match(/Round Robin/g)).toHaveLength(1)
  expect(legend.querySelectorAll('strong')).toHaveLength(4)
  expect(legend.querySelectorAll('strong')[3].textContent).toBe('1')
})
