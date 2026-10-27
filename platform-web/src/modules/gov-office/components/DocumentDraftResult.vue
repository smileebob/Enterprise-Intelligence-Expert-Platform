<script setup lang="ts">
/** 公文草稿视图：模板/版本/结构化正文/审校项/审核操作与 DOCX 导出。 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { downloadDocumentExport } from '../api/gov'
import type { DocumentDraft, ReviewAction } from '../types'
import { docTypeLabel, draftStatusDescription, draftStatusLabel, draftStatusType } from '../presenters'
import DraftChecks from './DraftChecks.vue'
import DraftReviewBar from './DraftReviewBar.vue'

const props = defineProps<{
  draft: DocumentDraft
  duplicated: boolean
  canReview: boolean
  reviewing: boolean
}>()

const emit = defineEmits<{ review: [action: ReviewAction, note: string] }>()

function onReview(action: ReviewAction, note: string) {
  emit('review', action, note)
}

const exporting = ref(false)
async function onExport() {
  exporting.value = true
  try {
    await downloadDocumentExport(props.draft.draft_id)
    ElMessage.success('已下载 DOCX 与证据包；导出不代表签发或用印')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-2">
      <el-tag size="small" :type="draftStatusType(draft.status)">
        {{ draftStatusLabel(draft.status) }}
      </el-tag>
      <span class="text-xs text-gray-500">版本 v{{ draft.version }}</span>
      <span class="text-xs text-gray-500">
        模板 {{ draft.template.template_id }} / {{ draft.template.version }}
      </span>
      <span class="text-xs text-gray-400">{{ draft.draft_id }}</span>
      <el-tag v-if="duplicated" size="small" type="info" effect="plain">幂等命中（未重复创建）</el-tag>
      <el-button
        v-if="draft.status === 'approved'"
        size="small"
        type="primary"
        :loading="exporting"
        @click="onExport"
      >
        下载 DOCX + 证据包
      </el-button>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      :title="draftStatusDescription(draft.status) || '离线 / 抽取式草稿，需人工审核'"
    />

    <div class="border border-gray-100 rounded p-3 bg-gray-50/40 text-xs text-gray-600 flex flex-col gap-1">
      <div class="text-sm font-600 text-gray-800">{{ draft.title }}</div>
      <div>
        <span class="text-gray-400">文种：</span>
        {{ docTypeLabel(draft.doc_type) }} ·
        <span class="text-gray-400">主送：</span>{{ draft.recipient || '未填写' }} ·
        <span class="text-gray-400">发文机关：</span>{{ draft.issuing_authority }}
      </div>
      <div><span class="text-gray-400">成文日期：</span>{{ draft.document_date }}</div>
      <div class="whitespace-pre-wrap"><span class="text-gray-400">起草目的：</span>{{ draft.purpose }}</div>
    </div>

    <div class="flex flex-col gap-2">
      <div class="text-xs font-600 text-gray-600">正文结构（sections）</div>
      <div
        v-for="(section, index) in draft.sections"
        :key="`${section.heading}-${index}`"
        class="border border-gray-100 rounded p-2"
      >
        <div class="text-sm font-500 text-gray-800 mb-1">{{ section.heading }}</div>
        <div class="text-sm text-gray-700 whitespace-pre-wrap leading-6">{{ section.content }}</div>
      </div>
      <div v-if="!draft.sections.length" class="text-xs text-gray-400">未生成正文结构</div>
    </div>

    <div v-if="draft.facts.length" class="flex flex-col gap-1">
      <div class="text-xs font-600 text-gray-600">事实材料与来源</div>
      <div v-for="(fact, index) in draft.facts" :key="index" class="text-xs text-gray-600">
        {{ index + 1 }}. {{ fact.text }}
        <span class="text-gray-400">（{{ fact.source_ref || '未登记来源' }}）</span>
      </div>
    </div>

    <div v-if="draft.policy_citations.length" class="text-xs text-gray-500 break-all">
      政策引用：{{ draft.policy_citations.join('、') }}
    </div>

    <div class="flex flex-col gap-2">
      <div class="text-xs font-600 text-gray-600">审校结果（checks）</div>
      <DraftChecks :checks="draft.checks" />
    </div>

    <div v-if="draft.review" class="text-xs text-gray-500 flex flex-col gap-1">
      <div class="font-600 text-gray-600">审核记录</div>
      <div>审核人：{{ draft.review.reviewed_by || '—' }}</div>
      <div>审核时间：{{ draft.review.reviewed_at || '—' }}</div>
      <div>审核意见：{{ draft.review.note || '—' }}</div>
    </div>

    <div v-if="canReview && draft.status === 'pending_review'" class="border-t border-gray-100 pt-3">
      <div class="text-xs font-600 text-gray-600 mb-2">人工审核（output.review）</div>
      <DraftReviewBar :loading="reviewing" @review="onReview" />
    </div>
  </div>
</template>
