<script setup lang="ts">
import { Inbox } from '@lucide/vue'
defineProps<{ title?: string; description?: string }>()
</script>
<template>
  <div class="empty-state">
    <span class="empty-icon"><Inbox :size="24" /></span>
    <h3>{{ title ?? '暂无数据' }}</h3>
    <p>{{ description ?? '数据将来自后端的真实执行记录。' }}</p>
    <slot />
  </div>
</template>
