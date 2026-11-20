<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { Bot, GitBranch, Repeat2, Layers, Merge, ShieldCheck } from '@lucide/vue'
import type { WorkflowNode } from '../types'
import { kindLabels } from '../composables'
const props = defineProps<{ data: { spec: WorkflowNode }; selected?: boolean }>()
const icons = {
  task: Bot,
  condition: GitBranch,
  loop: Repeat2,
  subworkflow: Layers,
  parallel: Merge,
}
</script>
<template>
  <div :class="['harness-node', data.spec.kind, { selected }]">
    <Handle type="target" :position="Position.Left" />
    <div class="harness-node-header">
      <span class="node-icon"><component :is="icons[data.spec.kind]" :size="18" /></span
      ><span>{{ kindLabels[data.spec.kind] }}</span
      ><ShieldCheck v-if="data.spec.config.verification" :size="14" class="positive" />
    </div>
    <strong>{{ data.spec.id }}</strong><small>{{
      data.spec.config.agent_id ||
      (data.spec.config.candidate_agents
        ? '动态调度候选 Agent'
        : data.spec.kind === 'condition'
          ? '结构化条件判断'
          : data.spec.kind === 'loop'
            ? '有界子工作流迭代'
            : data.spec.kind === 'parallel'
              ? '依赖汇合屏障'
              : '引用已保存工作流')
    }}</small
    ><Handle type="source" :position="Position.Right" />
  </div>
</template>
