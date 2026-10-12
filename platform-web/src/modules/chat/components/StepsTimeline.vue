<script setup lang="ts">
/** 思考步骤时间线（可折叠，规则：过程透明）。 */
import type { StepItem } from '@/modules/chat/stores/chatStore'

defineProps<{ steps: StepItem[] }>()

const nodeNames: Record<string, string> = {
  clarify: '需求澄清',
  plan: '任务规划',
  retrieve: '分区召回',
  fuse: '融合消解',
  tool: '工具调用',
  evaluate: '六维评估',
  generate: '方案生成',
  self_check: '输出自检',
}

function statusType(status: string) {
  if (status === 'done') return 'success'
  if (status === 'replan') return 'warning'
  if (status === 'interrupted' || status === 'manual_review') return 'danger'
  return 'info'
}
</script>

<template>
  <el-collapse>
    <el-collapse-item name="steps">
      <template #title>
        <span class="text-xs text-gray-500">思考步骤（{{ steps.length }}）</span>
      </template>
      <div class="flex flex-col gap-2 pl-1">
        <div v-for="(step, index) in steps" :key="index" class="flex gap-2 items-start">
          <el-tag size="small" :type="statusType(step.status)" effect="plain" class="shrink-0">
            {{ nodeNames[step.node] || step.node }}
          </el-tag>
          <span class="text-xs text-gray-600 leading-5">{{ step.summary }}</span>
        </div>
        <div v-if="!steps.length" class="text-xs text-gray-400">（暂无步骤）</div>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>
