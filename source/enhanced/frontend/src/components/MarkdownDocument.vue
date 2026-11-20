<script setup lang="ts">
// Deliberately limited Markdown: headings, lists, tables and fenced code.
// Render model text as Vue text nodes only; HTML, links and images never execute.
import { computed } from 'vue'
const props = defineProps<{ source: string }>()
type Block = { kind: string; text: string; rows?: string[][]; level?: number }
const blocks = computed(() => {
  const lines = props.source.split('\n'),
    result: Block[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.startsWith('```')) {
      const content: string[] = []
      while (++i < lines.length && !lines[i].startsWith('```')) content.push(lines[i])
      result.push({ kind: 'code', text: content.join('\n') })
      continue
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      result.push({ kind: 'heading', text: heading[2], level: heading[1].length })
      continue
    }
    if (
      line.trim().startsWith('|') &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:|-]+\|\s*$/.test(lines[i + 1])
    ) {
      const split = (s: string) =>
        s
          .trim()
          .replace(/^\||\|$/g, '')
          .split('|')
          .map((x) => x.trim())
      const rows = [split(line)]
      i++
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith('|'))
        rows.push(split(lines[++i]))
      result.push({ kind: 'table', text: '', rows })
      continue
    }
    if (line.trim())
      result.push({
        kind: /^\s*(?:[-*+] |\d+\. )/.test(line) ? 'item' : 'paragraph',
        text: line.replace(/^\s*(?:[-*+] |\d+\. )/, ''),
      })
  }
  return result
})
</script>
<template>
  <article class="markdown-document" aria-label="系统设计 Markdown 文档">
    <template v-for="(b, i) in blocks" :key="i"
      ><component :is="'h' + b.level" v-if="b.kind === 'heading'">{{ b.text }}</component>
      <pre v-else-if="b.kind === 'code'"><code>{{ b.text }}</code></pre>
      <div v-else-if="b.kind === 'table'" class="markdown-table">
        <table>
          <thead>
            <tr>
              <th v-for="(cell, c) in b.rows![0]" :key="c">{{ cell }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, r) in b.rows!.slice(1)" :key="r">
              <td v-for="(cell, c) in row" :key="c">{{ cell }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else :class="{ 'markdown-item': b.kind === 'item' }">
        {{ b.kind === 'item' ? '• ' : '' }}{{ b.text }}
      </p></template
    >
  </article>
</template>
<style scoped>
.markdown-document {
  line-height: 1.85;
  overflow-wrap: anywhere;
}
.markdown-document h2 {
  margin-top: 28px;
  border-bottom: 1px solid #d6e5df;
  padding-bottom: 8px;
}
.markdown-document p {
  white-space: pre-wrap;
}
.markdown-document pre {
  white-space: pre-wrap;
  background: #f2f7f5;
  padding: 14px;
  border-radius: 8px;
}
.markdown-table {
  max-width: 100%;
  overflow: auto;
}
.markdown-document table {
  width: 100%;
  border-collapse: collapse;
}
.markdown-document th,
.markdown-document td {
  border: 1px solid #d6e5df;
  padding: 8px;
  text-align: left;
}
.markdown-item {
  padding-left: 12px;
}
</style>
