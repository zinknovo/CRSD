# 合同审查 Skill Eval Pipeline

## 概览

本目录是合同审查 Skill 的评估与优化闭环工具链，用于量化审查质量、检测回归、驱动 Skill 迭代。

## 数据集

| 合同 | 文件 | Ground Truth | 风险项数 |
|------|------|-------------|---------|
| 换电柜采购合同 | `tests/contracts/contract-4-换电柜采购.txt` | `tests/ground-truth/contract-4-换电柜采购.md` | ~17 |
| 化工品采购合同 | `tests/contracts/contract-5-化工品采购.txt` | `tests/ground-truth/contract-5-化工品采购.md` | ~13 |

**数据集状态**：当前仅 2 份合同，是已知短板。Obsidian `eval-benchmark-standards.md` 记录了扩到 20+ 的标准做法，待落地。

**Ground truth 来源**：人工标注（单标注者）。标准做法要求双人独立标注 + inter-annotator agreement，目前未实现。

## 指标体系

### 核心指标

| 指标 | 定义 | 当前目标 | 当前最优（iter-4） |
|------|------|---------|------------------|
| 语义召回率 | GT 风险项中被 Skill 命中的比例 | ≥85% | 79.4% |
| 定级准确率 | 命中项中风险等级与 GT 一致的比例 | ≥80% | 81.5% |
| 严重风险召回 | 严重风险项的召回 | ≥95% | - |
| 重要风险召回 | 重要风险项的召回 | ≥95% | - |

### 辅助指标

| 指标 | 定义 | 用途 |
|------|------|------|
| 标高（inflation） | Skill 定级高于 GT | 偏保守，可接受 |
| 标低（deflation） | Skill 定级低于 GT | **危险**，需优先修 |
| 漏报项 | 未命中的 GT 项 | 驱动 gotcha 迭代 |
| 格式门禁 | 输出结构是否符合要求 | 硬门槛，不过即不合格 |
| 护栏落地 | 法条/条款号/引文是否真实可查 | 软标记，防止幻觉 |

### 待落地指标

| 指标 | 状态 | 说明 |
|------|------|------|
| F2 综合分 | 未实现 | 召回加权的 F 值，风险场景首选。`run_eval.py` 已有召回+精确，加一行即可 |
| 精确率 | 未独立计算 | 当前靠 LLM 语义匹配间接衡量，无独立误报统计 |
| 标注一致性 | 未实现 | 需双人标注 + Cohen's Kappa |

## 工具链

```
evals/
├── evals.json              ← 断言式测试定义（must_include、min_findings 等）
├── run_eval.py             ← 核心指标计算（召回/定级/标高标低/漏报分析）
├── run_pipeline.py         ← 一键运行：门禁→质量→回归→KPI
├── validate_output.py      ← 格式硬门禁（总览表/五字段/免责声明/空泛建议）
├── validate_grounding.py   ← 护栏落地检查（法条/条款号/引文真实性）
└── operational_metrics.py  ← 运营 KPI（trace 数据，6 个指标）
```

## 使用方式

```bash
# 跑完整 pipeline（最新一轮 iteration）
uv run python evals/run_pipeline.py

# 指定 iteration 目录
uv run python evals/run_pipeline.py --workspace contract-reviewer-workspace/iteration-4

# 自动跑语义匹配（需 OPENAI_API_KEY，默认用 GLM-4-Plus）
uv run python evals/run_pipeline.py --with-llm

# 只算指标
uv run python evals/run_eval.py --report

# 指标 + 失败分析（生成 analysis_prompt.md 供 Agent 修 Skill）
uv run python evals/run_eval.py --all
```

## Pipeline 流程

```
[LLM] 0. 跑 skill 于测试合同        → workspace/iteration-N/eval-*/with_skill/outputs/review_output.md
[LLM] 1. 语义匹配 vs ground-truth   → workspace/iteration-N/eval-*/semantic_match.json
[auto] 2. 格式门禁 (validate_output)    每份输出结构是否合规
[auto] 3. 质量指标 (run_eval)           召回 / 定级 / 标高标低 / 漏报
[auto] 4. 回归对比                      本轮 vs 上一轮 benchmark.json
[auto] 5. 运营 KPI (operational_metrics) 6 个 KPI
```

Stage 0 手动执行（在 Claude Code 中跑 skill 保证真实 fidelity），Stage 1 可选自动（`--with-llm`），其余全自动化。

## 语义匹配机制

Skill 输出与 Ground Truth 的匹配不是精确字符串比对，而是通过 LLM 判断语义等价：

- 输入：GT 的每个风险项 + Skill 完整审查输出
- 输出：每个 GT 项是否被命中 + 风险等级是否一致
- 判定模型：GLM-4-Plus（通过 OpenAI 兼容 API）
- **已知问题**：LLM 裁判的一致性未验证。标准做法要求与人工标注比对达 ≥85% 一致率才可信。

## 业界基准参照

本项目没有直接使用任何外部公开 benchmark（CUAD/LegalBench/ContractEval 均为英文英美法合同），但方法论对齐如下：

| 业界基准 | 本项目对应 | 差异 |
|---------|----------|------|
| ContractEval（条款级风险识别） | 语义召回率 | ContractEval 是英文；本项目中文买卖合同 |
| CUAD（span 抽取） | 条款定位（validate_grounding） | CUAD 做 span-level，本项目做条款级 |
| LegalBench-RAG（检索评估） | 不适用 | 本项目不做检索 |
| F2 值（召回加权） | 待落地 | `run_eval.py` 已有 PR 数据 |

详细调研见 Obsidian `proj/CRSD/eval-benchmark-standards.md`。

## 历史结果

| 迭代 | 召回率 | 定级准确率 | 标高 | 标低 | 主要改动 |
|------|--------|----------|------|------|---------|
| iter-1 | - | - | - | - | 初始版本 |
| iter-2 | - | - | - | - | +gotcha 1-4 |
| iter-3 | - | - | - | - | +gotcha 5-7 |
| iter-4 | 79.4% | 81.5% | - | - | +gotcha 8（空白分类） |

（iter-1/2/3 的详细数字未记录在代码中，需查 `contract-reviewer-workspace/iteration-*/benchmark.json`）

## 迭代闭环

```
改 SKILL.md / references / gotchas
       ↓
在 Claude Code 中跑 skill 于测试合同（Stage 0）
       ↓
run_pipeline.py（Stage 1-5）
       ↓
看 benchmark.json + analysis_prompt.md
       ↓
差异分析（漏报→加 gotcha，标低→修 risk-level-guide，误触发→收 description）
       ↓
回归验证 → 回到第一步
```
