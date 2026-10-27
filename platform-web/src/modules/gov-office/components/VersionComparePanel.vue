<script setup lang="ts">
/** 政策版本对照：比较同一文件的条款新增、删除和修改。 */
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { comparePolicyVersions } from '../api/gov'
import type { PolicyVersionCompare } from '../types'
import { errorMessage } from '../presenters'

const form = reactive({
  docId: 'GOV-REG-GOV-INFO-DISCLOSURE',
  left: '国务院令第492号原版',
  right: '国务院令第711号修订版',
})
const loading = ref(false)
const error = ref('')
const result = ref<PolicyVersionCompare | null>(null)

async function compare() {
  if (!form.docId.trim() || !form.left.trim() || !form.right.trim()) {
    ElMessage.warning('请填写政策文件 ID 和两个版本标识')
    return
  }
  loading.value = true
  error.value = ''
  try {
    result.value = await comparePolicyVersions(
      form.docId.trim(),
      form.left.trim(),
      form.right.trim(),
    )
  } catch (err) {
    result.value = null
    error.value = errorMessage(err, '政策版本对照失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-4 items-start">
    <div class="panel">
      <div class="panel-title">版本对照条件</div>
      <div class="p-4 flex flex-col gap-3">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="政策文件 ID">
            <el-input v-model="form.docId" maxlength="128" />
          </el-form-item>
          <el-form-item label="左侧版本">
            <el-input v-model="form.left" maxlength="96" />
          </el-form-item>
          <el-form-item label="右侧版本">
            <el-input v-model="form.right" maxlength="96" />
          </el-form-item>
        </el-form>
        <el-button type="primary" :loading="loading" @click="compare">开始对照</el-button>
        <div class="text-[11px] text-gray-400">
          仅比较同属一个政策文件的版本；新增、删除和修改按条款定位。
        </div>
      </div>
    </div>

    <div class="panel min-h-[360px]">
      <div class="panel-title">条款差异</div>
      <div class="p-4 flex flex-col gap-3">
        <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
        <el-empty v-if="!result && !error" description="填写文件与版本后查看条款差异" />
        <template v-if="result">
          <div class="flex flex-wrap gap-2">
            <el-tag type="success">新增 {{ result.summary.added }}</el-tag>
            <el-tag type="danger">删除 {{ result.summary.removed }}</el-tag>
            <el-tag type="warning">修改 {{ result.summary.changed }}</el-tag>
            <el-tag type="info">未变 {{ result.summary.unchanged }}</el-tag>
          </div>
          <div class="text-xs text-gray-500">
            左：{{ result.left.version_label }}（{{ result.left.effective_from || '未标注' }} 至
            {{ result.left.effective_to || '长期' }}）
          </div>
          <div class="text-xs text-gray-500">
            右：{{ result.right.version_label }}（{{ result.right.effective_from || '未标注' }} 至
            {{ result.right.effective_to || '长期' }}）
          </div>

          <div v-if="result.added.length" class="flex flex-col gap-1">
            <div class="text-xs font-600 text-green-700">新增条款</div>
            <div v-for="clause in result.added" :key="`a-${clause}`" class="text-xs text-gray-700">
              {{ clause }}
            </div>
          </div>
          <div v-if="result.removed.length" class="flex flex-col gap-1">
            <div class="text-xs font-600 text-red-600">删除条款</div>
            <div v-for="clause in result.removed" :key="`r-${clause}`" class="text-xs text-gray-700">
              {{ clause }}
            </div>
          </div>
          <div v-if="result.changed.length" class="flex flex-col gap-2">
            <div class="text-xs font-600 text-amber-700">修改条款（{{ result.changed.length }}）</div>
            <div
              v-for="change in result.changed"
              :key="change.clause_no"
              class="border border-amber-100 bg-amber-50/40 rounded p-2"
            >
              <div class="text-xs font-600 text-gray-700">{{ change.clause_no }}</div>
              <div class="text-xs text-gray-500 mt-1 whitespace-pre-wrap">旧：{{ change.left }}</div>
              <div class="text-xs text-gray-700 mt-1 whitespace-pre-wrap">新：{{ change.right }}</div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
