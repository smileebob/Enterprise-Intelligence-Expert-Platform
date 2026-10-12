<script setup lang="ts">
/** 评估中心（P12.3）：三个金值算例一键对拍 + 历史评估列表。 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { newIdempotencyKey, request } from '@/shared/api/request'
import EvalCard from '@/modules/evaluation/components/EvalCard.vue'
import type { EvalResult } from '@/shared/types/api'

const demoCases = [
  { key: 'demo-6608-eu', label: '算例1：涤纶无氟防水·出口欧盟（期望 83.8）', products: ['TF-WR-6608'], scenario: '出口合规敏感', market: '欧盟' },
  { key: 'demo-6612-eu', label: '算例2：涤纶有机氟防水·出口欧盟（期望 69.3，CS-05）', products: ['TF-WR-6612'], scenario: '出口合规敏感', market: '欧盟' },
  { key: 'demo-2210-premium', label: '算例3：涤纶高端柔软·内销（期望 84.6 / 83.1）', products: ['TF-SS-2210'], scenario: '高端品牌性能优先', market: '内销' },
]

const current = ref<EvalResult | null>(null)
const history = ref<EvalResult[]>([])
const loading = ref('')

async function loadHistory() {
  try {
    history.value = await request.get<EvalResult[]>('/evaluations', { params: { domain_id: 'chemical', limit: 20 } })
  } catch {
    history.value = []
  }
}

onMounted(loadHistory)

async function runCase(item: (typeof demoCases)[number], scenario = item.scenario) {
  loading.value = item.key + scenario
  try {
    const result = await request.post<EvalResult>(
      '/evaluations',
      {
        domain_id: 'chemical',
        object_type: 'solution',
        object_ref: { demo_case: item.key, products: item.products, target_market: item.market },
        scenario,
      },
      { headers: { 'Idempotency-Key': newIdempotencyKey() } },
    )
    current.value = result
    ElMessage.success(`总分 ${result.total_score}`)
    await loadHistory()
  } finally {
    loading.value = ''
  }
}

async function openEval(evalId: string) {
  current.value = await request.get<EvalResult>(`/evaluations/${evalId}`)
}
</script>

<template>
  <div class="h-full overflow-auto p-4 flex gap-4">
    <div class="flex-1 flex flex-col gap-4 min-w-0">
      <div class="panel">
        <div class="panel-title">金值算例对拍（08 §八；误差 ≤0.1 为通过）</div>
        <div class="p-3 flex flex-col gap-2">
          <div v-for="item in demoCases" :key="item.key" class="flex items-center gap-2">
            <span class="text-sm flex-1">{{ item.label }}</span>
            <el-button size="small" type="primary" plain :loading="loading === item.key + item.scenario" @click="runCase(item)">
              运行
            </el-button>
            <el-button
              v-if="item.key === 'demo-2210-premium'"
              size="small"
              plain
              @click="runCase(item, '默认')"
            >
              默认画像
            </el-button>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">历史评估（标准版本可回溯 · BR-09 不自动重算）</div>
        <el-table :data="history" size="small" class="p-3" height="320">
          <el-table-column prop="eval_id" label="评估 ID" width="180" />
          <el-table-column prop="scenario" label="场景" width="130" />
          <el-table-column prop="total_score" label="总分" width="80" />
          <el-table-column prop="confidence" label="置信度" width="80" />
          <el-table-column prop="scored_at" label="时间" width="180" />
          <el-table-column label="" width="90">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openEval(String(row.eval_id))">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="w-[420px] panel shrink-0 overflow-auto">
      <div class="panel-title">评估卡</div>
      <EvalCard :result="current" />
    </div>
  </div>
</template>
