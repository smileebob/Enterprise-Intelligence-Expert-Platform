<script setup lang="ts">
/** 草稿审核操作条：仅在持有 output.review 权限且草稿待审时由父组件渲染。 */
import { ref } from 'vue'
import type { ReviewAction } from '../types'

defineProps<{ loading: boolean }>()

const emit = defineEmits<{ review: [action: ReviewAction, note: string] }>()

const note = ref('')

function submit(action: ReviewAction) {
  emit('review', action, note.value.trim())
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <el-input
      v-model="note"
      size="small"
      maxlength="1000"
      placeholder="审核意见（退回时建议写明修改要求）"
    />
    <div class="flex items-center gap-2">
      <el-button size="small" type="success" :loading="loading" @click="submit('approve')">
        通过
      </el-button>
      <el-button size="small" type="danger" plain :loading="loading" @click="submit('reject')">
        退回
      </el-button>
      <span class="text-[11px] text-gray-400">起草人不能审核自己的草稿；通过不等于签发或发布</span>
    </div>
  </div>
</template>
