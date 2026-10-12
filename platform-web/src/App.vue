<script setup lang="ts">
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

<template>
  <el-config-provider :locale="zhCn">
    <router-view />
    <div class="fixed bottom-2 right-3 text-[11px] text-gray-400 select-none z-9999 pointer-events-none">
      赛事模拟数据（is_mock）· 不用于生产决策
    </div>
  </el-config-provider>
</template>
