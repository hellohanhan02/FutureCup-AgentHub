import { ref, onUnmounted } from 'vue'
export const refreshVersion = ref(0)
export const refresh = () => refreshVersion.value++
export function usePolling(action: () => Promise<void>, milliseconds = 3000) {
  let stopped = false, started = false
  const lastError = ref<unknown>()
  let timer: ReturnType<typeof setTimeout>
  async function tick() {
    try {
      await action()
      lastError.value = undefined
    } catch (error) {
      lastError.value = error
    } finally {
      if (!stopped) timer = setTimeout(tick, milliseconds)
    }
  }
  onUnmounted(() => {
    stopped = true
    clearTimeout(timer)
  })
  return {
    start: async () => { if (started || stopped) return; started = true; await tick() },
    lastError,
    stop: () => {
      stopped = true
      clearTimeout(timer)
    },
  }
}
export const pretty = (value: unknown) => JSON.stringify(value, null, 2) ?? 'null'
export const stamp = (value: string | null) =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
export const duration = (value: number) =>
  value < 1000 ? `${value.toFixed(0)} ms` : `${(value / 1000).toFixed(2)} s`
export const terminal = (state: string) => ['succeeded', 'failed', 'cancelled'].includes(state)
export const kindLabels = {
  task: 'Agent 任务',
  parallel: '并行汇合',
  condition: '条件分支',
  loop: '受限循环',
  subworkflow: '子工作流',
}

export const elapsed = (state: string, startedAt: string | null, milliseconds: number) =>
  terminal(state) || state === 'skipped'
    ? milliseconds
    : startedAt
      ? Math.max(0, Date.now() - Date.parse(startedAt))
      : 0
