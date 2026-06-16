# CRSD — Contract Review Skill Development

合同审查 Agent Skill 的开发实验。目标：把合同审查能力从"依赖强模型 + 好提示词"变成"任何模型装上 Skill 都能审到及格线以上"。

## 项目结构

```
skills/contract-reviewer/
├── SKILL.md                  审查流程 + gotcha（核心）
├── SKILL.en.md               English version
├── references/
│   ├── purchase-contract.md  12维度审查规则 + 8个常见风险模式
│   ├── risk-level-guide.md   四级风险判定标准
│   ├── legal-basis.md        民法典高频法条原文
│   └── clause-templates.md   6类条款修改模板
├── adapters/                 Agent 可插拔适配（OpenAI function calling / system prompt）
└── scripts/                  可观测中间件（telemetry + observer）

evals/                        自动化评估管线（5阶段 pipeline）
tests/                        测试合同 + ground truth
docs/                         技术文档 + benchmark 规格
```

## 四维框架

| 维度 | 回答什么 | 状态 |
|------|---------|------|
| **内容** | Skill 怎么写——三级加载、文件分工、gotcha | ✅ v0.5，10.5 条 gotcha |
| **评估** | 怎么测——eval pipeline、护栏、成绩 | ✅ 5阶段 pipeline，召回 97.1%，定级 ~94% |
| **观测** | 怎么看——结构化报告、迭代历史 | ✅ pipeline_report.json，5 轮迭代记录 |
| **韧性** | 怎么改怎么扛——飞轮、杠杆、已知薄弱点 | ✅ 四杠杆 + 飞轮闭环可跑 |

详见 [技术总览文档](docs/contract-reviewer-skill-overview.md)。

## 跑评估

```bash
# 安装依赖
uv sync

# 跑完整 pipeline（格式门禁 → 语义匹配 → 质量指标 → 回归 → KPI）
uv run python evals/run_pipeline.py

# 只跑格式门禁
uv run python evals/validate_output.py

# 只跑法条落地校验
uv run python evals/validate_grounding.py
```

详见 [evals/README.md](evals/README.md)。

## 当前成绩

iter-5，2 份买卖合同，34 个 ground truth 项：

| 指标 | 有 Skill | 无 Skill (baseline) |
|------|---------|-------------------|
| 召回率 | 97.1% | 100% |
| 定级准确率 | ~94% | 94.1% |
| 严重风险召回 | 100% | 100% |
| 重要风险召回 | 100% | 100% |

在强模型上 Skill 的增量主要在结构化和一致性；核心价值在弱模型保底——这部分待测。

## 技术栈

- Python 3.12 + uv
- 评估管线：纯 Python + LLM 语义匹配
- Skill 适配：OpenAI function calling / system prompt 注入 / 通用 JSON

## License

MIT
