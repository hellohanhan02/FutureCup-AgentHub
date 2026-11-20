<script setup lang="ts">
import { ref } from 'vue'
import { Copy, Check } from '@lucide/vue'
import { pretty } from '../composables'
const props = defineProps<{ value: unknown; title?: string }>()
const copied = ref(false),
  error = ref('')
async function copy() {
  try {
    await navigator.clipboard.writeText(pretty(props.value))
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    error.value = '复制失败，请手动选择内容。'
  }
}
</script>
<template>
  <section class="json-panel">
    <header>
      <span>{{ title ?? '结构化数据' }}</span><button
        class="icon-button"
        aria-label="复制 JSON"
        @click="copy"
      >
        <Check v-if="copied" :size="14" /><Copy v-else :size="14" />
      </button>
    </header>
    <pre>{{ pretty(value) }}</pre>
    <small v-if="error" class="error-text">{{ error }}</small>
  </section>
</template>
