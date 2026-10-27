<script setup lang="ts">
/** 事实材料 / 素材编辑器：文本与来源引用成对，动态增删。 */
import type { DraftFact } from '../types'

const props = withDefaults(
  defineProps<{
    modelValue: DraftFact[]
    addLabel?: string
    textPlaceholder?: string
    maxRows?: number
  }>(),
  {
    addLabel: '新增一条',
    textPlaceholder: '填写事实或素材原文（可追责、可核对）',
    maxRows: 20,
  },
)

const emit = defineEmits<{ 'update:modelValue': [value: DraftFact[]] }>()

function addRow() {
  if (props.modelValue.length >= props.maxRows) return
  emit('update:modelValue', [...props.modelValue, { text: '', source_ref: '' }])
}

function removeRow(index: number) {
  emit(
    'update:modelValue',
    props.modelValue.filter((_, position) => position !== index),
  )
}

function updateRow(index: number, key: keyof DraftFact, value: string) {
  emit(
    'update:modelValue',
    props.modelValue.map((row, position) =>
      position === index ? { ...row, [key]: value } : row,
    ),
  )
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <div
      v-for="(row, index) in modelValue"
      :key="index"
      class="border border-gray-100 rounded p-2 flex flex-col gap-2 bg-gray-50/40"
    >
      <div class="flex items-start gap-2">
        <el-input
          :model-value="row.text"
          type="textarea"
          :rows="2"
          size="small"
          :placeholder="textPlaceholder"
          @update:model-value="updateRow(index, 'text', $event)"
        />
        <el-button size="small" text type="danger" @click="removeRow(index)">删除</el-button>
      </div>
      <el-input
        :model-value="row.source_ref"
        size="small"
        placeholder="来源引用（文件名/页码/链接/会议记录编号）"
        @update:model-value="updateRow(index, 'source_ref', $event)"
      />
    </div>
    <div class="flex items-center gap-2">
      <el-button size="small" plain :disabled="modelValue.length >= maxRows" @click="addRow">
        {{ addLabel }}
      </el-button>
      <span class="text-[11px] text-gray-400">共 {{ modelValue.length }} 条</span>
    </div>
  </div>
</template>
