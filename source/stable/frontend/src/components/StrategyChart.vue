<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { init, use, type ECharts } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
use([PieChart, TooltipComponent, CanvasRenderer])
const props = defineProps<{ values: Record<string, number> }>()
const element = ref<HTMLDivElement>()
let chart: ECharts | undefined, observer: ResizeObserver | undefined
function render() {
  chart?.setOption({
    color: ['#07866e', '#729de0', '#b7c7cb'],
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'pie',
        radius: ['66%', '85%'],
        center: ['50%', '50%'],
        label: { show: false },
        emphasis: { scale: false },
        data: Object.entries(props.values).map(([name, value]) => ({ name, value })),
      },
    ],
  })
}
onMounted(() => {
  if (element.value) {
    chart = init(element.value)
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(element.value)
    render()
  }
})
watch(() => props.values, render, { deep: true })
onUnmounted(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <div
    ref="element"
    class="strategy-chart"
    role="img"
    :aria-label="'调度策略分布：' + JSON.stringify(values)"
  ></div>
</template>
