<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { init, use, type ECharts } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { benchmarkOption, type Metric } from '../presentation'
import type { DemoInfo } from '../types'
use([BarChart, GridComponent, TooltipComponent, LegendComponent, SVGRenderer])
const props = defineProps<{
  groups: NonNullable<DemoInfo['benchmark']>['groups']
  metric: Metric
}>()
const element = ref<HTMLDivElement>()
let chart: ECharts | undefined, observer: ResizeObserver | undefined
function render() {
  chart?.setOption(benchmarkOption(props.groups, props.metric), true)
}
onMounted(() => {
  if (!element.value || !element.value.clientWidth) return
  chart = init(element.value, undefined, { renderer: 'svg' })
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(element.value)
})
watch(() => [props.groups, props.metric], render, { deep: true })
onUnmounted(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <div
    ref="element"
    class="benchmark-chart"
    role="img"
    :aria-label="'四场景三策略 · ' + metric + ' · 离线模拟；数据详见原始表格'"
  />
</template>
<style scoped>
.benchmark-chart {
  width: 100%;
  height: 330px;
  min-width: 0;
}
</style>
