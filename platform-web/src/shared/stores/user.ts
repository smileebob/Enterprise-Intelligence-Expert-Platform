/** 登录态与权限（全局 store，setup 风格，规则 N-03）。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { request } from '@/shared/api/request'
import type { LoginResult, MeInfo } from '@/shared/types/api'

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(localStorage.getItem('token') || '')
  const me = ref<MeInfo | null>(null)

  const isLoggedIn = computed(() => Boolean(token.value))
  const permissions = computed(() => new Set(me.value?.permissions || []))

  async function login(username: string, password: string) {
    const data = await request.post<LoginResult>('/auth/login', { username, password })
    token.value = data.access_token
    localStorage.setItem('token', data.access_token)
    await fetchMe()
    return data.user
  }

  async function fetchMe() {
    me.value = await request.get<MeInfo>('/auth/me')
  }

  function hasPerm(perm: string) {
    return permissions.value.has(perm)
  }

  function logout() {
    token.value = ''
    me.value = null
    localStorage.removeItem('token')
  }

  return { token, me, isLoggedIn, permissions, login, fetchMe, hasPerm, logout }
})
