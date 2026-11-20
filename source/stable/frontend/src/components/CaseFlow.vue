<script setup lang="ts">
import type { DemoInfo } from '../types'
import StatusBadge from './StatusBadge.vue'
import { roleNames } from '../presentation'
defineProps<{ run: DemoInfo['runs'][number] }>()
const verdict = (v: string) =>
  ({ pass: 'PASSED', fail: 'FAILED', error: 'ERROR', inconclusive: 'INCONCLUSIVE' })[v] ??
  'unverified'
</script>
<template>
  <div v-if="run.execution_order.length > 1" class="case-flow" aria-label="历史产物传递示意">
    <template v-for="(name, index) in run.execution_order" :key="name"
      ><span v-if="index" class="flow-arrow">→</span>
      <div class="flow-step">
        <strong>{{ roleNames[name] ?? name }}</strong><small>{{ name }}</small
        ><StatusBadge
          v-if="run.nodes?.find((n) => n.node_id === name)"
          :state="run.nodes.find((n) => n.node_id === name)!.state"
        /></div
    ></template>
  </div>
  <div v-else class="case-flow" aria-label="历史验证与反馈时间线">
    <template v-for="(a, index) in run.nodes?.[0]?.attempts ?? []" :key="a.id">
      <span
        v-if="
          index &&
          run.nodes?.[0]?.feedback_bindings.some((b) => b.attempt_id === a.id && b.confirmed)
        "
        class="flow-feedback"
        >→ Feedback →</span
      >
      <div class="flow-step">
        <small>第 {{ a.ordinal }} 轮 · 程序验证</small
        ><StatusBadge :state="verdict(a.verdict)" /><small
          >{{ a.resolved_model ?? a.model }} · {{ a.input_tokens + a.output_tokens }} Tokens</small
        >
      </div>
    </template>
    <small v-if="!run.nodes?.[0]?.attempts.length">尝试证据待载入，不推断修复结果</small>
  </div>
</template>
