<script setup lang="ts">
/** 六维评估卡（规则：得分/权重/依据/置信度/护栏/合规提示 + 人工确认）。 */
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { request } from '@/shared/api/request'
import type { EvalResult } from '@/shared/types/api'

const props = defineProps<{ result: EvalResult | null; highlightCid?: string }>()

const dims = computed(() => {
  if (!props.result?.dimensions) return []
  return Object.values(props.result.dimensions)
})

const radarPoints = computed(() => {
  const list = dims.value
  if (list.length !== 6) return ''
  const cx = 110
  const cy = 100
  const radius = 72
  return list
    .map((dim, index) => {
      const angle = (Math.PI / 3) * index - Math.PI / 2
      const ratio = Math.max(0, Math.min(100, dim.score)) / 100
      return `${cx + Math.cos(angle) * radius * ratio},${cy + Math.sin(angle) * radius * ratio}`
    })
    .join(' ')
})

const gridPolygon = computed(() => {
  const cx = 110
  const cy = 100
  const radius = 72
  return Array.from({ length: 6 })
    .map((_, index) => {
      const angle = (Math.PI / 3) * index - Math.PI / 2
      return `${cx + Math.cos(angle) * radius},${cy + Math.sin(angle) * radius}`
    })
    .join(' ')
})

const axisLabels = computed(() =>
  dims.value.map((dim, index) => {
    const angle = (Math.PI / 3) * index - Math.PI / 2
    return {
      code: dim.code,
      x: 110 + Math.cos(angle) * 88,
      y: 100 + Math.sin(angle) * 84,
    }
  }),
)

function flagNotice(flag: Record<string, unknown>): string {
  return String(flag.notice || '命中法规限制')
}

function scoreColor(score: number) {
  if (score >= 85) return '#16a34a'
  if (score >= 70) return '#1677ff'
  if (score >= 50) return '#d97706'
  return '#dc2626'
}

async function confirmResult() {
  if (!props.result) return
  await request.post(`/evaluations/${props.result.eval_id}/confirm`, { action: 'confirm', reason: '界面复核通过' })
  ElMessage.success('已确认评估结果')
}
</script>

<template>
  <div v-if="result" class="p-3">
    <div class="flex items-end justify-between mb-2">
      <div>
        <span class="text-2xl font-700" :style="{ color: scoreColor(result.total_score) }">{{ result.total_score }}</span>
        <span class="text-xs text-gray-400 ml-2">六维加权总分 / 100</span>
      </div>
      <el-tag size="small" type="info">{{ result.scenario }}</el-tag>
    </div>
    <div class="text-xs text-gray-500 mb-2">
      置信度 {{ result.confidence }} · 标准 {{ result.standard_version || '—' }} · 打分 {{ result.scored_by || 'rule' }}
    </div>

    <svg width="220" height="200" class="mx-auto block">
      <polygon :points="gridPolygon" fill="#f8fafc" stroke="#e2e8f0" />
      <polygon :points="radarPoints" fill="rgba(22,119,255,.18)" stroke="#1677ff" stroke-width="2" />
      <text
        v-for="label in axisLabels"
        :key="label.code"
        :x="label.x"
        :y="label.y"
        text-anchor="middle"
        font-size="11"
        fill="#64748b"
      >
        {{ label.code }}
      </text>
    </svg>

    <div class="flex flex-col gap-2 mt-2">
      <div v-for="dim in dims" :key="dim.code" class="text-xs">
        <div class="flex justify-between">
          <span class="text-gray-600">{{ dim.code }} {{ dim.name }}（权重 {{ dim.weight }}%）</span>
          <span class="font-600" :style="{ color: scoreColor(dim.score) }">{{ dim.score }}</span>
        </div>
        <el-progress :percentage="dim.score" :stroke-width="6" :show-text="false" :color="scoreColor(dim.score)" />
        <div v-if="dim.evidence_refs?.length" class="text-[11px] text-gray-400 mt-0.5">
          依据：{{ dim.evidence_refs.slice(0, 4).join('、') }}
        </div>
        <div v-for="(deduction, i) in dim.deductions || []" :key="i" class="text-[11px] text-orange-500">
          扣分：{{ deduction.reason }}（-{{ deduction.points }}）
        </div>
      </div>
    </div>

    <div v-if="result.guardrails?.length" class="mt-3">
      <el-alert
        v-for="(item, index) in result.guardrails"
        :key="index"
        :title="item"
        type="warning"
        :closable="false"
        class="mb-1"
        show-icon
      />
    </div>
    <div v-if="result.compliance_flags?.length" class="mt-2">
      <el-alert
        v-for="(flag, index) in result.compliance_flags"
        :key="index"
        :title="`合规强制提示：${flagNotice(flag as Record<string, unknown>)}`"
        type="error"
        :closable="false"
        class="mb-1"
        show-icon
      />
    </div>

    <div class="mt-3 flex justify-end">
      <el-button v-if="!result.confirmed_by" size="small" type="primary" plain @click="confirmResult">
        确认评估结果
      </el-button>
      <span v-else class="text-xs text-green-600">已由 {{ result.confirmed_by }} 确认</span>
    </div>
  </div>
  <div v-else class="p-4 text-xs text-gray-400">本回合未触发评估（诊断/对比类问题或证据不足）。</div>
</template>
