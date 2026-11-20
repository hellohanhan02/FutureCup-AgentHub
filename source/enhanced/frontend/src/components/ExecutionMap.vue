<script setup lang="ts">
import { computed } from 'vue'
import type { Observation } from '../platform'
import { layoutGraph } from '../platform'
import StatusBadge from './StatusBadge.vue'
import { duration, stamp } from '../composables'
const props = defineProps<{ value: Observation; selected: string }>()
defineEmits<{ select: [id: string] }>()
const positions = computed(() => layoutGraph(props.value.graph))
const width = computed(() => Math.max(440, ...positions.value.map(p => p.x + 210)))
const height = computed(() => Math.max(145, ...positions.value.map(p => p.y + 100)))
const nodes = computed(() => Object.fromEntries(props.value.report.nodes.map(n => [n.node_id, n])))
function line(source: string, target: string) {
  const a = positions.value.find(p => p.id === source), b = positions.value.find(p => p.id === target)
  return a && b ? `M ${a.x + 185} ${a.y + 38} C ${a.x + 215} ${a.y + 38}, ${b.x - 25} ${b.y + 38}, ${b.x} ${b.y + 38}` : ''
}
</script>
<template>
  <section class="panel p14d-map">
    <div class="panel-heading"><h2>运行状态与真实依赖</h2><span class="tiny-chip">持久化状态 · 非执行动画</span></div>
    <p class="small muted">箭头来自工作流定义；复用结果与新执行分别标识。点击节点查看真实审计。</p>
    <svg :viewBox="`0 0 ${width} ${height}`" role="img" aria-label="工作流实际依赖状态图">
      <defs><marker id="p14d-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10" fill="#78968d" /></marker></defs>
      <path v-for="(edge, i) in value.graph.edges" :key="i" :d="line(edge.source,edge.target)" fill="none" stroke="#9ab4ab" stroke-width="2" marker-end="url(#p14d-arrow)" />
      <g v-for="p in positions" :key="p.id" :transform="`translate(${p.x},${p.y})`" role="button" tabindex="0" :aria-label="`节点 ${p.id}`" @click="$emit('select',p.id)" @keydown.enter="$emit('select',p.id)">
        <rect width="185" height="78" rx="12" :fill="nodes[p.id]?.reused_from ? '#f0f3f8' : '#f4faf7'" :stroke="selected === p.id ? '#087f67' : '#c5d9d0'" :stroke-width="selected === p.id ? 2.5 : 1" />
        <text x="13" y="24" font-weight="600" fill="#243c36">{{ p.id.slice(0,20) }}</text>
        <text x="13" y="47" font-size="12" fill="#3b6455">{{ nodes[p.id]?.state ?? '未记录' }}</text>
        <text x="13" y="65" font-size="11" fill="#526b61">{{ nodes[p.id]?.reused_from ? '复用已验证结果 · 无新调用' : nodes[p.id]?.execution_id ? '本运行实际执行' : p.kind }}</text>
        <title>{{ p.id }} · {{ nodes[p.id]?.state }} · {{ p.kind }}</title>
      </g>
    </svg>
    <details><summary>节点时间与下游依赖</summary>
      <div class="table-scroll"><table><thead><tr><th>节点</th><th>状态</th><th>等待 / 运行</th><th>开始 / 结束</th><th>直接下游</th></tr></thead><tbody>
        <tr v-for="n in value.report.nodes" :key="n.id"><td><button class="text-link" @click="$emit('select',n.node_id)">{{ n.node_id }}</button></td><td><StatusBadge :state="n.state" /></td><td>{{ duration(n.waiting_ms ?? 0) }} / {{ n.reused_from ? '复用' : duration(n.elapsed_ms ?? 0) }}</td><td>{{ stamp(n.started_at) }}<br>{{ stamp(n.ended_at) }}</td><td>{{ value.graph.edges.filter(e => e.source === n.node_id).map(e => `${e.target}${e.branch ? ' ('+e.branch+')' : ''}`).join('、') || '无' }}</td></tr>
      </tbody></table></div>
    </details>
  </section>
</template>
<style scoped>.p14d-map{margin-bottom:18px;padding:20px}.p14d-map svg{display:block;width:100%;max-height:370px}g[role=button]{cursor:pointer}text{font-family:inherit}</style>
