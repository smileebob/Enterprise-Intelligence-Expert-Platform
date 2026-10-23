/** 全站唯一 axios 实例（规则 N-05）：统一超时、JWT 注入、响应解包 {code,message,data}、401 跳登录。 */
import axios, { AxiosError, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import type { ApiResponse } from '@/shared/types/api'

export class ApiError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
  }
}

const instance = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
})

instance.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now())
  config.headers['X-Request-ID'] = requestId
  return config
})

instance.interceptors.response.use(
  (resp) => {
    const body = resp.data as ApiResponse<unknown>
    if (body && typeof body === 'object' && 'code' in body && body.code !== 'SUCCESS') {
      ElMessage.error(body.message || '请求失败')
      return Promise.reject(new ApiError(body.code, body.message))
    }
    return body.data as never
  },
  (error: AxiosError<ApiResponse<unknown>>) => {
    const status = error.response?.status
    const body = error.response?.data
    if (status === 401) {
      localStorage.removeItem('token')
      if (!location.hash.includes('/login')) {
        location.hash = '#/login'
      }
      ElMessage.warning('登录已失效，请重新登录')
    } else if (status === 429) {
      ElMessage.warning('请求过于频繁，请稍后重试')
    } else {
      ElMessage.error(body?.message || '网络异常，请稍后重试')
    }
    return Promise.reject(error)
  },
)

/** 类型化门面：拦截器已解包 {code,message,data}，此处让返回值类型与运行时一致。 */
export const request = {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return instance.get(url, config) as unknown as Promise<T>
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.post(url, data, config) as unknown as Promise<T>
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.put(url, data, config) as unknown as Promise<T>
  },
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return instance.delete(url, config) as unknown as Promise<T>
  },
}

/** /api/v2 门面（与 v1 共用拦截器/JWT/统一解包）。 */
export const requestV2 = {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return instance.get(url, { ...config, baseURL: '/api/v2' }) as unknown as Promise<T>
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.post(url, data, { ...config, baseURL: '/api/v2' }) as unknown as Promise<T>
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return instance.put(url, data, { ...config, baseURL: '/api/v2' }) as unknown as Promise<T>
  },
}

/** 幂等键：写接口（入库/评估）必带（规则 B-01） */
export function newIdempotencyKey(): string {
  return crypto.randomUUID ? crypto.randomUUID() : `idem-${Date.now()}-${Math.random()}`
}

export default request
