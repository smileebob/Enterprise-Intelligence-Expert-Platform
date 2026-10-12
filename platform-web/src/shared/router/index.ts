/** 路由（规则 N-04）：全部按域懒加载，meta 声明权限，全局守卫集中鉴权。 */
import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/shared/stores/user'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/modules/auth/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('@/shared/layout/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/chat' },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/modules/chat/views/ChatView.vue'),
        meta: { title: '领域智能体对话台', perm: 'chat.use' },
      },
      {
        path: 'knowledge',
        name: 'Knowledge',
        component: () => import('@/modules/knowledge/views/KnowledgeView.vue'),
        meta: { title: '知识管理', perm: 'knowledge.write' },
      },
      {
        path: 'evaluation',
        name: 'Evaluation',
        component: () => import('@/modules/evaluation/views/EvaluationView.vue'),
        meta: { title: '评估中心', perm: 'eval.run' },
      },
      {
        path: 'memory',
        name: 'Memory',
        component: () => import('@/modules/memory/views/MemoryView.vue'),
        meta: { title: '我的记忆', perm: 'memory.mine.read' },
      },
      {
        path: 'assembly',
        name: 'Assembly',
        component: () => import('@/modules/admin/views/AssemblyView.vue'),
        meta: { title: '领域装配与权限', perm: 'domain.view' },
      },
      {
        path: 'audit',
        name: 'Audit',
        component: () => import('@/modules/admin/views/AuditView.vue'),
        meta: { title: '安全审计台', perm: 'audit.read' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const userStore = useUserStore()
  document.title = `${String(to.meta.title || '')} · 企业级领域专家 Agent 平台`
  if (to.meta.public) {
    return true
  }
  if (to.meta.requiresAuth && !userStore.isLoggedIn) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (!userStore.me && userStore.isLoggedIn) {
    try {
      await userStore.fetchMe()
    } catch {
      userStore.logout()
      return { name: 'Login' }
    }
  }
  const perm = to.meta.perm as string | undefined
  if (perm && !userStore.hasPerm(perm)) {
    return { name: 'Chat' }
  }
  return true
})

export default router
