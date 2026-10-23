<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { useUserStore } from '@/shared/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const loading = ref(false)
const form = reactive({ username: 'expert', password: 'Expert@12345' })

const demoAccounts = [
  { label: '领域专家（化学工程师）', username: 'expert', password: 'Expert@12345' },
  { label: '知识管理员', username: 'kbadmin', password: 'Kb@12345' },
  { label: '平台管理员', username: 'admin', password: 'Admin@12345' },
  { label: '安全审计员', username: 'auditor', password: 'Audit@12345' },
]

async function submit() {
  loading.value = true
  try {
    const user = await userStore.login(form.username, form.password)
    ElMessage.success(`欢迎，${user.name || user.username}`)
    router.push(String(route.query.redirect || '/chat'))
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function pick(account: (typeof demoAccounts)[number]) {
  form.username = account.username
  form.password = account.password
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-white to-blue-100">
    <div class="w-[420px] panel p-8">
      <div class="text-center mb-6">
        <div class="w-12 h-12 mx-auto rounded-lg bg-blue-600 text-white flex items-center justify-center text-xl font-700 mb-3">
          智
        </div>
        <h1 class="text-lg font-600">企业级领域专家 Agent 平台</h1>
        <p class="text-xs text-gray-400 mt-1">多模态知识底座 + 自成长智能体 + 权限化工具网关 + 企业级记忆</p>
        <p class="text-xs text-orange-500 mt-1">演示环境 · 全部数据为虚构示例（is_mock）</p>
      </div>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="form.username" placeholder="username" @keyup.enter="submit" />
        </el-form-item>
        <el-form-item label="口令">
          <el-input v-model="form.password" type="password" show-password placeholder="password" @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" class="w-full" :loading="loading" @click="submit">登 录</el-button>
      </el-form>

      <div class="mt-6">
        <div class="text-xs text-gray-400 mb-2">演示账号（点击填入）</div>
        <div class="flex flex-wrap gap-2">
          <el-tag v-for="account in demoAccounts" :key="account.username" class="cursor-pointer" @click="pick(account)">
            {{ account.label }}
          </el-tag>
        </div>
      </div>
    </div>
  </div>
</template>
