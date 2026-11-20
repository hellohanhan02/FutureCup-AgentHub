<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { request } from '../api'
const bases = ref<any[]>([]),
  selected = ref(''),
  docs = ref<any[]>([]),
  hits = ref<any[]>([]),
  name = ref(''),
  description = ref(''),
  query = ref(''),
  error = ref(''),
  busy = ref(false)
async function run(action: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try {
    await action()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
async function load() {
  bases.value = await request<any[]>('/knowledge-bases')
  if (selected.value)
    docs.value = await request<any[]>('/knowledge-bases/' + selected.value + '/documents')
}
async function save() {
  await run(async () => {
    await request('/knowledge-bases' + (selected.value ? '/' + selected.value : ''), {
      method: selected.value ? 'PUT' : 'POST',
      body: JSON.stringify({ name: name.value, description: description.value }),
    })
    await load()
  })
}
async function remove() {
  if (!confirm('删除该知识库并停止后续检索？')) return
  await run(async () => {
    await request('/knowledge-bases/' + selected.value, { method: 'DELETE' })
    selected.value = ''
    docs.value = []
    await load()
  })
}
async function upload(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 2097152) {
    error.value = '最多2MB'
    return
  }
  await run(async () => {
    const bytes = new Uint8Array(await file.arrayBuffer())
    let binary = ''
    for (const b of bytes) binary += String.fromCharCode(b)
    await request('/knowledge-bases/' + selected.value + '/documents', {
      method: 'POST',
      body: JSON.stringify({ name: file.name, content_base64: btoa(binary) }),
    })
    await load()
  })
}
async function search() {
  await run(async () => {
    hits.value = await request<any[]>('/knowledge-bases/' + selected.value + '/search', {
      method: 'POST',
      body: JSON.stringify({ query: query.value, top_k: 4 }),
    })
  })
}
async function chunks(id: string) {
  await run(async () => {
    hits.value = await request<any[]>(
      '/knowledge-bases/' + selected.value + '/documents/' + id + '/chunks',
    )
  })
}
async function drop(id: string) {
  await run(async () => {
    await request('/knowledge-bases/' + selected.value + '/documents/' + id, { method: 'DELETE' })
    await load()
  })
}
onMounted(() => run(load))
</script>
<template>
  <div class="panel" style="padding: 24px">
    <h1>本地知识库</h1>
    <p>中文字符级关键词检索 / FTS5 BM25，非向量语义搜索。可信本机单用户模式。</p>
    <p v-if="error" class="error-text">{{ error }}</p>
    <select v-model="selected" @change="run(load)">
      <option value="">新建知识库</option>
      <option v-for="b in bases" :key="b.id" :value="b.id">
        {{ b.name }} · v{{ b.version }}
      </option></select
    ><label>名称<input v-model="name" /></label><label>描述<textarea v-model="description" /></label
    ><button :disabled="busy || !name.trim()" @click="save">
      {{ selected ? '保存编辑' : '创建' }}</button
    ><button :disabled="!selected || busy" @click="remove">删除知识库</button>
    <section v-if="selected">
      <h2>导入 TXT / MD / PDF / DOCX</h2>
      <input type="file" accept=".txt,.md,.pdf,.docx" :disabled="busy" @change="upload" />
      <p>每文件2MB；每库30文档；扫描PDF不支持OCR。资料仅保存在本机。</p>
      <article v-for="d in docs" :key="d.id">
        <strong>{{ d.name }}</strong> · {{ d.status }} {{ d.error
        }}<button @click="chunks(d.id)">查看分块来源</button
        ><button @click="drop(d.id)">删除文档</button>
      </article>
      <h2>检索测试</h2>
      <input v-model="query" placeholder="例如 校园身份 订单取消 隐私" /><button
        :disabled="busy || !query.trim()"
        @click="search"
      >
        检索 Top 4
      </button>
      <p v-if="!hits.length">暂无检索结果或尚未检索。</p>
      <article
        v-for="h in hits"
        :key="h.id"
        style="border-top: 1px solid #cadcd3; padding: 12px; overflow-wrap: anywhere"
      >
        <strong>{{ h.document_name ?? h.doc_id }} · {{ h.locator }}</strong
        ><small> {{ h.id }} / BM25 {{ h.score }}</small>
        <p style="white-space: pre-wrap">{{ h.text }}</p>
      </article>
    </section>
  </div>
</template>
