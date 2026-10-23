<script setup lang="ts">
/** 领域装配与权限（P12.4）：领域实例上下线/回滚、角色授权矩阵、工具集。 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import 'element-plus/es/components/message/style/css'
import { useUserStore } from '@/shared/stores/user'
import { newIdempotencyKey, request, requestV2 } from '@/shared/api/request'

interface DomainItem {
  domain_id: string
  name: string
  status: string
  version: string
  config_rev: number
  active_revision_id?: string | null
  draft_revision_id?: string | null
}

interface RevisionItem {
  revision_id: string
  config_rev: number
  change_kind: string
}

const userStore = useUserStore()
const domains = ref<DomainItem[]>([])
const roles = ref<Array<{ role_id: string; name: string; permissions: string[] }>>([])
const tools = ref<Array<{ name: string; description: string; permission: string; risk_level: string }>>([])

async function load() {
  domains.value = await requestV2.get<DomainItem[]>('/domains')
  if (userStore.hasPerm('role.manage')) {
    roles.value = await request.get<typeof roles.value>('/admin/roles')
    const data = await request.get<{ tools: typeof tools.value }>('/admin/tools')
    tools.value = data.tools
  }
}

onMounted(load)

function writeHeaders(row: DomainItem) {
  return {
    'If-Match': `"${row.config_rev}"`,
    'Idempotency-Key': newIdempotencyKey(),
  }
}

async function publish(row: DomainItem) {
  try {
    await requestV2.post(`/domains/${row.domain_id}/publish`, {}, { headers: writeHeaders(row) })
    ElMessage.success(`${row.domain_id} 已上线（底座代码零改动）`)
  } finally {
    await load()
  }
}

async function rollback(row: DomainItem) {
  try {
    const revisions = await requestV2.get<RevisionItem[]>(`/domains/${row.domain_id}/revisions`)
    const target = revisions.find((item) => item.revision_id !== row.active_revision_id)
    if (!target) {
      ElMessage.warning('暂无可回滚的历史版本')
      return
    }
    await requestV2.post(
      `/domains/${row.domain_id}/rollback`,
      { revision_id: target.revision_id },
      { headers: writeHeaders(row) },
    )
    ElMessage.success(`已回滚到 revision ${target.config_rev}`)
  } catch {
    ElMessage.warning('回滚未完成，请刷新后重试')
  } finally {
    await load()
  }
}

async function offline(row: DomainItem) {
  try {
    await requestV2.post(`/domains/${row.domain_id}/offline`, {}, { headers: writeHeaders(row) })
    ElMessage.success('已下线')
  } finally {
    await load()
  }
}
</script>

<template>
  <div class="h-full overflow-auto p-4 flex flex-col gap-4">
    <div class="panel">
      <div class="panel-title">领域实例装配（F7：配置即领域）</div>
      <el-table :data="domains" size="small" class="p-3">
        <el-table-column prop="domain_id" label="领域" width="140" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'online' ? 'success' : row.status === 'offline' ? 'danger' : 'info'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button v-if="userStore.hasPerm('domain.publish') && (row.status !== 'online' || row.draft_revision_id)" size="small" type="success" text @click="publish(row)">
              上线
            </el-button>
            <el-button v-if="userStore.hasPerm('domain.publish')" size="small" text @click="rollback(row)">
              回滚
            </el-button>
            <el-button v-if="userStore.hasPerm('domain.publish') && row.status === 'online'" size="small" type="danger" text @click="offline(row)">
              下线
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="roles.length" class="panel">
      <div class="panel-title">角色 × 权限矩阵（FR-9-04）</div>
      <el-table :data="roles" size="small" class="p-3">
        <el-table-column prop="name" label="角色" width="160" />
        <el-table-column prop="role_id" label="ID" width="160" />
        <el-table-column label="权限点">
          <template #default="{ row }">
            <el-tag v-for="perm in row.permissions" :key="perm" size="small" class="mr-1 mb-1" effect="plain">
              {{ perm }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="tools.length" class="panel">
      <div class="panel-title">工具注册表（MCP 同构契约 · 权限声明 · 风险等级）</div>
      <el-table :data="tools" size="small" class="p-3">
        <el-table-column prop="name" label="工具" width="200" />
        <el-table-column prop="description" label="描述" />
        <el-table-column prop="permission" label="权限点" width="180" />
        <el-table-column prop="risk_level" label="风险" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.risk_level === 'dangerous' ? 'danger' : 'success'">{{ row.risk_level }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
