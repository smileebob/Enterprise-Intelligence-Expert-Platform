<script setup lang="ts">
/** 政策条款引用列表：展示文号/版本/生效区间，source_url 可点击回跳原文。 */
import type { PolicyCitation } from '../types'

defineProps<{ citations: PolicyCitation[]; emptyText?: string }>()

function effectiveRange(citation: PolicyCitation): string {
  const from = citation.effective_from || '未标注'
  const to = citation.effective_to || '长期'
  return `${from} 至 ${to}`
}
</script>

<template>
  <div v-if="citations.length" class="flex flex-col gap-2">
    <div
      v-for="citation in citations"
      :key="citation.citation_id"
      class="border border-gray-100 rounded p-2 bg-gray-50/60"
    >
      <div class="text-sm text-gray-800 font-500">{{ citation.title }}</div>
      <div class="text-xs text-gray-500 mt-0.5">
        <span v-if="citation.document_number">{{ citation.document_number }} · </span>
        <span>版本 {{ citation.version_label }}</span>
        <span> · 生效 {{ effectiveRange(citation) }}</span>
      </div>
      <div class="flex items-center gap-2 mt-1">
        <span class="text-[11px] text-gray-400 break-all">条款 {{ citation.citation_id }}</span>
        <a
          v-if="citation.source_url"
          :href="citation.source_url"
          target="_blank"
          rel="noopener noreferrer"
          class="text-xs text-blue-600 hover:underline"
        >
          查看来源
        </a>
        <span v-else class="text-[11px] text-gray-400">未登记原文链接</span>
      </div>
    </div>
  </div>
  <div v-else class="text-xs text-gray-400">{{ emptyText || '暂无引用条款' }}</div>
</template>
