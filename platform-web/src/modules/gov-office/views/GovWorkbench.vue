<script setup lang="ts">
/** 政务办公工作台（G-19）：政策解读、公文起草、文稿/总结/咨询三个工作区。 */
import { ref } from 'vue'
import { useUserStore } from '@/shared/stores/user'
import PolicyInterpretPanel from '../components/PolicyInterpretPanel.vue'
import DocumentDraftPanel from '../components/DocumentDraftPanel.vue'
import ReviewQueuePanel from '../components/ReviewQueuePanel.vue'
import VersionComparePanel from '../components/VersionComparePanel.vue'
import WritingDraftPanel from '../components/WritingDraftPanel.vue'

const userStore = useUserStore()
const activeTab = ref<'interpret' | 'document' | 'writing' | 'compare' | 'review'>('interpret')
</script>

<template>
  <div class="h-full overflow-auto p-4 flex flex-col gap-4">
    <div class="flex items-center justify-between">
      <div>
        <div class="text-base font-600 text-gray-800">政务办公工作台</div>
        <div class="text-xs text-gray-400 mt-0.5">
          政策解读 · 公文起草 · 文稿/总结/咨询（条款级证据与人工审核闭环）
        </div>
      </div>
      <el-tag type="warning" effect="plain" size="small">草稿箱 · 人工审核</el-tag>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="离线 / 抽取式草稿，需人工审核"
      description="本工作台只生成辅助草稿与条款原文摘录，不自动签发、不自动对外发布；涉及审批、资格、裁量或个案事项必须转人工确认。"
    />

    <div class="panel px-4 pb-4 pt-2">
      <el-tabs v-model="activeTab" class="gov-workbench-tabs">
        <el-tab-pane label="政策解读" name="interpret">
          <PolicyInterpretPanel />
        </el-tab-pane>
        <el-tab-pane label="公文起草" name="document">
          <DocumentDraftPanel />
        </el-tab-pane>
        <el-tab-pane label="文稿 / 总结 / 咨询" name="writing">
          <WritingDraftPanel />
        </el-tab-pane>
        <el-tab-pane label="政策版本对照" name="compare">
          <VersionComparePanel />
        </el-tab-pane>
        <el-tab-pane v-if="userStore.hasPerm('output.review')" label="待审草稿" name="review">
          <ReviewQueuePanel />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.gov-workbench-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}
</style>
