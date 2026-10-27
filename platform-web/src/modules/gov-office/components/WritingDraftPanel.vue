<script setup lang="ts">
/** 文稿 / 总结 / 咨询（G-17、G-18）：素材来源、提纲、双层咨询答复与人工审核。 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { useUserStore } from '@/shared/stores/user'
import { createWritingDraft, reviewWritingDraft } from '../api/gov'
import type { DraftFact, ReviewAction, WritingDraft, WritingMode } from '../types'
import { WRITING_MODE_OPTIONS, errorMessage, parseCitationIds } from '../presenters'
import SourceListEditor from './SourceListEditor.vue'
import WritingDraftResult from './WritingDraftResult.vue'

const userStore = useUserStore()

const form = reactive<{
  mode: WritingMode
  request: string
  audience: string
  jurisdiction: string
  subject: string
  as_of: string
  materials: DraftFact[]
  outline: Array<{ heading: string }>
  citationText: string
}>({
  mode: 'summarization',
  request: '',
  audience: '',
  jurisdiction: '',
  subject: '',
  as_of: '',
  materials: [{ text: '', source_ref: '' }],
  outline: [{ heading: '' }],
  citationText: '',
})

const loading = ref(false)
const reviewing = ref(false)
const error = ref('')
const draft = ref<WritingDraft | null>(null)
const duplicated = ref(false)

const isContentWriting = computed(() => form.mode === 'content_writing')
const isConsultation = computed(() => form.mode === 'consultation_draft')
const canReview = computed(
  () =>
    userStore.hasPerm('output.review') &&
    Boolean(draft.value) &&
    draft.value?.owner_id !== userStore.me?.user_id,
)

watch(
  () => form.mode,
  () => {
    error.value = ''
    if (isContentWriting.value && form.outline.length === 0) {
      form.outline = [{ heading: '' }]
    }
  },
)

function addOutline() {
  if (form.outline.length >= 30) return
  form.outline = [...form.outline, { heading: '' }]
}

function removeOutline(index: number) {
  form.outline = form.outline.filter((_, position) => position !== index)
}

async function submit() {
  if (!form.request.trim()) {
    ElMessage.warning('请填写撰写要求或咨询事项')
    return
  }
  const materials = form.materials
    .filter((item) => item.text.trim())
    .map((item) => ({ text: item.text.trim(), source_ref: item.source_ref.trim() }))
  if (!materials.length) {
    ElMessage.warning('请至少提供一条来源材料')
    return
  }
  const outline = isContentWriting.value
    ? form.outline
        .filter((item) => item.heading.trim())
        .map((item) => ({ heading: item.heading.trim() }))
    : []
  if (isContentWriting.value && !outline.length) {
    ElMessage.warning('文稿撰写请至少填写一个提纲条目')
    return
  }
  if (isConsultation.value && (!form.jurisdiction.trim() || !form.as_of)) {
    ElMessage.warning('咨询答复拟稿需填写政策地区与事项时间')
    return
  }

  loading.value = true
  error.value = ''
  try {
    const result = await createWritingDraft({
      mode: form.mode,
      request: form.request.trim(),
      audience: form.audience.trim(),
      jurisdiction: isConsultation.value ? form.jurisdiction.trim() : '',
      subject: isConsultation.value ? form.subject.trim() : '',
      as_of: isConsultation.value ? form.as_of || null : null,
      materials,
      outline,
      policy_citations: parseCitationIds(form.citationText),
    })
    draft.value = result.draft
    duplicated.value = result.duplicated
    ElMessage.success(result.duplicated ? '幂等命中，返回既有草稿' : '草稿已生成，等待人工审核')
  } catch (err) {
    draft.value = null
    error.value = errorMessage(err, '草稿生成失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function onReview(action: ReviewAction, note: string) {
  if (!draft.value) return
  reviewing.value = true
  try {
    draft.value = await reviewWritingDraft(draft.value.draft_id, action, note)
    duplicated.value = false
    ElMessage.success(action === 'approve' ? '已通过人工审核（仍需线下签发）' : '已退回起草人')
  } catch (err) {
    error.value = errorMessage(err, '审核操作失败')
  } finally {
    reviewing.value = false
  }
}
</script>

<template>
  <div class="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-4 items-start">
    <div class="panel">
      <div class="panel-title">生成条件（按模式切换输入项）</div>
      <div class="p-4 flex flex-col gap-3">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="模式">
            <el-select v-model="form.mode" class="w-full">
              <el-option
                v-for="item in WRITING_MODE_OPTIONS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              >
                <span>{{ item.label }}</span>
                <span class="text-[11px] text-gray-400 ml-2">{{ item.description }}</span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="事项要求">
            <el-input
              v-model="form.request"
              type="textarea"
              :rows="3"
              maxlength="4000"
              show-word-limit
              placeholder="例如：提炼会议纪要中的任务分工 / 起草优化营商环境工作简报"
            />
          </el-form-item>
          <el-form-item label="服务对象（可选）">
            <el-input v-model="form.audience" maxlength="256" placeholder="例如：局领导 / 社会公众" />
          </el-form-item>

          <template v-if="isConsultation">
            <el-form-item label="政策地区">
              <el-input v-model="form.jurisdiction" maxlength="128" placeholder="例如：杭州市余杭区" />
            </el-form-item>
            <el-form-item label="适用对象（可选）">
              <el-input v-model="form.subject" maxlength="256" placeholder="例如：小微企业" />
            </el-form-item>
            <el-form-item label="事项时间">
              <el-date-picker
                v-model="form.as_of"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择事项发生或适用日期"
                class="w-full"
              />
            </el-form-item>
          </template>

          <el-form-item label="政策引用 ID（可选，逗号分隔）">
            <el-input v-model="form.citationText" placeholder="例如：CLS-xxxx@v1" />
          </el-form-item>
        </el-form>

        <div class="text-xs font-600 text-gray-600">来源材料（至少一条，逐条登记来源）</div>
        <SourceListEditor v-model="form.materials" add-label="新增材料" text-placeholder="粘贴会议记录、来文、调研材料等原文" />

        <template v-if="isContentWriting">
          <div class="text-xs font-600 text-gray-600">提纲（按提纲逐段匹配素材）</div>
          <div v-for="(item, index) in form.outline" :key="index" class="flex items-center gap-2">
            <el-input v-model="item.heading" size="small" maxlength="200" placeholder="章节标题" />
            <el-button size="small" text type="danger" @click="removeOutline(index)">删除</el-button>
          </div>
          <el-button size="small" plain :disabled="form.outline.length >= 30" @click="addOutline">
            新增提纲项
          </el-button>
        </template>

        <el-button type="primary" :loading="loading" @click="submit">生成待审草稿</el-button>
        <div class="text-[11px] text-gray-400">
          输出为离线 / 抽取式草稿，可能缺少适用条件；须人工审核，系统不自动发布。
        </div>
      </div>
    </div>

    <div class="panel min-h-[420px]">
      <div class="panel-title">草稿结果与审校</div>
      <div class="p-4 flex flex-col gap-3">
        <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
        <el-empty v-if="!draft && !error" description="填写条件与来源材料并提交后可查看草稿" />
        <WritingDraftResult
          v-if="draft"
          :draft="draft"
          :duplicated="duplicated"
          :can-review="canReview"
          :reviewing="reviewing"
          @review="onReview"
        />
      </div>
    </div>
  </div>
</template>
