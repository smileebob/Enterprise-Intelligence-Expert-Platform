import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'virtual:uno.css'
import App from './App.vue'
import router from '@/shared/router'

// Element Plus 经 unplugin 按需引入（规则 N-06）；组件样式随模板自动注入，
// 命令式 API 的样式在调用处显式引入。locale 由 App.vue 的 el-config-provider 提供。
const app = createApp(App)
app.config.errorHandler = (error, _instance, info) => {
  const detail = error instanceof Error ? error.stack || error.message : String(error)
  console.error(`[vue-error] ${info}`, detail)
}
app.use(createPinia())
app.use(router)
app.mount('#app')
