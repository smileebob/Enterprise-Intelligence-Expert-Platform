<script setup lang="ts">
/** 知识管理（P12.4）：入库（内置数据集 + 幂等键）、任务进度、冲突裁决（F4）、案例审核队列。 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { newIdempotencyKey, request } from '@/shared/api/request'

const domainId = ref('chemical')
const ingestFiles = ['products.json', 'formulas.json', 'processes.json', 'experiment_cases.json', 'external_knowledge.json']
const ingesting = ref('')
const tasks = ref<Array<Record<string, unknown>>>([])
const conflicts = ref<Array<Record<string, unknown>>>([])
const reviewQueue = ref<Array<Record<string, unknown>>>([])

async function loadConflicts() {
  conflicts.value = await request.get<Array<Record<string, unknown>>>('/knowledge/conflicts', { params: { domain_id: domainId.value } })
}

async function loadReviewQueue() {
  try {
    reviewQueue.value = await request.get<Array<Record<string, unknown>>>('/knowledge/review-queue', { params: { domain_id: domainId.value } })
  } catch {
    reviewQueue.value = []
  }
}

onMounted(async () => {
  await Promise.all([loadConflicts(), loadReviewQueue()])
})

async function ingest(fileName: string) {
  ingesting.value = fileName
  try {
    const response = await fetch(`/data/${fileName}`)
    const payload = await response.json()
    const data = await request.post<{ task_id: string }>(
      '/knowledge/documents',
      { domain_id: domainId.value, source_name: fileName, payload },
      { headers: { 'Idempotency-Key': newIdempotencyKey() } },
    )
    ElMessage.success(`入库任务已提交：${data.task_id}`)
    pollTask(data.task_id)
  } catch {
    ElMessage.error('入库失败')
  } finally {
    ingesting.value = ''
  }
}

function pollTask(taskId: string) {
  const timer = window.setInterval(async () => {
    try {
      const task = await request.get<Record<string, unknown>>(`/knowledge/documents/${taskId}`)
      upsertTask(task)
      if (['ready', 'failed'].includes(String(task.status))) {
        window.clearInterval(timer)
        await loadConflicts()
      }
    } catch {
      window.clearInterval(timer)
    }
  }, 1200)
}

function upsertTask(task: Record<string, unknown>) {
  const index = tasks.value.findIndex((item) => item.task_id === task.task_id)
  if (index === -1) tasks.value = [task, ...tasks.value]
  else tasks.value[index] = task
}

const resolveForm = ref<Record<string, { resolution: string; note: string }>>({})

function claimText(conflict: Record<string, unknown>, key: 'internal_claim' | 'external_claim'): string {
  const claim = conflict[key] as { statement?: string } | undefined
  return claim?.statement || ''
}

function resolutionPrimary(conflict: Record<string, unknown>): string {
  const expected = conflict.expected_resolution as { primary?: string } | undefined
  return expected?.primary || ''
}

function formOf(conflictId: string): { resolution: string; note: string } {
  if (!resolveForm.value[conflictId]) {
    resolveForm.value[conflictId] = { resolution: 'internal_priority', note: '' }
  }
  return resolveForm.value[conflictId]
}

async function resolveConflict(conflictId: string) {
  const form = resolveForm.value[conflictId] || { resolution: 'internal_priority', note: '' }
  await request.post(`/knowledge/conflicts/${conflictId}/resolve`, form)
  ElMessage.success('已裁决（记录裁决依据）')
  await loadConflicts()
}

async function reviewCase(feedbackId: string, action: 'approve' | 'reject') {
  await request.post(`/knowledge/review/${feedbackId}`, { action })
  ElMessage.success(action === 'approve' ? '已通过（参与后续检索）' : '已驳回')
  await loadReviewQueue()
}
</script>

<template>
  <div class="h-full overflow-auto p-4 flex flex-col gap-4">
    <div class="panel">
      <div class="panel-title flex items-center justify-between">
        <span>知识入库（模拟数据集 · 结构感知切分 + 六级打标 + 向量化）</span>
        <el-tag size="small" type="warning" effect="plain">赛事模拟数据（is_mock）</el-tag>
      </div>
      <div class="p-3 flex flex-wrap gap-2">
        <el-button
          v-for="file in ingestFiles"
          :key="file"
          size="small"
          :loading="ingesting === file"
          @click="ingest(file)"
        >
          导入 {{ file }}
        </el-button>
      </div>
      <el-table v-if="tasks.length" :data="tasks" size="small" class="px-3 pb-3">
        <el-table-column prop="task_id" label="任务" width="180" />
        <el-table-column prop="source_name" label="文件" width="180" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'ready' ? 'success' : row.status === 'failed' ? 'danger' : 'info'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunks" label="知识块" width="80" />
        <el-table-column prop="conflicts_detected" label="检出冲突" width="90" />
        <el-table-column prop="pending_review" label="待审核" width="80" />
        <el-table-column prop="error" label="错误" />
      </el-table>
    </div>

    <div class="panel">
      <div class="panel-title">冲突裁决队列（F4：双方保留 + 裁决依据留痕）</div>
      <div class="p-3 flex flex-col gap-3">
        <div v-for="conflict in conflicts" :key="String(conflict.conflict_id)" class="border border-gray-100 rounded p-3">
          <div class="flex items-center gap-2 mb-1">
            <el-tag size="small" :type="conflict.status === 'resolved' ? 'success' : 'warning'">
              {{ conflict.status === 'resolved' ? '已裁决' : '待裁决' }}
            </el-tag>
            <span class="text-sm font-600">{{ conflict.conflict_id }} · {{ conflict.title }}</span>
            <span class="text-xs text-gray-400">{{ conflict.conflict_type }}</span>
          </div>
          <div class="text-xs text-gray-600">内部主张：{{ claimText(conflict, 'internal_claim') }}</div>
          <div class="text-xs text-gray-600">外部主张：{{ claimText(conflict, 'external_claim') }}</div>
          <div class="text-xs text-amber-600 mt-1">
            期望消解：{{ resolutionPrimary(conflict) }}
          </div>
          <div v-if="conflict.status !== 'resolved'" class="flex gap-2 mt-2">
            <el-input
              v-model="formOf(String(conflict.conflict_id)).resolution"
              size="small"
              placeholder="resolution"
              class="w-[240px]"
            />
            <el-input
              v-model="formOf(String(conflict.conflict_id)).note"
              size="small"
              placeholder="裁决依据备注"
            />
            <el-button size="small" type="primary" @click="resolveConflict(String(conflict.conflict_id))">提交裁决</el-button>
          </div>
          <div v-else class="text-xs text-green-600 mt-1">裁决结果：{{ JSON.stringify(conflict.resolution) }}</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title">案例审核队列（正样本回流 · BR-06 防污染）</div>
      <el-table :data="reviewQueue" size="small" class="p-3">
        <el-table-column prop="feedback_id" label="反馈" width="160" />
        <el-table-column prop="content_preview" label="方案内容（预览）" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button v-if="row.status === 'pending'" size="small" type="success" text @click="reviewCase(String(row.feedback_id), 'approve')">
              通过
            </el-button>
            <el-button v-if="row.status === 'pending'" size="small" type="danger" text @click="reviewCase(String(row.feedback_id), 'reject')">
              驳回
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
