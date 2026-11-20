<script setup lang="ts">
import type { Decision } from '../types'
import JsonPanel from './JsonPanel.vue'
defineProps<{ decisions: Decision[] }>()
const factors: Record<string,string> = {match:'能力',reliability:'可靠性',load:'可用容量',cost:'成本',latency:'预计延迟'}
function value(v: unknown) { return typeof v === 'number' ? v.toFixed(3) : v == null ? '未记录' : String(v) }
</script>
<template>
  <div>
    <p class="info-note">仅展示决策当时保存的数据。可靠性来自执行统计，不是业务正确率；费用是声明估计。</p>
    <p v-if="!decisions.length" class="muted">没有调度解释记录。</p>
    <article v-for="d in decisions" :key="d.id" class="decision-card">
      <header><strong>{{ d.strategy }}</strong><span>最终选择：{{ d.selected?.id ?? '无' }}</span><span>{{ d.outcome }}</span></header>
      <details><summary>实际任务能力需求</summary><JsonPanel v-if="d.requirements" :value="d.requirements" /><p v-else>旧记录未保存需求快照。</p></details>
      <section v-for="c in d.candidates" :key="c.agent_id" class="p14d-candidate">
        <h4>{{ c.agent_id }} <small>{{ c.eligible ? '符合约束' : '已排除' }}</small></h4>
        <p>排除原因：{{ c.reasons.length ? c.reasons.join('、') : c.eligible ? '无' : '未记录' }} · 综合评分 {{ value(c.score) }}</p>
        <div class="p14d-factor-grid"><div v-for="(label,key) in factors" :key="key"><span>{{ label }}</span><strong>{{ value(c.components[key]) }}</strong><small>权重 {{ value(c.adaptation?.weights?.[key]) }}</small></div></div>
        <p>负载 {{ c.profile?.capacity ? `${c.profile.capacity.active} / ${c.profile.capacity.capacity}` : '未记录' }} · 可用状态 {{ c.profile?.available == null ? '未记录' : c.profile.available ? '可用' : '不可用' }}</p>
        <p>近期有效样本 {{ value(c.adaptation?.effective_samples) }} · 冷却剩余 {{ value(c.adaptation?.cooldown_remaining_seconds) }} 秒</p>
        <p>选择依据：{{ c.adaptation?.reason ?? '未记录自适应解释；按该策略原始决策查看' }}</p>
        <details><summary>完整评分、画像版本与故障分类</summary><JsonPanel :value="c" /></details>
      </section>
    </article>
  </div>
</template>
<style scoped>.p14d-candidate{padding:16px;margin:12px 0;border:1px solid var(--line);border-radius:12px}.p14d-factor-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px}.p14d-factor-grid>div{display:grid;background:#f3f8f5;padding:10px;border-radius:8px}.p14d-factor-grid strong{font-size:18px}.p14d-factor-grid small{color:#526b61}</style>
