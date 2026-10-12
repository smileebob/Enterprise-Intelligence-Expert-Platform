-- 企业级领域专家 Agent 平台 · PostgreSQL 16 + pgvector 参考 DDL（对照技术方案 04 §4.2）
-- 说明：实际建表由 `alembic upgrade head` 完成；本脚本供 DBA 审查/手工初始化参考，幂等。
-- 全部演示数据为赛事模拟数据（is_mock）。

CREATE EXTENSION IF NOT EXISTS vector;

-- 知识块：三级检索要素（向量/全文/标量过滤）+ 六级分级字段 + 引用锚点
CREATE TABLE IF NOT EXISTS kb_chunk (
    chunk_id        TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES kb_document(doc_id),
    seq             INT  NOT NULL,
    content         TEXT NOT NULL,
    tokens          TEXT NOT NULL DEFAULT '',        -- jieba 预分词结果（空格分隔）
    content_tsv     TSVECTOR,                        -- to_tsvector('simple', tokens)
    embedding       VECTOR(1024),
    embedding_model TEXT NOT NULL DEFAULT '',        -- 向量版本化，防换模型混用
    source_type     TEXT NOT NULL CHECK (source_type IN
                    ('raw','internal','external','stated','ai_inferred','confirmed','deprecated')),
    category        TEXT,                            -- 合规类证据打 'compliance'（CS-05 反转依据）
    confidence      NUMERIC(4,3),
    permission_level SMALLINT NOT NULL DEFAULT 1,
    domain_id       TEXT NOT NULL,
    source_ref      TEXT,                            -- 可追溯定位（文档名/段落/记录 ID）
    record_id       TEXT DEFAULT '',                 -- 领域记录 ID 锚点（如 EXP-2201 / EXT-STD-006）
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/ready/deprecated/rejected
    meta            JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunk_embedding ON kb_chunk USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_chunk_tsv   ON kb_chunk USING gin (content_tsv);
CREATE INDEX IF NOT EXISTS idx_chunk_scope ON kb_chunk (domain_id, source_type, status);

-- 审计日志：只追加，五类 Hook 决策全量留痕
CREATE TABLE IF NOT EXISTS gov_audit_log (
    log_id     BIGSERIAL PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    actor_id   TEXT,
    action     TEXT NOT NULL,
    resource   TEXT,
    hook_type  TEXT CHECK (hook_type IN ('input','retrieval','tool','output','memory_write')),
    decision   TEXT CHECK (decision IN ('pass','mask','block','confirm')),
    detail     JSONB DEFAULT '{}',
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_trace ON gov_audit_log (trace_id, ts DESC);

-- 其余表（kb_document / kb_conflict / mem_item / mem_event / eval_standard / eval_result /
-- gov_user|role|permission / dom_instance / growth_* / chem_* / inv_* / chat_* / agent_checkpoint）
-- 由 alembic 迁移统一创建，字段语义见技术方案 04 §4.1 映射表。
