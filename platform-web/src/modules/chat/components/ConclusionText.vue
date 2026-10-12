<script setup lang="ts">
/** 结论文本渲染：转义 + 段落标题高亮 + [Cn] 引用角标（点击反查右侧来源）。 */
import { computed } from 'vue'

const props = defineProps<{ text: string }>()
const emit = defineEmits<{ (e: 'cite', cid: string): void }>()

const html = computed(() => {
  let escaped = (props.text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  escaped = escaped.replace(/【([^】]+)】/g, '<div class="font-600 text-gray-800 mt-2 mb-1">【$1】</div>')
  escaped = escaped.replace(
    /\[(C\d+)\]/g,
    '<span class="cite-chip" data-cid="$1">$1</span>',
  )
  return escaped
})

function onClick(event: MouseEvent) {
  const target = event.target as HTMLElement
  const cid = target?.dataset?.cid
  if (cid) {
    emit('cite', cid)
  }
}
</script>

<template>
  <div class="leading-6 text-[13px] text-gray-700 whitespace-pre-wrap break-words" @click="onClick" v-html="html" />
</template>

<style scoped>
:deep(.cite-chip) {
  display: inline-block;
  margin: 0 2px;
  padding: 0 6px;
  border-radius: 8px;
  background: #eef6ff;
  color: #1677ff;
  font-size: 11px;
  cursor: pointer;
  border: 1px solid #cfe4ff;
}
:deep(.cite-chip:hover) {
  background: #d9eaff;
}
</style>
