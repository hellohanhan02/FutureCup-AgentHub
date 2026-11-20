// Offline SVG rendering through the project's existing ECharts dependency.
// No browser, external assets, model service or additional plotting install.
import * as echarts from '../frontend/node_modules/echarts/index.js'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const dir = resolve(process.argv[2])
if (relative(root, dir).startsWith('..')) throw new Error('Output outside project')
const summary = JSON.parse(readFileSync(join(dir, 'summary.json'), 'utf8'))
const raw = JSON.parse(readFileSync(join(dir, 'raw-trials.json'), 'utf8'))
const scenarios = ['A_uniform', 'B_heterogeneous', 'C_dynamic_fault', 'D_bursty']
const policies = ['fixed', 'round_robin', 'caas']
const colors = ['#3563ad', '#bf781d', '#167b65']

function figure(metric, title, filename, scale = 1) {
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 1100, height: 760 })
  const grids = scenarios.map((_, i) => ({ left: i % 2 ? '57%' : '8%', top: i < 2 ? '16%' : '59%', width: '36%', height: '27%' }))
  const xAxis = scenarios.map((_, i) => ({ gridIndex: i, type: 'category', data: policies, axisLabel: { fontSize: 13 } }))
  const yAxis = scenarios.map((_, i) => ({ gridIndex: i, type: 'value', min: 0, max: metric === 'success_rate' ? 100 : undefined, name: metric === 'success_rate' ? '%' : 'logical ms', splitLine: { lineStyle: { color: '#e3e9ee' } } }))
  const series = []
  scenarios.forEach((scenario, gridIndex) => {
    const groups = policies.map(strategy => summary.groups.find(g => g.scenario === scenario && g.strategy === strategy))
    series.push({ type: 'bar', xAxisIndex: gridIndex, yAxisIndex: gridIndex, barWidth: '38%',
      data: groups.map((g, i) => ({ value: g.metrics[metric].mean * scale, itemStyle: { color: colors[i] } })) })
    series.push({ type: 'custom', xAxisIndex: gridIndex, yAxisIndex: gridIndex, z: 5,
      data: groups.map((g, i) => [i, ...g.metrics[metric].ci95.map(v => v * scale)]),
      renderItem(params, api) {
        const low = api.coord([api.value(0), api.value(1)])
        const high = api.coord([api.value(0), api.value(2)])
        const style = { stroke: '#172c40', lineWidth: 1.8 }
        return { type: 'group', children: [
          { type: 'line', shape: { x1: low[0], y1: low[1], x2: high[0], y2: high[1] }, style },
          ...[low, high].map(p => ({ type: 'line', shape: { x1: p[0] - 6, y1: p[1], x2: p[0] + 6, y2: p[1] }, style })),
        ] }
      } })
    // Retain individual seed points so the figure does not hide dispersion.
    series.push({ type: 'scatter', xAxisIndex: gridIndex, yAxisIndex: gridIndex, z: 6, symbolSize: 4,
      itemStyle: { color: '#182d44', opacity: .45 },
      data: policies.flatMap((policy, p) => raw.trials.filter(t => t.scenario === scenario && t.strategy === policy).map(t => [p, t.metrics[metric] * scale])) })
  })
  chart.setOption({ animation: false, backgroundColor: '#fff',
    title: [{ text: title, subtext: 'SYNTHETIC SIMULATION | means and bootstrap 95% CIs across seeds; dots = individual seeds', left: 'center', top: 12 },
      ...scenarios.map((text, i) => ({ text, left: i % 2 ? '57%' : '8%', top: i < 2 ? '10%' : '53%', textStyle: { fontSize: 16 } }))],
    grid: grids, xAxis, yAxis, series,
    graphic: [{ type: 'text', left: 60, bottom: 14, style: { text: 'All failures included. Logical task time excludes host overhead. These are not real LLM or production measurements.', fill: '#536477', fontSize: 12 } }] })
  writeFileSync(join(dir, filename), chart.renderToSVGString())
  chart.dispose()
}
figure('success_rate', 'Task success rate by workload and production policy', 'success-rate.svg', 100)
figure('mean_latency_ms', 'Arrival-to-terminal mean latency', 'mean-latency.svg')
figure('p95_latency_ms', 'Mean of per-seed P95 terminal latencies (nearest rank)', 'p95-latency.svg')
console.log('Rendered 3 offline ECharts SVG figures')
