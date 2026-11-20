<script setup lang="ts">
import { computed } from 'vue'
import type { RunReport } from '../types'
import StatusBadge from './StatusBadge.vue'
import JsonPanel from './JsonPanel.vue'
import { roleNames } from '../presentation'
const props = defineProps<{ report: RunReport }>()
defineEmits<{ select: [id: string] }>()
const ordered = computed(() =>
  [...props.report.nodes].sort((a, b) => (a.started_at ?? '').localeCompare(b.started_at ?? '')),
)
</script>
<template>
  <section v-if="ordered.length > 1" class="panel evidence-overview">
    <h3>协作节点与实际产物传递</h3>
    <p class="small muted">
      按记录开始时间排列；历史示意，不是实时执行动画。箭头映射以节点实际输入为准。
    </p>
    <div class="detail-flow">
      <article v-for="(n, i) in ordered" :key="n.id" class="flow-step">
        <small>{{ i + 1 }} · {{ n.node_id }}</small
        ><button class="text-link" @click="$emit('select', n.node_id)">
          {{ roleNames[n.node_id] ?? n.node_id }} →</button
        ><StatusBadge :state="n.state" /><small>{{
          n.resolved_model ?? n.model ?? '模型未记录'
        }}</small
        ><small
          >调用 {{ n.model_calls?.length ?? '未记录' }} · Tokens
          {{
            n.input_tokens != null && n.output_tokens != null
              ? n.input_tokens + n.output_tokens
              : '未记录'
          }}</small
        >
        <details>
          <summary>实际输入与产物映射</summary>
          <JsonPanel
            :value="{
              input: n.input,
              output: n.output,
              agent_ref: n.agent_ref,
              execution_id: n.execution_id,
            }"
          />
        </details>
      </article>
    </div>
  </section>
</template>
