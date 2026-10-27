# 支持性检查消融（G-26 部分）

- 运行时间（UTC）：2026-10-27T18:25:52.476531+00:00
- 输入：`model-20261027T182542Z.json`
- 方法：对模型 claims 做确定性支持检查；`direct/partial` 接受，`insufficient/error` 降级
- 口径：离线开发者自检，不是人工复核，不是正式选型

| 模型 | 原样通过 | 加支持检查通过 | 含不支持 claim 的样本 |
|---|---|---|---|
| qwen3.8-max | 13/15 | 9/15 | 4 |
| qwen3.8-flash | 13/15 | 9/15 | 4 |
| deepseek-v4.1-flash | 13/15 | 9/15 | 4 |

## 逐条

| case | 模型 | 原样 | claim 数 | 不支持 | 支持状态 | 加检查后 |
|---|---|---|---|---|---|---|
| GOV-PUB-001 | qwen3.8-max | PASS | 1 | 0 | ['partial'] | PASS |
| GOV-PUB-001 | qwen3.8-flash | PASS | 1 | 0 | ['partial'] | PASS |
| GOV-PUB-001 | deepseek-v4.1-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-002 | qwen3.8-max | PASS | 1 | 0 | ['partial'] | PASS |
| GOV-PUB-002 | qwen3.8-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-002 | deepseek-v4.1-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-003 | qwen3.8-max | PASS | 1 | 0 | ['partial'] | PASS |
| GOV-PUB-003 | qwen3.8-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-003 | deepseek-v4.1-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-004 | qwen3.8-max | PASS | 0 | 0 | [] | PASS |
| GOV-PUB-004 | qwen3.8-flash | PASS | 0 | 0 | [] | PASS |
| GOV-PUB-004 | deepseek-v4.1-flash | PASS | 0 | 0 | [] | PASS |
| GOV-PUB-005 | qwen3.8-max | PASS | 3 | 1 | ['partial', 'partial', 'insufficient'] | FAIL |
| GOV-PUB-005 | qwen3.8-flash | PASS | 4 | 2 | ['partial', 'partial', 'insufficient', 'insufficient'] | FAIL |
| GOV-PUB-005 | deepseek-v4.1-flash | PASS | 4 | 3 | ['insufficient', 'partial', 'insufficient', 'insufficient'] | FAIL |
| GOV-PUB-006 | qwen3.8-max | FAIL | 2 | 0 | ['partial', 'partial'] | FAIL |
| GOV-PUB-006 | qwen3.8-flash | FAIL | 3 | 0 | ['partial', 'partial', 'partial'] | FAIL |
| GOV-PUB-006 | deepseek-v4.1-flash | FAIL | 2 | 0 | ['partial', 'partial'] | FAIL |
| GOV-PUB-007 | qwen3.8-max | PASS | 3 | 0 | ['partial', 'partial', 'partial'] | PASS |
| GOV-PUB-007 | qwen3.8-flash | PASS | 3 | 0 | ['partial', 'partial', 'partial'] | PASS |
| GOV-PUB-007 | deepseek-v4.1-flash | PASS | 3 | 0 | ['partial', 'partial', 'partial'] | PASS |
| GOV-PUB-008 | qwen3.8-max | PASS | 1 | 1 | ['insufficient'] | FAIL |
| GOV-PUB-008 | qwen3.8-flash | PASS | 1 | 1 | ['insufficient'] | FAIL |
| GOV-PUB-008 | deepseek-v4.1-flash | PASS | 1 | 1 | ['insufficient'] | FAIL |
| GOV-PUB-009 | qwen3.8-max | PASS | 1 | 1 | ['insufficient'] | FAIL |
| GOV-PUB-009 | qwen3.8-flash | PASS | 1 | 1 | ['insufficient'] | FAIL |
| GOV-PUB-009 | deepseek-v4.1-flash | PASS | 1 | 1 | ['insufficient'] | FAIL |
| GOV-PUB-010 | qwen3.8-max | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-010 | qwen3.8-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-010 | deepseek-v4.1-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-011 | qwen3.8-max | PASS | 1 | 0 | ['direct'] | PASS |
| GOV-PUB-011 | qwen3.8-flash | PASS | 1 | 0 | ['direct'] | PASS |
| GOV-PUB-011 | deepseek-v4.1-flash | PASS | 1 | 0 | ['direct'] | PASS |
| GOV-PUB-012 | qwen3.8-max | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-012 | qwen3.8-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-012 | deepseek-v4.1-flash | PASS | 2 | 0 | ['partial', 'partial'] | PASS |
| GOV-PUB-013 | qwen3.8-max | PASS | 4 | 1 | ['partial', 'partial', 'insufficient', 'direct'] | FAIL |
| GOV-PUB-013 | qwen3.8-flash | PASS | 5 | 1 | ['partial', 'partial', 'insufficient', 'direct', 'partial'] | FAIL |
| GOV-PUB-013 | deepseek-v4.1-flash | PASS | 3 | 1 | ['partial', 'partial', 'insufficient'] | FAIL |
| GOV-PUB-014 | qwen3.8-max | FAIL | 0 | 0 | [] | FAIL |
| GOV-PUB-014 | qwen3.8-flash | FAIL | 0 | 0 | [] | FAIL |
| GOV-PUB-014 | deepseek-v4.1-flash | FAIL | 0 | 0 | [] | FAIL |
| GOV-PUB-015 | qwen3.8-max | PASS | 0 | 0 | [] | PASS |
| GOV-PUB-015 | qwen3.8-flash | PASS | 0 | 0 | [] | PASS |
| GOV-PUB-015 | deepseek-v4.1-flash | PASS | 0 | 0 | [] | PASS |

## 限制

- 支持检查是词面覆盖 + 否定风险规则，`partial` 仍可能是弱支持；不是语义证明。
- 模型回答中的 claim 可能被截断或 JSON 解析失败，按不支持处理。
- 该消融不重新调用模型，只检验已有输出能否被引用条款支撑。
