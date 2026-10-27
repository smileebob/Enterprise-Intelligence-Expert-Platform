<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/shared/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const menuItems = computed(() => {
  const items = [
    { path: '/chat', label: '领域智能体对话台', perm: 'chat.use' },
    { path: '/gov', label: '政务办公工作台', perm: 'chat.use' },
    { path: '/evaluation', label: '评估中心', perm: 'eval.run' },
    { path: '/knowledge', label: '知识管理', perm: 'knowledge.write' },
    { path: '/memory', label: '我的记忆', perm: 'memory.mine.read' },
    { path: '/assembly', label: '领域装配与权限', perm: 'domain.view' },
    { path: '/audit', label: '安全审计台', perm: 'audit.read' },
  ]
  return items.filter((item) => userStore.hasPerm(item.perm) || item.path === '/chat')
})

function logout() {
  userStore.logout()
  router.push({ name: 'Login' })
}
</script>

<template>
  <div class="min-h-screen flex flex-col bg-gray-50">
    <header class="h-14 px-4 flex items-center justify-between bg-white border-b border-gray-200 z-10">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded bg-blue-600 text-white flex items-center justify-center font-700">智</div>
        <div>
          <div class="text-sm font-600 leading-4">企业级领域专家 Agent 平台</div>
          <div class="text-[11px] text-gray-400 leading-4">通用底座 + 可插拔领域插件 · 开源参考实现（模拟数据）</div>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <span class="chip bg-blue-50 text-blue-600">{{ userStore.me?.role_id || '' }}</span>
        <span class="text-sm text-gray-600">{{ userStore.me?.name || userStore.me?.username }}</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </div>
    </header>

    <div class="flex-1 flex min-h-0">
      <aside class="w-56 bg-white border-r border-gray-200 py-3 flex flex-col gap-1">
        <router-link
          v-for="item in menuItems"
          :key="item.path"
          :to="item.path"
          class="px-4 py-2 text-sm rounded mx-2 transition-colors"
          :class="route.path.startsWith(item.path) ? 'bg-blue-50 text-blue-600 font-500' : 'text-gray-600 hover:bg-gray-50'"
        >
          {{ item.label }}
        </router-link>
      </aside>
      <main class="flex-1 min-w-0 overflow-hidden">
        <router-view />
      </main>
    </div>
  </div>
</template>
