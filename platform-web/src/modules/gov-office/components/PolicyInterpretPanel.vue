<script setup lang="ts">
/** 政策解读（G-14）：条件澄清、条款级证据、冲突与证据不足展示。 */
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { DatePickerInstance, InputInstance } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { interpretPolicy } from '../api/gov'
import type { PolicyInterpretResult } from '../types'
import {
  errorMessage,
  interpretOutcomeLabel,
  interpretOutcomeType,
  supportStatusLabel,
  supportStatusType,
} from '../presenters'
import CitationList from './CitationList.vue'

const form = reactive({
  question: '',
  jurisdiction: '',
  subject: '',
  as_of: '',
})

const loading = ref(false)
const error = ref('')
const submitted = ref(false)
const result = ref<PolicyInterpretResult | null>(null)
const formRef = ref<HTMLElement | null>(null)
const jurisdictionRef = ref<InputInstance | null>(null)
const asOfRef = ref<DatePickerInstance | null>(null)

/** 澄清恢复：滚动到条件区并把焦点落到缺失字段。 */
function applyClarification(field: string) {
  formRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  if (field.includes('地区')) {
    jurisdictionRef.value?.focus()
  } else if (field.includes('时间')) {
    asOfRef.value?.focus()
  }
}

async function submit() {
  if (!form.question.trim()) {
    ElMessage.warning('请填写需要解读的政策问题')
    return
  }
  loading.value = true
  error.value = ''
  try {
    result.value = await interpretPolicy({
      question: form.question.trim(),
      jurisdiction: form.jurisdiction.trim() || undefined,
      subject: form.subject.trim() || undefined,
      as_of: form.as_of || undefined,
    })
  } catch (err) {
    result.value = null
    error.value = errorMessage(err, '政策解读失败，请稍后重试')
  } finally {
    submitted.value = true
    loading.value = false
  }
}
</script>

<template>
  <div class="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-4 items-start">
    <div ref="formRef" class="panel">
      <div class="panel-title">解读条件（地区与事项时间决定适用版本）</div>
      <div class="p-4 flex flex-col gap-3">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="政策问题">
            <el-input
              v-model="form.question"
              type="textarea"
              :rows="4"
              maxlength="1000"
              show-word-limit
              placeholder="例如：本区企业申请高技能人才培训补贴需要哪些条件和材料？"
            />
          </el-form-item>
          <el-form-item label="政策地区 / 行政区划">
            <el-input
              ref="jurisdictionRef"
              v-model="form.jurisdiction"
              maxlength="128"
              placeholder="例如：杭州市余杭区"
            />
          </el-form-item>
          <el-form-item label="适用对象（可选）">
            <el-input v-model="form.subject" maxlength="256" placeholder="例如：中小微企业 / 灵活就业人员" />
          </el-form-item>
          <el-form-item label="事项时间">
            <el-date-picker
              ref="asOfRef"
              v-model="form.as_of"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择事项发生或适用日期"
              class="w-full"
            />
          </el-form-item>
        </el-form>
        <div class="text-[11px] text-gray-400">
          未填写地区或时间时，系统只返回澄清问题，不生成确定性结论。
        </div>
        <el-button type="primary" :loading="loading" @click="submit">开始解读</el-button>
      </div>
    </div>

    <div class="panel min-h-[360px]">
      <div class="panel-title flex items-center justify-between">
        <span>解读结果（条款原文摘录 + 支持性检查）</span>
        <el-tag v-if="result" size="small" :type="interpretOutcomeType(result.outcome)">
          {{ interpretOutcomeLabel(result.outcome) }}
        </el-tag>
      </div>

      <div class="p-4 flex flex-col gap-3">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="离线 / 抽取式解读，需人工审核"
          description="结论为已发布政策条款的原文摘录，不构成个案适用决定；系统不自动对外发布或答复。"
        />

        <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

        <el-empty v-if="!submitted && !error" description="填写左侧条件并提交后可查看条款级证据" />

        <template v-if="result">
          <div class="flex flex-wrap items-center gap-2 text-xs text-gray-500">
            <span v-if="result.as_of">事项时间：{{ result.as_of }}</span>
            <span v-if="result.question" class="truncate">问题：{{ result.question }}</span>
            <el-tag v-if="result.manual_review_required" size="small" type="danger" effect="plain">
              需人工复核
            </el-tag>
          </div>

          <el-alert
            v-if="result.outcome === 'clarify'"
            type="warning"
            :closable="false"
            show-icon
            title="缺少必要条件，暂不生成结论"
            description="请补充下列条件后重新提交。"
          />
          <div v-if="result.clarifications?.length" class="flex flex-col gap-2">
            <div
              v-for="item in result.clarifications || []"
              :key="item.field"
              class="flex items-center gap-2 border border-amber-100 bg-amber-50/60 rounded p-2"
            >
              <el-tag size="small" type="warning">{{ item.field }}</el-tag>
              <span class="text-xs text-gray-700 flex-1">{{ item.question }}</span>
              <el-button size="small" text type="primary" @click="applyClarification(item.field)">
                去补充
              </el-button>
            </div>
          </div>

          <div v-if="result.summary" class="text-sm text-gray-700 leading-6 whitespace-pre-wrap">
            {{ result.summary }}
          </div>

          <div v-if="result.conflicts.length" class="flex flex-col gap-2">
            <div class="text-xs font-600 text-gray-600">适用冲突（不强行裁决，转人工核对）</div>
            <el-alert
              v-for="(conflict, index) in result.conflicts"
              :key="`${conflict.conflict_type}-${index}`"
              type="error"
              :closable="false"
              show-icon
              :title="conflict.message"
            >
              <template #default>
                <div class="text-xs">类型：{{ conflict.conflict_type }}</div>
                <div class="text-xs mt-1">涉及文件：{{ conflict.doc_ids.join('、') }}</div>
              </template>
            </el-alert>
          </div>

          <div v-if="result.claims.length" class="flex flex-col gap-2">
            <div class="text-xs font-600 text-gray-600">条款论断与支持状态（{{ result.claims.length }} 条）</div>
            <div
              v-for="claim in result.claims"
              :key="claim.claim_id"
              class="border border-gray-100 rounded p-2"
            >
              <div class="flex flex-wrap items-center gap-2 mb-1">
                <el-tag size="small" :type="supportStatusType(claim.support_status)">
                  {{ supportStatusLabel(claim.support_status) }}
                </el-tag>
                <span class="text-[11px] text-gray-400">
                  {{ claim.claim_id }} · {{ claim.support_method || '未标注方法' }}
                </span>
                <span v-if="typeof claim.coverage === 'number'" class="text-[11px] text-gray-400">
                  覆盖 {{ Math.round(claim.coverage * 100) }}%
                </span>
                <el-tag v-if="claim.negation_risk" size="small" type="danger" effect="plain">
                  否定风险
                </el-tag>
              </div>
              <div class="text-sm text-gray-700 whitespace-pre-wrap">{{ claim.text }}</div>
              <div class="text-[11px] text-gray-400 mt-1">
                证据：{{ claim.citation_ids.join('、') || '无' }}
              </div>
            </div>
          </div>

          <div class="flex flex-col gap-2">
            <div class="text-xs font-600 text-gray-600">引用条款与原文（{{ result.citations.length }} 条）</div>
            <CitationList
              :citations="result.citations"
              empty-text="本次未检索到可引用的已发布条款"
            />
          </div>

          <div v-if="result.warnings.length" class="flex flex-col gap-1">
            <div class="text-xs font-600 text-gray-600">提示与限制</div>
            <ul class="text-xs text-amber-700 list-disc pl-5">
              <li v-for="(warning, index) in result.warnings" :key="index">{{ warning }}</li>
            </ul>
          </div>

          <div v-if="result.unverified_claim_ids?.length" class="text-[11px] text-red-500">
            未核验论断：{{ result.unverified_claim_ids.join('、') }}
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
