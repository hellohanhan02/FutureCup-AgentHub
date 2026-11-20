<script setup lang="ts">
import { computed } from 'vue'
import JsonPanel from './JsonPanel.vue'
const props = defineProps<{ records: unknown[]; artifact?: unknown; evidence?: unknown[] }>()
function rows(value: unknown): Record<string, any>[] { return Array.isArray(value) ? value.filter(v => v && typeof v === 'object' && !Array.isArray(v)) : [] }
const records = computed(() => rows(props.records))
const citations = computed(() => rows((props.artifact as Record<string,unknown> | undefined)?.citations))
const checks = computed(() => rows(props.evidence).flatMap(e => rows(e.details?.coverage)))
const labels: Record<string,string> = {COVERED:'发现明确相关规则',PARTIAL:'仅有部分相关依据',UNCOVERED:'未发现相关片段',RULE_REFLECTED:'已引用并体现关键规则',SOURCE_NOT_REFLECTED:'来源存在但成果遗漏规则',SOURCE_NOT_CITED:'缺少本轮实际来源引用',POSSIBLE_CONFLICT:'部分数值可能与来源冲突',SEMANTIC_UNCERTAIN:'无法确认正文与完整规则一致',INSUFFICIENT_EVIDENCE:'依据不足',SOURCE_UNAVAILABLE_ADVICE:'来源不足，已标注设计建议'}
const kinds: Record<string,string> = {RETRIEVAL_COVERAGE_INSUFFICIENT:'检索覆盖不足',SOURCE_CITATION_INVALID:'引用来源或必需引文无效',RULE_NOT_REFLECTED:'必需规则未在正文体现',RULE_CONFLICT:'正文规则冲突',NUMERIC_UNIT_CONFLICT:'数字、单位或比较关系冲突',SEMANTIC_UNCERTAIN:'无法确认正文与完整规则一致'}
function source(id: unknown) {
  const element = document.getElementById('knowledge-'+String(id)) as HTMLDetailsElement | null
  if (element) { element.open = true; element.scrollIntoView?.({ block:'nearest' }) }
}
</script>
<template>
  <section>
    <h3>知识检索与关键规则覆盖</h3>
    <p class="warning-note">检索主题覆盖、来源真实性、成果规则体现是不同检查，均不等于全部业务结论正确。自动主题为词法拆分，或来自调用方配置。</p>
    <p v-if="!records.length" class="muted">本轮未保存检索记录；不推测知识库不存在规则。</p>
    <article v-for="(r,index) in records" :key="index" class="p14d-rag-record">
      <p>检索 {{ r.retrieval_calls ?? '未记录' }} 次 · 注入 {{ r.context_chars ?? '未记录' }} 字符 · 查询版本 {{ r.query_version ?? '未记录' }}</p>
      <div class="table-scroll"><table><thead><tr><th>主题</th><th>检索覆盖</th><th>查询状态</th><th>要求来源</th></tr></thead><tbody>
        <tr v-for="t in rows(r.coverage)" :key="t.id"><td>{{ t.query }}<small class="block muted">{{ t.id }}</small></td><td><strong :class="'coverage-'+t.status">{{ t.status }}</strong><small class="block">{{ labels[t.status] ?? '未记录' }}</small></td><td>{{ t.search_state }}</td><td>{{ t.requirement_origin }} · {{ t.required ? '必需主题' : '可选主题' }}</td></tr>
      </tbody></table></div>
      <details><summary>初次检索 / 受控补检过程</summary><div v-for="(q,i) in rows(r.queries)" :key="i" class="p14d-query"><strong>{{ q.phase === 'supplement' ? '补检' : '初次' }}</strong> {{ q.query }} · 实际命中 {{ Array.isArray(q.hit_ids) ? q.hit_ids.length : '未记录' }}<JsonPanel :value="q" /></div></details>
      <p v-if="!rows(r.hits).length">检索没有命中；知识增强未完成，不能解释为没有业务规定。</p>
      <details v-for="h in rows(r.hits)" :id="'knowledge-'+h.id" :key="h.id"><summary>{{ h.document_name ?? h.doc_id }} · {{ h.locator ?? '位置未记录' }} · {{ String(h.id).slice(0,12) }}</summary><pre class="p14d-source">{{ h.text }}</pre><JsonPanel :value="{ chunk_id:h.id, kb_id:h.kb_id, document_version:h.document_version, document_sha256:h.document_sha256, score:h.score }" /></details>
    </article>
    <h4>模型实际引用</h4><p v-if="!citations.length">本轮产物未提供引用。</p>
    <button v-for="(c,i) in citations" :key="i" class="p14d-citation" @click="source(c.chunk_id)"><strong>{{ String(c.chunk_id).slice(0,16) }}</strong><span>{{ c.quote }}</span><small>查看实际检索片段 ↑</small></button>
    <h4>独立验证器的规则体现 / 冲突证据</h4><p v-if="!checks.length">未记录该项验证证据；不推测通过。</p>
    <article v-for="(c,i) in checks" :key="i" class="info-note"><strong>{{ kinds[c.validation_type] ?? labels[c.status] ?? c.status }}</strong><JsonPanel :value="c" /></article>
  </section>
</template>
<style scoped>.p14d-rag-record{border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}.p14d-source{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;background:#f2f7f4}.p14d-citation{display:grid;gap:5px;text-align:left;width:100%;padding:12px;border:1px solid #cbded4;border-radius:9px;margin:8px 0;background:#f9fcfa}.p14d-query{padding:10px}.coverage-COVERED{color:#087f67}.coverage-PARTIAL{color:#956311}.coverage-UNCOVERED{color:#b34545}.block{display:block}</style>
