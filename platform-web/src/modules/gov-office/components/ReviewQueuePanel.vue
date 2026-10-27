<script setup lang="ts">
/** 待审草稿：审核人查看他人提交的公文/文稿并处理。 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { useUserStore } from '@/shared/stores/user'
import {
  listDocumentDrafts,
  listWritingDrafts,
  reviewDocumentDraft,
  reviewWritingDraft,
} from '../api/gov'
import type { DocumentDraft, ReviewAction, WritingDraft } from '../types'
import { draftStatusLabel, errorMessage, WRITING_MODE_OPTIONS } from '../presenters'
import DraftChecks from './DraftChecks.vue'

const userStore = useUserStore()
const documents = ref<DocumentDraft[]>([])
const writings = ref<WritingDraft[]>([])
const loading = ref(false)
const reviewing = ref('')
const error = ref('')

function modeLabel(mode: string): string {
  return WRITING_MODE_OPTIONS.find((item) => item.value === mode)?.label || mode
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [docResult, writingResult] = await Promise.all([
      listDocumentDrafts(),
      listWritingDrafts(),
    ])
    documents.value = docResult.items.filter((item) => item.owner_id !== userStore.me?.user_id)
    writings.value = writingResult.items.filter((item) => item.owner_id !== userStore.me?.user_id)
  } catch (err) {
    error.value = errorMessage(err, '加载待审草稿失败')
  } finally {
    loading.value = false
  }
}

async function reviewDocument(item: DocumentDraft, action: ReviewAction) {
  reviewing.value = item.draft_id
  try {
    await reviewDocumentDraft(item.draft_id, action, '待审列表人工审核')
    ElMessage.success(action === 'approve' ? '公文已通过审核' : '公文已退回')
    await load()
  } catch (err) {
    error.value = errorMessage(err, '审核失败')
  } finally {
    reviewing.value = ''
  }
}

async function reviewWriting(item: WritingDraft, action: ReviewAction) {
  reviewing.value = item.draft_id
  try {
    await reviewWritingDraft(item.draft_id, action, '待审列表人工审核')
    ElMessage.success(action === 'approve' ? '文稿已通过审核' : '文稿已退回')
    await load()
  } catch (err) {
    error.value = errorMessage(err, '审核失败')
  } finally {
    reviewing.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center justify-between">
      <div class="text-xs text-gray-500">
        仅显示他人提交且处于待审核状态的草稿；起草人与审核人不能是同一账号。
      </div>
      <el-button size="small" :loading="loading" @click="load">刷新待审列表</el-button>
    </div>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
    <el-empty
      v-if="!loading && !documents.length && !writings.length"
      description="暂无待审公文或文稿"
    />

    <div v-if="documents.length" class="flex flex-col gap-2">
      <div class="text-sm font-600 text-gray-700">待审公文（{{ documents.length }}）</div>
      <div v-for="item in documents" :key="item.draft_id" class="border border-gray-100 rounded p-3">
        <div class="flex flex-wrap items-center gap-2">
          <el-tag size="small" type="warning">{{ draftStatusLabel(item.status) }}</el-tag>
          <span class="text-sm font-500">{{ item.title }}</span>
          <span class="text-[11px] text-gray-400">{{ item.draft_id }} · 起草人 {{ item.owner_id }}</span>
        </div>
        <div class="text-xs text-gray-500 mt-1">
          {{ item.issuing_authority }} · {{ item.document_date }} · v{{ item.version }}
        </div>
        <DraftChecks :checks="item.checks" class="mt-2" />
        <div class="flex gap-2 mt-2">
          <el-button
            size="small"
            type="success"
            :loading="reviewing === item.draft_id"
            @click="reviewDocument(item, 'approve')"
          >通过</el-button>
          <el-button size="small" type="danger" @click="reviewDocument(item, 'reject')">退回</el-button>
        </div>
      </div>
    </div>

    <div v-if="writings.length" class="flex flex-col gap-2">
      <div class="text-sm font-600 text-gray-700">待审文稿（{{ writings.length }}）</div>
      <div v-for="item in writings" :key="item.draft_id" class="border border-gray-100 rounded p-3">
        <div class="flex flex-wrap items-center gap-2">
          <el-tag size="small" type="info">{{ modeLabel(item.mode) }}</el-tag>
          <span class="text-sm font-500">{{ item.request }}</span>
          <span class="text-[11px] text-gray-400">{{ item.draft_id }} · 起草人 {{ item.owner_id }}</span>
        </div>
        <div class="text-xs text-gray-500 mt-1">版本 v{{ item.version }} · {{ draftStatusLabel(item.status) }}</div>
        <div v-if="item.result.summary" class="text-xs text-gray-600 mt-2 line-clamp-3">
          {{ item.result.summary }}
        </div>
        <div class="flex gap-2 mt-2">
          <el-button
            size="small"
            type="success"
            :loading="reviewing === item.draft_id"
            @click="reviewWriting(item, 'approve')"
          >通过</el-button>
          <el-button size="small" type="danger" @click="reviewWriting(item, 'reject')">退回</el-button>
        </div>
      </div>
    </div>
  </div>
</template>
