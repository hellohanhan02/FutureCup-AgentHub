<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NConfigProvider, NMessageProvider, NDialogProvider, zhCN, dateZhCN } from 'naive-ui'
import {
  Network,
  LayoutDashboard,
  Bot,
  GitBranch,
  Activity,
  RefreshCw,
  ChevronRight,
  ShieldCheck,
  ExternalLink,
  Cpu,
} from '@lucide/vue'
import { api } from '../api'
import { refresh, usePolling } from '../composables'
const route = useRoute(),
  online = ref<boolean | null>(null)
const replay = ref(false)
const router = useRouter()
onMounted(async () => {
  try {
    replay.value = (await api.demoInfo()).mode === 'historical_read_only'
    if (replay.value && ['/', '/workspace'].includes(route.path)) await router.replace('/demo')
  } catch {
    /* Normal service has no replay package. */
  }
})
const theme = {
  common: {
    primaryColor: '#087f67',
    primaryColorHover: '#096b58',
    primaryColorPressed: '#065344',
    borderRadius: '8px',
    fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", sans-serif',
  },
}
const allNavigation = [
  { path: '/knowledge', label: '知识库', icon: Bot },
  { path: '/demo', label: '比赛演示与证据', icon: ShieldCheck },
  { path: '/workspace', label: '智能协作工作台', icon: Bot },
  { path: '/professional', label: '专业工作台总览', icon: LayoutDashboard },
  { path: '/agents', label: 'Agent 管理', icon: Bot },
  { path: '/models', label: '模型与 Provider', icon: Cpu },
  { path: '/workflows', label: '工作流编排', icon: GitBranch },
  { path: '/runs', label: '运行监控', icon: Activity },
]
const navigation = computed(() =>
  replay.value
    ? allNavigation.filter((n) => ['/demo', '/runs'].includes(n.path))
    : route.path.startsWith('/workspace')
      ? allNavigation.filter((n) =>
          ['/workspace', '/professional', '/demo', '/knowledge'].includes(n.path),
        )
      : allNavigation,
)
const poll = usePolling(async () => {
  try {
    online.value = (await api.health()).status === 'ok'
  } catch {
    online.value = false
  }
}, 30000)
onMounted(poll.start)
</script>
<template>
  <NConfigProvider :theme-overrides="theme" :locale="zhCN" :date-locale="dateZhCN"
    ><NMessageProvider
      ><NDialogProvider>
        <div class="app-shell">
          <aside class="sidebar">
            <RouterLink to="/" class="brand"
              ><span class="brand-mark"><Network :size="23" /></span>
              <div>
                <strong>FutureCup<span> / Harness</span></strong
                ><small>多智能体协作控制台</small>
              </div></RouterLink
            >
            <div class="workspace-label">
              <span class="workspace-avatar">F</span>
              <div><strong>FutureCup 工作空间</strong><small>本地开发环境</small></div>
              <span class="workspace-dot"></span>
            </div>
            <p class="nav-heading">工作空间 <span>WORKSPACE</span></p>
            <nav>
              <RouterLink
                v-for="item in navigation"
                :key="item.path"
                :to="item.path"
                :class="{
                  active: item.path === '/' ? route.path === '/' : route.path.startsWith(item.path),
                }"
                ><component :is="item.icon" :size="19" /><span>{{ item.label }}</span
                ><ChevronRight v-if="route.path === item.path" :size="15" class="nav-chevron"
              /></RouterLink>
            </nav>
            <div class="sidebar-note">
              <ShieldCheck :size="20" /><strong>可解释 · 可验证 · 可追溯</strong>
              <p>让每次 Agent 协作<br />都有依据、有边界。</p>
              <span class="tiny-chip">AGENT HARNESS</span>
            </div>
            <div class="sidebar-bottom">
              <span class="local-dot"></span>
              <div><strong>本地运行模式</strong><small>不自动连接付费模型</small></div>
              <span class="version">v0.1</span>
            </div>
          </aside>
          <div class="main-shell">
            <header class="topbar">
              <div class="breadcrumb">
                工作空间 <ChevronRight :size="13" /><span>{{ route.meta.title }}</span>
              </div>
              <div class="top-actions">
                <span :class="['connection', online === false ? 'offline' : '']"
                  ><i></i
                  >{{ online === null ? '检查服务中' : online ? 'API 已连接' : 'API 未连接' }}</span
                ><span class="divider"></span
                ><a
                  href="http://127.0.0.1:8000/docs"
                  target="_blank"
                  rel="noopener"
                  title="打开本地 API 文档"
                  >API 文档 <ExternalLink :size="13" /></a
                ><span class="user-avatar">FC</span>
              </div>
            </header>
            <main class="main-content">
              <div v-if="online === false" class="error-banner" role="alert">
                后端未连接。请启动 127.0.0.1:8000 服务；页面不会生成虚假的运行数据。<button
                  @click="refresh"
                >
                  重试页面数据
                </button>
              </div>
              <div class="page-heading">
                <div>
                  <p class="eyebrow">FUTURECUP · AGENT HARNESS</p>
                  <h1>{{ route.meta.title }}</h1>
                  <p>{{ route.meta.subtitle }}</p>
                </div>
                <button class="button secondary" @click="refresh">
                  <RefreshCw :size="15" />刷新数据
                </button>
              </div>
              <RouterView />
              <footer class="page-footer">
                <span>FutureCup Agent Harness</span
                ><span>执行成功 ≠ 验证通过 <span class="footer-dot">·</span> 数据来自本地 API</span>
              </footer>
            </main>
          </div>
        </div>
      </NDialogProvider></NMessageProvider
    ></NConfigProvider
  >
</template>
