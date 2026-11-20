<script setup lang="ts">
import type { RunSummary } from '../types'
import { ArrowUpRight } from '@lucide/vue'
import StatusBadge from './StatusBadge.vue'
import EmptyState from './EmptyState.vue'
import { duration, stamp, elapsed } from '../composables'
defineProps<{ runs: RunSummary[] }>()
</script>
<template>
  <EmptyState
    v-if="!runs.length"
    title="还没有执行记录"
    description="在工作流编排器中创建并执行一个流程，运行状态会显示在这里。"
  />
  <div v-else class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>工作流 / 执行 ID</th>
          <th>执行状态</th>
          <th>结果可信度</th>
          <th>耗时</th>
          <th>执行时间</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.id">
          <td>
            <RouterLink class="table-title" :to="'/runs/' + run.id" :title="run.workflow.id"
              >{{
                run.workflow.id.startsWith('ds09a-')
                  ? '多 Agent 协作案例'
                  : run.workflow.id.startsWith('ds09c-')
                    ? '验证与反馈案例'
                    : run.workflow.id
              }}
              <span class="version">v{{ run.workflow.version }}</span></RouterLink
            ><small class="mono muted" :title="run.id"
              >{{ run.id.slice(0, 12) }}
              <span v-if="run.simulated" class="mock-tag">MOCK</span></small
            >
            <small v-if="run.simulated === false" class="positive">真实模型记录</small>
          </td>
          <td><StatusBadge :state="run.state" /></td>
          <td><StatusBadge :state="run.verified ? 'PASSED' : 'unverified'" /></td>
          <td class="mono">{{ duration(elapsed(run.state, run.started_at, run.elapsed_ms)) }}</td>
          <td class="muted">{{ stamp(run.started_at) }}</td>
          <td>
            <RouterLink :to="'/runs/' + run.id" class="icon-button" aria-label="查看执行详情"
              ><ArrowUpRight :size="17"
            /></RouterLink>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
