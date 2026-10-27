<script setup lang="ts">
/** 文稿 / 总结 / 咨询草稿视图：按模式展示抽取结果、来源映射、覆盖率与双层咨询答复。 */
import { computed } from 'vue'
import type { ConsultationItem, ReviewAction, WritingDraft } from '../types'
import {
  WRITING_MODE_OPTIONS,
  coveragePercent,
  draftStatusDescription,
  draftStatusLabel,
  draftStatusType,
} from '../presenters'
import CitationList from './CitationList.vue'
import DraftChecks from './DraftChecks.vue'
import DraftReviewBar from './DraftReviewBar.vue'

const props = defineProps<{
  draft: WritingDraft
  duplicated: boolean
  canReview: boolean
  reviewing: boolean
}>()

const emit = defineEmits<{ review: [action: ReviewAction, note: string] }>()

const modeLabel = computed(
  () => WRITING_MODE_OPTIONS.find((item) => item.value === props.draft.mode)?.label || props.draft.mode,
)

const coverage = computed(() => props.draft.result.coverage || null)

const coverageText = computed(() => {
  const value = coverage.value
  if (!value) return ''
  if (typeof value.source_materials === 'number') {
    return `素材覆盖 ${value.used_materials ?? 0}/${value.source_materials} 条`
  }
  if (typeof value.source_units === 'number') {
    return `原文单元覆盖 ${value.represented_units ?? 0}/${value.source_units} 条`
  }
  return ''
})

const consultationGroups = computed<Array<{ label: string; items: ConsultationItem[] }>>(() => {
  const result = props.draft.result
  return [
    { label: '适用条件', items: result.conditions || [] },
    { label: '申请材料', items: result.required_materials || [] },
    { label: '办理渠道', items: result.channels || [] },
    { label: '办理时限', items: result.time_limits || [] },
  ].filter((group) => group.items.length > 0)
})

function onReview(action: ReviewAction, note: string) {
  emit('review', action, note)
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-2">
      <el-tag size="small" type="info">{{ modeLabel }}</el-tag>
      <el-tag size="small" :type="draftStatusType(draft.status)">
        {{ draftStatusLabel(draft.status) }}
      </el-tag>
      <span class="text-xs text-gray-500">版本 v{{ draft.version }}</span>
      <span class="text-xs text-gray-400">{{ draft.draft_id }}</span>
      <el-tag v-if="duplicated" size="small" type="info" effect="plain">幂等命中（未重复创建）</el-tag>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="离线 / 抽取式草稿，需人工审核"
      :description="draftStatusDescription(draft.status) || '结果仅由来源材料抽取拼接，未自动发布或答复。'"
    />

    <div class="text-xs text-gray-500">
      <span class="text-gray-400">事项要求：</span>{{ draft.request }}
      <span v-if="draft.audience"> · <span class="text-gray-400">对象：</span>{{ draft.audience }}</span>
    </div>

    <div v-if="coverage" class="flex items-center gap-3">
      <el-progress
        :percentage="coveragePercent(coverage.ratio)"
        :stroke-width="10"
        class="flex-1"
      />
      <span class="text-xs text-gray-500 whitespace-nowrap">{{ coverageText }}</span>
    </div>

    <div v-if="draft.result.summary" class="text-sm text-gray-700 leading-6 whitespace-pre-wrap">
      {{ draft.result.summary }}
    </div>

    <!-- 总结提炼：要点 / 任务 / 覆盖 -->
    <template v-if="draft.mode === 'summarization'">
      <div v-if="draft.result.key_points?.length" class="flex flex-col gap-2">
        <div class="text-xs font-600 text-gray-600">
          要点与原文映射（{{ draft.result.key_points.length }} 条）
        </div>
        <div
          v-for="point in draft.result.key_points"
          :key="point.point_id"
          class="border border-gray-100 rounded p-2"
        >
          <div class="text-sm text-gray-700">{{ point.text }}</div>
          <div class="text-[11px] text-gray-400 mt-1">
            {{ point.point_id }} · 原文单元 {{ point.unit_id }} · 来源 {{ point.source_ref || '未登记' }}
          </div>
        </div>
      </div>
      <div v-if="draft.result.tasks?.length" class="flex flex-col gap-1">
        <div class="text-xs font-600 text-gray-600">任务清单（{{ draft.result.tasks.length }} 条）</div>
        <div v-for="task in draft.result.tasks" :key="task.task_id" class="text-xs text-gray-600">
          {{ task.task_id }}. {{ task.text }}
          <span class="text-gray-400">（{{ task.source_ref || '未登记来源' }}）</span>
        </div>
      </div>
    </template>

    <!-- 文稿撰写：分段正文与来源 -->
    <template v-if="draft.mode === 'content_writing'">
      <div
        v-for="(section, index) in draft.result.sections || []"
        :key="`${section.heading}-${index}`"
        class="border border-gray-100 rounded p-2"
      >
        <div class="text-sm font-500 text-gray-800 mb-1">{{ section.heading }}</div>
        <div class="text-sm text-gray-700 whitespace-pre-wrap leading-6">
          {{ section.content || '（暂无匹配素材）' }}
        </div>
        <div v-if="section.source_refs?.length" class="text-[11px] text-gray-400 mt-1">
          来源：{{ section.source_refs.join('、') }}
        </div>
        <div v-if="section.warning" class="text-[11px] text-amber-600 mt-1">{{ section.warning }}</div>
      </div>
      <el-alert
        v-if="draft.result.outline_suggestions?.length"
        type="info"
        :closable="false"
        show-icon
        title="尚未确认提纲，未生成正文"
      >
        <template #default>
          <div class="text-xs">
            建议提纲：
            {{ draft.result.outline_suggestions.map((item) => item.heading).join('、') }}
          </div>
        </template>
      </el-alert>
    </template>

    <!-- 咨询答复拟稿：公众答复 + 内部依据（同源） -->
    <template v-if="draft.mode === 'consultation_draft'">
      <div class="flex flex-col gap-2">
        <div class="text-xs font-600 text-gray-600">公众答复（草稿，未发布）</div>
        <div class="border border-gray-100 rounded p-3 text-sm text-gray-700 leading-6 whitespace-pre-wrap bg-gray-50/40">
          {{ draft.result.public_reply || '政策依据不足，未形成公众答复。' }}
        </div>
      </div>
      <div v-for="group in consultationGroups" :key="group.label" class="flex flex-col gap-1">
        <div class="text-xs font-600 text-gray-600">{{ group.label }}</div>
        <div v-for="(item, index) in group.items" :key="index" class="text-xs text-gray-600">
          {{ item.text }}
          <span class="text-gray-400">（依据 {{ item.citation_ids.join('、') }}）</span>
        </div>
      </div>
      <div class="flex flex-col gap-2">
        <div class="text-xs font-600 text-gray-600">内部依据（与公众答复同源）</div>
        <CitationList
          :citations="draft.result.internal_basis || []"
          empty-text="未形成内部依据"
        />
      </div>
      <div v-if="draft.result.high_risk_markers?.length" class="text-xs text-red-500">
        高风险标记（应转人工确认）：{{ draft.result.high_risk_markers.join('、') }}
      </div>
      <div class="text-[11px] text-gray-400">
        发布状态：{{ draft.result.publish_status || 'draft_only' }} · 系统无自动公开发布行为
      </div>
    </template>

    <div v-if="draft.result.warnings?.length" class="flex flex-col gap-1">
      <div class="text-xs font-600 text-gray-600">提示与限制</div>
      <ul class="text-xs text-amber-700 list-disc pl-5">
        <li v-for="(warning, index) in draft.result.warnings" :key="index">{{ warning }}</li>
      </ul>
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
