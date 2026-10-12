"""自定义业务指标（技术方案 06 §6.6）——统一注册于 prometheus_client 默认注册表。

命名遵循 Prometheus 规范（snake_case + 单位后缀）；标签避免高基数（不落 user_id/session_id）。
指标写入失败不阻断业务（调用点均为纯内存操作，无需额外防护）。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---- 体验 ----
CHAT_TTFT = Histogram(
    "chat_ttft_seconds",
    "对话首 token 时延（秒，含检索，不含澄清等待）",
    buckets=(0.5, 1, 2, 3, 5, 10, 30, 60, 120),
)

# ---- 编排 ----
AGENT_NODE_DURATION = Histogram(
    "agent_node_duration_seconds",
    "编排节点耗时（秒）",
    ["node"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60),
)
AGENT_REPLAN = Counter("agent_replan_total", "自检未通过触发的重规划次数")
DEGRADE_LEVEL = Counter("degrade_level_total", "回合结束降级层级计数", ["level"])

# ---- LLM ----
LLM_TOKENS = Counter("llm_tokens_total", "LLM token 用量", ["model", "type"])
LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM 调用耗时（秒）",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 240),
)
LLM_FAILURES = Counter("llm_failures_total", "LLM 调用失败/降级计数", ["alias"])

# ---- Hook / 安全 ----
HOOK_DECISIONS = Counter("hook_decisions_total", "五类 Hook 决策计数", ["stage", "decision"])
CONFLICT_DETECTED = Counter("conflict_detected_total", "内外部冲突检出计数", ["scenario"])

# ---- 业务 ----
SOLUTION_FEEDBACK = Counter("solution_feedback_total", "方案反馈计数", ["rating"])
CITATION_COVERAGE = Gauge("citation_coverage_ratio", "带引用结论占比（最近回合）")
EVAL_CALIBRATION = Counter("eval_calibration_total", "评估大模型校准计数", ["applied"])
