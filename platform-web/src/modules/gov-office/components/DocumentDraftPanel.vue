<script setup lang="ts">
/** 公文起草（G-16）：模板装配、事实清单、分层审校与人工审核。 */
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { useUserStore } from '@/shared/stores/user'
import { createDocumentDraft, reviewDocumentDraft } from '../api/gov'
import type { DocumentDraft, DraftFact, GovDocType, ReviewAction } from '../types'
import { DOC_TYPE_OPTIONS, errorMessage, parseCitationIds } from '../presenters'
import SourceListEditor from './SourceListEditor.vue'
import DocumentDraftResult from './DocumentDraftResult.vue'

const userStore = useUserStore()

function today(): string {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 10)
}

const form = reactive<{
  doc_type: GovDocType
  title: string
  recipient: string
  issuing_authority: string
  document_date: string
  purpose: string
  facts: DraftFact[]
  citationText: string
}>({
  doc_type: 'notice',
  title: '',
  recipient: '',
  issuing_authority: '',
  document_date: today(),
  purpose: '',
  facts: [{ text: '', source_ref: '' }],
  citationText: '',
})

const loading = ref(false)
const reviewing = ref(false)
const error = ref('')
const draft = ref<DocumentDraft | null>(null)
const duplicated = ref(false)

const canReview = computed(
  () =>
    userStore.hasPerm('output.review') &&
    Boolean(draft.value) &&
    draft.value?.owner_id !== userStore.me?.user_id,
)

async function submit() {
  const facts = form.facts
    .filter((item) => item.text.trim())
    .map((item) => ({ text: item.text.trim(), source_ref: item.source_ref.trim() }))

  if (!form.title.trim()) {
    ElMessage.warning('请填写公文标题')
    return
  }
  if (!form.issuing_authority.trim()) {
    ElMessage.warning('请填写发文机关')
    return
  }
  if (!form.document_date) {
    ElMessage.warning('请选择成文日期')
    return
  }
  if (!form.purpose.trim()) {
    ElMessage.warning('请填写起草目的')
    return
  }

  loading.value = true
  error.value = ''
  try {
    const result = await createDocumentDraft({
      doc_type: form.doc_type,
      title: form.title.trim(),
      recipient: form.recipient.trim(),
      issuing_authority: form.issuing_authority.trim(),
      document_date: form.document_date,
      purpose: form.purpose.trim(),
      facts,
      policy_citations: parseCitationIds(form.citationText),
    })
    draft.value = result.draft
    duplicated.value = result.duplicated
    ElMessage.success(result.duplicated ? '幂等命中，返回既有草稿' : '草稿已生成，等待人工审核')
  } catch (err) {
    draft.value = null
    error.value = errorMessage(err, '公文起草失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function onReview(action: ReviewAction, note: string) {
  if (!draft.value) return
  reviewing.value = true
  try {
    draft.value = await reviewDocumentDraft(draft.value.draft_id, action, note)
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
      <div class="panel-title">起草要素（文种硬规则 + 事实核对）</div>
      <div class="p-4 flex flex-col gap-3">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="文种">
            <el-select v-model="form.doc_type" class="w-full">
              <el-option
                v-for="item in DOC_TYPE_OPTIONS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="标题">
            <el-input v-model="form.title" maxlength="512" placeholder="例如：关于开展安全生产检查的通知" />
          </el-form-item>
          <el-form-item label="主送单位">
            <el-input v-model="form.recipient" maxlength="256" placeholder="例如：各街道办事处、区直属各单位" />
          </el-form-item>
          <el-form-item label="发文机关">
            <el-input v-model="form.issuing_authority" maxlength="256" placeholder="例如：杭州市余杭区人民政府办公室" />
          </el-form-item>
          <el-form-item label="成文日期">
            <el-date-picker
              v-model="form.document_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择成文日期"
              class="w-full"
            />
          </el-form-item>
          <el-form-item label="起草目的">
            <el-input
              v-model="form.purpose"
              type="textarea"
              :rows="3"
              maxlength="4000"
              show-word-limit
              placeholder="说明发文依据、目标与需要部署的事项"
            />
          </el-form-item>
          <el-form-item label="政策引用 ID（可选，逗号分隔）">
            <el-input v-model="form.citationText" placeholder="例如：CLS-xxxx@v1, CLS-yyyy@v1" />
          </el-form-item>
        </el-form>

        <div class="text-xs font-600 text-gray-600">事实材料与来源</div>
        <SourceListEditor v-model="form.facts" add-label="新增事实" />
        <div class="text-[11px] text-gray-400">
          正文中的数字、日期必须能在事实材料中找到来源，否则会被标记为阻断项。
        </div>
        <el-button type="primary" :loading="loading" @click="submit">生成待审草稿</el-button>
        <div class="text-[11px] text-gray-400">
          草稿为离线 / 抽取式生成，提交后进入待审；系统不会自动签发或发布。
        </div>
      </div>
    </div>

    <div class="panel min-h-[420px]">
      <div class="panel-title">草稿与审校</div>
      <div class="p-4 flex flex-col gap-3">
        <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
        <el-empty v-if="!draft && !error" description="填写起草要素并提交后可查看结构化草稿与审校项" />
        <DocumentDraftResult
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
