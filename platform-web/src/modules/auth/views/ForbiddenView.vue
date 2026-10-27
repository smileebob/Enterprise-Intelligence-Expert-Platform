<script setup lang="ts">
/** 无权限提示：避免权限守卫把用户重定向回同样无权限的页面。 */
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const from = String(route.query.from || '')
</script>

<template>
  <div class="h-full flex items-center justify-center p-6">
    <div class="panel max-w-lg p-6 text-center">
      <el-result icon="warning" title="无访问权限" sub-title="当前账号没有访问该页面的权限。">
        <template #extra>
          <div class="text-xs text-gray-500 mb-3">请求路径：{{ from || '未知' }}</div>
          <el-button type="primary" @click="router.back()">返回上一页</el-button>
        </template>
      </el-result>
    </div>
  </div>
</template>
