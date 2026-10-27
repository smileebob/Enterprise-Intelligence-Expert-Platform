<script setup lang="ts">
/** 审校结果：按严重度着色（error 阻断 / warning 提示 / info 说明）。 */
import type { DraftCheck } from '../types'
import { severityLabel, severityType } from '../presenters'

defineProps<{ checks: DraftCheck[] }>()
</script>

<template>
  <div v-if="checks.length" class="flex flex-col gap-2">
    <div
      v-for="(check, index) in checks"
      :key="`${check.code}-${index}`"
      class="flex items-start gap-2 text-xs"
    >
      <el-tag size="small" :type="severityType(check.severity)" effect="plain">
        {{ severityLabel(check.severity) }}
      </el-tag>
      <div class="flex-1 min-w-0">
        <div class="text-gray-700">{{ check.message }}</div>
        <div class="text-[11px] text-gray-400">{{ check.code }} · {{ check.location }}</div>
      </div>
    </div>
  </div>
  <el-alert
    v-else
    type="success"
    :closable="false"
    show-icon
    title="模板与事实核对未发现阻断项（仍须人工审核）"
  />
</template>
