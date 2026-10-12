<script setup lang="ts">
/** 安全审计台（P12.4）：按 trace_id / Hook 类型 / 时间查询，复原全链路。 */
import { onMounted, ref } from 'vue'
import { request } from '@/shared/api/request'

interface AuditRow {
  log_id: number
  trace_id: string
  actor_id: string | null
  action: string
  resource: string | null
  hook_type: string | null
  decision: string | null
  detail: Record<string, unknown>
  ts: string
}

const filters = ref({ trace_id: '', hook_type: '', limit: 50 })
const rows = ref<AuditRow[]>([])
const loading = ref(false)

const hookTypes = ['input', 'retrieval', 'tool', 'output', 'memory_write']

async function query() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: filters.value.limit }
    if (filters.value.trace_id) params.trace_id = filters.value.trace_id
    if (filters.value.hook_type) params.hook_type = filters.value.hook_type
    rows.value = await request.get<AuditRow[]>('/audit/logs', { params })
  } finally {
    loading.value = false
  }
}

onMounted(query)

function decisionType(decision: string | null) {
  if (decision === 'block') return 'danger'
  if (decision === 'mask' || decision === 'confirm') return 'warning'
  return 'success'
}
</script>

<template>
  <div class="h-full overflow-auto p-4 flex flex-col gap-4">
    <div class="panel">
      <div class="panel-title">审计查询（五类 Hook 决策全量留痕 · trace_id 贯穿复原）</div>
      <div class="p-3 flex gap-2 items-center flex-wrap">
        <el-input v-model="filters.trace_id" placeholder="trace_id（消息右下角可复制）" class="w-[320px]" size="small" />
        <el-select v-model="filters.hook_type" placeholder="Hook 类型" clearable size="small" class="w-[160px]">
          <el-option v-for="item in hookTypes" :key="item" :label="item" :value="item" />
        </el-select>
        <el-input-number v-model="filters.limit" :min="10" :max="200" size="small" />
        <el-button size="small" type="primary" :loading="loading" @click="query">查询</el-button>
      </div>
      <el-table :data="rows" size="small" height="520" class="p-3">
        <el-table-column prop="ts" label="时间" width="180" />
        <el-table-column prop="trace_id" label="trace_id" width="150">
          <template #default="{ row }">
            <el-text truncated class="w-[130px]">{{ row.trace_id }}</el-text>
          </template>
        </el-table-column>
        <el-table-column prop="hook_type" label="Hook" width="110" />
        <el-table-column prop="action" label="动作" width="170" />
        <el-table-column prop="decision" label="决策" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="decisionType(row.decision)" effect="plain">{{ row.decision }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="actor_id" label="主体" width="120" />
        <el-table-column label="详情">
          <template #default="{ row }">
            <span class="text-xs text-gray-500">{{ JSON.stringify(row.detail) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
