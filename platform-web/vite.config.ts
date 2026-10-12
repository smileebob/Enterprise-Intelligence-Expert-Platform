import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [
    vue(),
    UnoCSS(),
    // Element Plus 按需引入（规则 N-06：禁止全量引入）
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/healthz': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        // 依赖分包（规则 N-08）：vue 全家桶与 axios 独立 vendor；
        // element-plus 经 unplugin 按需引入，交由其组件级自然分包（避免强制聚合成单大 chunk）
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('pinia') || id.includes('vue-router') || /node_modules[\\/]vue[\\/]/.test(id) || id.includes('@vue')) {
            return 'vendor-vue'
          }
          if (id.includes('axios')) return 'vendor-axios'
          return undefined
        },
      },
    },
  },
})
