import type { DemoInfo, Verification } from './types'
export type Metric = 'success_rate' | 'mean_latency_ms' | 'p95_latency_ms'
export const scenarioNames: Record<string, string> = {
  A_uniform: 'A 均匀负载',
  B_heterogeneous: 'B 异构能力',
  C_dynamic_fault: 'C 动态故障',
  D_bursty: 'D 并发波动',
}
export const roleNames: Record<string, string> = {
  analyse: '需求分析',
  design: '方案设计',
  review: '方案审查',
  schedule: '约束规划',
}
export function benchmarkOption(
  groups: NonNullable<DemoInfo['benchmark']>['groups'],
  metric: Metric,
) {
  const scenarios = [...new Set(groups.map((g) => g.scenario))]
  return {
    animation: false,
    color: ['#94a3b8', '#5b8eb9', '#07866e'],
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: unknown) =>
        v == null ? '未记录' : String(v) + (metric === 'success_rate' ? '%' : ' 逻辑 ms'),
    },
    legend: { top: 0 },
    grid: { left: 65, right: 20, top: 48, bottom: 45 },
    xAxis: {
      type: 'category',
      data: scenarios.map((s) => scenarioNames[s] ?? s),
      axisLabel: { interval: 0, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: metric === 'success_rate' ? 100 : undefined,
      name: metric === 'success_rate' ? '成功率 %' : '逻辑 ms',
    },
    series: ['fixed', 'round_robin', 'caas'].map((name) => ({
      name,
      type: 'bar',
      barMaxWidth: 36,
      data: scenarios.map((s) => {
        const value = groups.find((g) => g.scenario === s && g.strategy === name)?.metrics[metric]
          ?.mean
        return value == null ? null : +(value * (metric === 'success_rate' ? 100 : 1)).toFixed(3)
      }),
    })),
  }
}
export function violationText(e: Verification['evidence'][number]): string[] {
  if (
    e.status !== 'FAILED' ||
    !e.details ||
    typeof e.details !== 'object' ||
    Array.isArray(e.details)
  )
    return []
  const rows = e.details.violations
  if (!Array.isArray(rows)) return []
  return rows.flatMap((v) => {
    if (!v || typeof v !== 'object' || Array.isArray(v)) return []
    const get = (key: string) => (v[key] == null ? '未记录' : String(v[key]))
    if (e.reason === 'order09c_dependencies_violation')
      return [
        `依赖违反：${get('before')}（第 ${get('before_position')} 位）必须先于 ${get('after')}（第 ${get('after_position')} 位）。`,
      ]
    if (e.reason === 'order09c_windows_violation')
      return [
        `位置窗口违反：${get('task')} 位于第 ${get('actual_position')} 位，允许范围为 ${get('min')}～${get('max')}。`,
      ]
    if (e.reason === 'order09c_completion_violation')
      return [
        `期限违反：${get('task')} 累计完成时间 ${get('actual_completion')}，最迟应为 ${get('latest')}。`,
      ]
    return []
  })
}
