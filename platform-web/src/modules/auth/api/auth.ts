/** 认证域 API。 */
import { request } from '@/shared/api/request'
import type { LoginResult, MeInfo } from '@/shared/types/api'

export function login(username: string, password: string): Promise<LoginResult> {
  return request.post('/auth/login', { username, password })
}

export function fetchMe(): Promise<MeInfo> {
  return request.get('/auth/me')
}
