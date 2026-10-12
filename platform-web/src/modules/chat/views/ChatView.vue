<script setup lang="ts">
/** 对话台（P12.2）：会话列 + 消息区（SSE 流式 + 引用角标 + 澄清交互 + 反馈）+ 右侧面板（步骤/来源/评估）。 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import { useChatStore } from '@/modules/chat/stores/chatStore'
import ConclusionText from '@/modules/chat/components/ConclusionText.vue'
import StepsTimeline from '@/modules/chat/components/StepsTimeline.vue'
import EvalCard from '@/modules/evaluation/components/EvalCard.vue'
import { request } from '@/shared/api/request'
import type { ChatMessageItem, ConflictSummary, EvalResult } from '@/shared/types/api'
import type { StepItem } from '@/modules/chat/stores/chatStore'

const store = useChatStore()
const input = ref('')
const rightTab = ref('steps')
const highlightCid = ref('')

const domainOptions = [
  { value: 'chemical', label: 'AI 化学家（主领域）' },
  { value: 'investment', label: 'AI 产业招商专家（副领域）' },
]

const quickQueries = [
  '客户要涤纶面料做无氟防水，要求耐洗50次不明显下降，无氟能做到吗？',
  '客户做出口欧盟的户外面料，要高性能防水，能用我们的C6有机氟TF-WR-6612吗？',
  '客户反馈用了我们的氨基硅油后白色涤纶泛黄，是什么原因、怎么解决？',
  '网上有评测说嵌段硅油柔软度就7分，你们TF-SS-2210凭什么说9.5分？',
]

const currentMessages = computed<ChatMessageItem[]>(() => store.messages)

onMounted(async () => {
  await store.loadSessions()
  if (store.sessions.length) {
    await store.openSession(store.sessions[0].session_id)
  }
})

watch(
  () => store.streamText,
  () => {
    rightTab.value = rightTab.value
  },
)

async function sendText(text: string) {
  const value = text.trim()
  if (!value || store.streaming) return
  if (!store.currentSessionId) {
    await store.newSession(store.domainId)
  }
  input.value = ''
  await store.send(value)
}

async function newSession() {
  await store.newSession(store.domainId)
  ElMessage.success('已创建新会话')
}

function onCite(cid: string) {
  rightTab.value = 'sources'
  highlightCid.value = cid
  setTimeout(() => (highlightCid.value = ''), 2500)
}

const feedbackText = ref('')
async function submitFeedback(message: ChatMessageItem, rating: 'adopted' | 'modified' | 'rejected') {
  if (rating === 'modified') {
    try {
      const { value } = await ElMessageBox.prompt('请填写修正意见（回流为纠错规则）', '修正方案', {
        inputValue: feedbackText.value,
        inputPlaceholder: '例如：该场景需补充交联剂用量说明',
      })
      feedbackText.value = value || ''
    } catch {
      return
    }
  }
  await store.feedback(message.message_id, rating, feedbackText.value)
  ElMessage.success({ adopted: '已采纳（正样本回流案例库，待审核）', modified: '已修正（回流纠错规则）', rejected: '已否决（负样本沉淀）' }[rating])
}

function degradeBadge(level: string) {
  if (level === 'L0') return ''
  if (level === 'L1') return '简化分析'
  if (level === 'L2') return 'AI 分析暂不可用'
  if (level === 'L3') return '语义检索暂不可用'
  return '离线回放'
}

async function loadEvalDetail(evalRef: string) {
  rightTab.value = 'eval'
  if (evalRef) {
    try {
      store.evalResult = await request.get<EvalResult>(`/evaluations/${evalRef}`)
    } catch {
      /* ignore */
    }
  }
}

function conflictsOf(message: ChatMessageItem): ConflictSummary[] {
  return message.conflicts || []
}

interface MessageMeta {
  demo_mode?: string
  annotations?: string[]
  steps?: StepItem[]
}

function metaOf(message: ChatMessageItem): MessageMeta {
  return (message.meta || {}) as MessageMeta
}

const feedbackLabels: Record<string, string> = { adopted: '采纳', modified: '修正', rejected: '否决' }
function feedbackLabel(feedback: string | null): string {
  return feedback ? feedbackLabels[feedback] || feedback : ''
}
</script>

<template>
  <div class="h-full flex">
    <!-- 会话列 -->
    <div class="w-56 border-r border-gray-200 bg-white flex flex-col">
      <div class="p-3 border-b border-gray-100 flex flex-col gap-2">
        <el-select v-model="store.domainId" size="small">
          <el-option v-for="option in domainOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-button size="small" type="primary" plain @click="newSession">＋ 新建会话</el-button>
      </div>
      <div class="flex-1 overflow-auto py-1">
        <div
          v-for="session in store.sessions"
          :key="session.session_id"
          class="px-3 py-2 mx-2 my-1 rounded cursor-pointer text-xs"
          :class="session.session_id === store.currentSessionId ? 'bg-blue-50 text-blue-600' : 'text-gray-600 hover:bg-gray-50'"
          @click="store.openSession(session.session_id)"
        >
          <div class="font-500 truncate">{{ session.title || session.session_id }}</div>
          <div class="text-[11px] text-gray-400">{{ session.domain_id }} · {{ session.updated_at?.slice(5, 16) }}</div>
        </div>
      </div>
    </div>

    <!-- 对话区 -->
    <div class="flex-1 min-w-0 flex flex-col bg-gray-50">
      <div class="flex-1 overflow-auto p-4 flex flex-col gap-3">
        <div v-if="!currentMessages.length && !store.streaming" class="mt-10 text-center text-gray-400 text-sm">
          <div class="mb-3">向领域智能体提问（演示：内部优先 / 冲突标注 / 合规例外 / 逐条引用）</div>
          <div class="flex flex-col gap-2 items-center">
            <el-tag
              v-for="query in quickQueries"
              :key="query"
              class="cursor-pointer max-w-[560px] whitespace-normal text-left"
              @click="sendText(query)"
            >
              {{ query }}
            </el-tag>
          </div>
        </div>

        <div v-for="message in currentMessages" :key="message.message_id" class="flex gap-2" :class="message.role === 'user' ? 'justify-end' : ''">
          <div class="max-w-[720px] panel px-4 py-3" :class="message.role === 'user' ? 'bg-blue-600 text-white' : ''">
            <div v-if="message.role === 'user'" class="text-sm whitespace-pre-wrap">{{ message.content }}</div>
            <template v-else>
              <div class="flex items-center gap-2 mb-2">
                <span class="text-xs font-600 text-gray-500">领域智能体</span>
                <el-tag v-if="degradeBadge(message.degrade_level)" size="small" type="warning" effect="plain">
                  {{ degradeBadge(message.degrade_level) }}
                </el-tag>
                <el-tag v-if="metaOf(message).demo_mode && metaOf(message).demo_mode !== 'live'" size="small" type="info" effect="plain">
                  离线模式（{{ metaOf(message).demo_mode }}）
                </el-tag>
              </div>
              <ConclusionText :text="message.content" @cite="onCite" />

              <!-- 冲突标注卡片 -->
              <div v-for="conflict in conflictsOf(message)" :key="conflict.conflict_id" class="mt-2 border border-amber-200 bg-amber-50 rounded p-2">
                <div class="text-xs font-600 text-amber-700 mb-1">
                  冲突标注 · {{ conflict.conflict_id }}（{{ conflict.title }}）
                </div>
                <div class="text-[12px] text-gray-700">内部结论：{{ conflict.internal }}</div>
                <div class="text-[12px] text-gray-700">外部观点：{{ conflict.external }}</div>
                <div v-if="conflict.must_note" class="text-[12px] text-amber-700 mt-1">裁决说明：{{ conflict.must_note }}</div>
              </div>

              <!-- 标注与操作 -->
              <div v-if="metaOf(message).annotations?.length" class="mt-2">
                <div
                  v-for="(note, index) in metaOf(message).annotations"
                  :key="index"
                  class="text-[11px] text-gray-400"
                >
                  · {{ note }}
                </div>
              </div>
              <div class="flex items-center gap-2 mt-2 flex-wrap">
                <el-button
                  v-if="!message.feedback"
                  size="small"
                  type="success"
                  text
                  @click="submitFeedback(message, 'adopted')"
                >
                  采纳
                </el-button>
                <el-button v-if="!message.feedback" size="small" type="warning" text @click="submitFeedback(message, 'modified')">
                  修正
                </el-button>
                <el-button v-if="!message.feedback" size="small" type="danger" text @click="submitFeedback(message, 'rejected')">
                  否决
                </el-button>
                <el-tag v-else size="small" type="info">已{{ feedbackLabel(message.feedback) }}</el-tag>
                <el-button v-if="message.eval_ref" size="small" text type="primary" @click="loadEvalDetail(message.eval_ref)">
                  查看评估卡
                </el-button>
                <span class="text-[11px] text-gray-300">trace: {{ message.trace_id?.slice(0, 12) }}</span>
              </div>
            </template>
          </div>
        </div>

        <!-- 流式进行中 -->
        <div v-if="store.streaming" class="max-w-[720px] panel px-4 py-3">
          <div class="text-xs font-600 text-gray-500 mb-2">领域智能体正在分析…</div>
          <ConclusionText :text="store.streamText || '…'" @cite="onCite" />
        </div>
      </div>

      <!-- 澄清追问卡片 -->
      <div v-if="store.clarify" class="mx-4 mb-2">
        <el-alert type="info" :closable="false" show-icon>
          <template #title>为给出可落地方案，请补充：{{ store.clarify.missing_slots.join('、') }}</template>
          <div class="text-xs mt-1">{{ store.clarify.question }}</div>
        </el-alert>
      </div>
      <div v-if="store.errorMessage" class="mx-4 mb-2">
        <el-alert type="error" :title="store.errorMessage" :closable="false" show-icon />
      </div>

      <!-- 输入区 -->
      <div class="border-t border-gray-200 bg-white p-3">
        <div class="flex gap-2">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="描述客户需求，例如：涤纶面料，要求柔软手感好且耐洗50次不明显下降（Enter 发送）"
            @keydown.enter.exact.prevent="sendText(store.clarify ? `补充：${input}` : input)"
          />
          <el-button type="primary" class="h-auto" :loading="store.streaming" @click="sendText(store.clarify ? `补充：${input}` : input)">
            {{ store.clarify ? '补充信息' : '发送' }}
          </el-button>
        </div>
      </div>
    </div>

    <!-- 右侧面板 -->
    <div class="w-[380px] border-l border-gray-200 bg-white flex flex-col">
      <el-tabs v-model="rightTab" class="px-3 pt-2 flex-1 min-h-0">
        <el-tab-pane label="思考步骤" name="steps">
          <div class="overflow-auto max-h-[calc(100vh-160px)]">
            <StepsTimeline :steps="store.steps" />
          </div>
        </el-tab-pane>
        <el-tab-pane label="来源引用" name="sources">
          <div class="overflow-auto max-h-[calc(100vh-160px)] px-1">
            <div
              v-for="citation in store.citations"
              :key="citation.citation_id"
              class="border rounded p-2 mb-2"
              :class="highlightCid === citation.citation_id ? 'border-blue-400 bg-blue-50' : 'border-gray-100'"
            >
              <div class="flex items-center gap-2">
                <span class="chip bg-blue-50 text-blue-600">{{ citation.citation_id }}</span>
                <el-tag size="small" :type="citation.source_type === 'internal' ? 'success' : citation.source_type === 'external' ? 'info' : 'warning'" effect="plain">
                  {{ { internal: '内部专有', external: '外部通用', confirmed: '人工确认', stated: '当事方陈述', ai_inferred: 'AI 推断' }[citation.source_type] || citation.source_type }}
                </el-tag>
                <span v-if="citation.category === 'compliance'" class="chip bg-red-50 text-red-500">法规</span>
                <span class="text-[11px] text-gray-400">{{ citation.record_id }}</span>
              </div>
              <div class="text-[12px] text-gray-600 mt-1">{{ citation.source_ref }}</div>
              <div class="text-[11px] text-gray-400">置信度 {{ citation.confidence ?? '—' }}</div>
            </div>
            <div v-if="!store.citations.length" class="text-xs text-gray-400 p-2">（本回合暂无引用）</div>
          </div>
        </el-tab-pane>
        <el-tab-pane label="评估卡" name="eval">
          <div class="overflow-auto max-h-[calc(100vh-160px)]">
            <EvalCard :result="store.evalResult" :highlight-cid="highlightCid" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>
