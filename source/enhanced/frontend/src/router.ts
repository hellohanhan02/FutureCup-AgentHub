import { createRouter, createWebHashHistory } from 'vue-router'
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/knowledge',
      component: () => import('./pages/Knowledge.vue'),
      meta: { title: '知识库', subtitle: '本地资料、检索与来源审计' },
    },
    {
      path: '/workspace/:id?',
      component: () => import('./pages/Workspace.vue'),
      meta: { title: '智能协作工作台', subtitle: '说出需求，审查计划，交付可信成果。' },
    },
    {
      path: '/demo',
      component: () => import('./pages/Demo.vue'),
      meta: { title: '比赛演示与证据', subtitle: '只读回放真实历史，分开呈现模拟实验。' },
    },
    {
      path: '/models',
      component: () => import('./pages/Models.vue'),
      meta: {
        title: '模型与 Provider',
        subtitle: '配置可替换的推理核心，明确每一次调用的权限与边界。',
      },
    },
    {
      path: '/professional',
      component: () => import('./pages/Dashboard.vue'),
      meta: { title: '平台总览', subtitle: '观察协作全局，追踪每一次决策。' },
    },
    {
      path: '/agents',
      component: () => import('./pages/Agents.vue'),
      meta: { title: 'Agent 管理中心', subtitle: '为不同角色配置能力、模型与执行边界。' },
    },
    {
      path: '/workflows',
      component: () => import('./pages/Workflows.vue'),
      meta: { title: '工作流编排', subtitle: '把复杂任务，连接成可执行的协作流程。' },
    },
    {
      path: '/runs',
      component: () => import('./pages/Runs.vue'),
      meta: { title: '运行监控', subtitle: '跟踪任务状态、资源分配与执行异常。' },
    },
    {
      path: '/runs/:id',
      component: () => import('./pages/RunDetail.vue'),
      meta: { title: '执行详情', subtitle: '从任务输入到验证结果，查看完整因果链。' },
    },
    { path: '/', redirect: '/workspace' },
    { path: '/:pathMatch(.*)*', redirect: '/workspace' },
  ],
})
