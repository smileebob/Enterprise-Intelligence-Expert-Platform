<script setup lang="ts">
/** 我的记忆（PIPL 最小集）：查阅 / 更正 / 删除 / 可携带导出。 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import { request } from '@/shared/api/request'

interface MemoryItem {
  memory_id: string
  layer: string
  content: string
  confidence: number
  reuse_count: number
  status: string
  source_type: string
}

const items = ref<MemoryItem[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    items.value = await request.get<MemoryItem[]>('/memory/mine')
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function edit(item: MemoryItem) {
  try {
    const { value } = await ElMessageBox.prompt('更正记忆内容', '编辑记忆', { inputValue: item.content })
    if (value) {
      await request.put(`/memory/mine/${item.memory_id}`, { content: value })
      ElMessage.success('已更正（写 mem_event 留痕）')
      await load()
    }
  } catch {
    /* cancel */
  }
}

async function remove(item: MemoryItem) {
  try {
    await ElMessageBox.confirm('删除后不可恢复（合规要求留痕操作记录）', '删除记忆', { type: 'warning' })
    await request.delete(`/memory/mine/${item.memory_id}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* cancel */
  }
}

async function exportAll() {
  const data = await request.get<Record<string, unknown>>('/memory/mine/export')
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'my-memory-export.json'
  anchor.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出（可携带）')
}
</script>

<template>
  <div class="h-full overflow-auto p-4">
    <div class="panel">
      <div class="panel-title flex items-center justify-between">
        <span>我的记忆（用户级 · 仅本人可见 · PIPL 最小集）</span>
        <div class="flex gap-2">
          <el-button size="small" @click="load" :loading="loading">刷新</el-button>
          <el-button size="small" type="primary" plain @click="exportAll">导出（可携带）</el-button>
        </div>
      </div>
      <div class="p-3 flex flex-col gap-2">
        <div v-for="item in items" :key="item.memory_id" class="border border-gray-100 rounded p-3 flex items-start gap-3">
          <div class="flex-1">
            <div class="text-sm">{{ item.content }}</div>
            <div class="text-[11px] text-gray-400 mt-1">
              {{ item.memory_id }} · 来源 {{ item.source_type }} · 置信度 {{ item.confidence }} · 复用 {{ item.reuse_count }} 次
            </div>
          </div>
          <el-button size="small" text type="primary" @click="edit(item)">更正</el-button>
          <el-button size="small" text type="danger" @click="remove(item)">删除</el-button>
        </div>
        <div v-if="!items.length" class="text-xs text-gray-400 py-4 text-center">
          暂无个人记忆。采纳/修正智能体方案后，系统会自动沉淀偏好与经验（可在此查阅/更正/删除/导出）。
        </div>
      </div>
    </div>
  </div>
</template>
