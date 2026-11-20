<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ state: string }>()
const labels: Record<string, string> = {
  succeeded: '执行成功',
  failed: '失败',
  running: '执行中',
  created: '已创建',
  pending: '待执行',
  ready: '已就绪',
  skipped: '已跳过',
  cancelled: '已取消',
  verifying: '验证中',
  recovering: '修复中',
  waiting_human: '待人工处理',
  paused: '已暂停',
  PASSED: '验证通过',
  FAILED: '验证失败',
  ERROR: '验证错误',
  INCONCLUSIVE: '无法判断',
  unverified: '未验证',
  enabled: '已启用',
  disabled: '已禁用',
}
const tone = computed(() =>
  ['succeeded', 'PASSED', 'enabled'].includes(props.state)
    ? 'success'
    : ['failed', 'FAILED', 'ERROR'].includes(props.state)
      ? 'danger'
      : ['running', 'verifying', 'ready', 'recovering'].includes(props.state)
        ? 'info'
        : ['paused', 'waiting_human', 'INCONCLUSIVE'].includes(props.state)
          ? 'warning'
          : 'neutral',
)
</script>
<template>
  <span :class="['badge', tone]"><i></i>{{ labels[state] ?? state }}</span>
</template>
