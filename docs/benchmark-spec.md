# 合同审查 Skill Benchmark 规格（v0.1）

> 基于业界/学界标准适配中文买卖合同审查场景，当前为**自建 benchmark**，无外部公开基准可直接采用。

---

## 一、为什么需要自建

| 公开基准 | 不适用的原因 |
|---------|------------|
| ContractEval（arXiv 2508.03080） | 英文商业合同，英美法体系，条款分类体系与中国民法典不对应 |
| CUAD（510份合同，41类条款） | 英文，span 抽取任务，非风险审查任务 |
| LegalBench（NeurIPS 2023） | 英文，含 CUAD/MAUD/ContractNLI，无中国法域 |
| LAiW / LexEval | 中文法律 LLM 基准，偏司法/法考/通用问答，非合同审查 |

**结论**：方法论通用（F2、分层召回、标注一致性），但条款体系 + 法条基准必须本地化。这正是本 benchmark 所做。

---

## 二、Benchmark 定义

### 2.1 任务定义

**任务**：给定一份中文买卖/采购/购销合同，输出结构化风险审查报告。

**输入**：合同全文 + 用户角色（买方/卖方/未明确）

**输出**：风险总览表（维度 × 风险等级 × 问题简述 × 条款位置）+ 逐项深入分析（条款原文 + 风险等级 + 法务分析 + 业务影响 + 修改建议 + 法规依据）

### 2.2 评估维度

参照 ContractEval 的多维评估框架，适配如下：

| 评估维度 | 对标 | 本项目实现 | 说明 |
|---------|------|----------|------|
| **风险识别（召回）** | ContractEval: clause-level risk detection | `run_eval.py` 语义召回率 | 头号指标——漏报是最严重失败 |
| **风险定级** | ContractEval: risk severity classification | `run_eval.py` 定级准确率 | 四级：严重/重要/一般/提示 |
| **条款定位** | CUAD: span extraction | `validate_grounding.py` 条款号落地 | CUAD 做 span-level，本项目做条款级 |
| **法条引用真实性** | — (业界无标准) | `validate_grounding.py` 法条落地 | 防幻觉：引用的法条必须真实存在 |
| **输出格式合规** | — | `validate_output.py` 格式门禁 | 硬门槛：总览表/五字段/免责声明 |
| **精确率（误报）** | ContractEval: "no relevant clause" error rate | 未独立实现 | 需人工标注负样本 |

### 2.3 核心指标

#### 主指标：F2 综合分

风险审查场景，漏报代价远大于误报（漏掉一个免责条款 = 签了有坑的合同），因此采用 **F2（β=2）** 而非 F1：

$$F_2 = \frac{5 \cdot P \cdot R}{4P + R}$$

| 优先级 | 指标 | 定义 | 目标 | 依据 |
|--------|------|------|------|------|
| ★★★ | **F2 综合分** | 精确率与召回的调和平均（召回加权 2×） | ≥ 0.75 | ContractEval 用 F1+F2；GPT-4.1 在 ContractEval 上 F1 仅 0.641，F2≥0.75 已是务实目标 |
| ★★★ | **严重/重要风险召回** | 严重+重要风险项中被命中的比例 | ≥ 95% | Sirion 采购 KPI：高风险条款识别率应接近 100%；漏掉严重风险 = 实质法律损失 |
| ★★☆ | **定级准确率** | 命中项中风险等级一致的比例 | ≥ 80% | 当前 iter-4 已达 81.5%，目标合理 |
| ★★☆ | **条款定位准确率** | 引用条款号与合同原文对应的比例 | ≥ 90% | CUAD 要求"完全覆盖标注 span"；本项目降为条款级，90% 可行 |
| ★☆☆ | **精确率** | 报出项中真风险的比例 | ≥ 70% | 太低则法务弃用；ContractEval GPT-4.1 精确率 0.67，0.70 是合理下限 |

#### 辅助指标

| 指标 | 定义 | 用途 | 阈值 |
|------|------|------|------|
| 标低率 | 定级低于 GT 的比例 | **危险信号**——严重风险被标低 | ≤ 5% |
| 标高率 | 定级高于 GT 的比例 | 偏保守，可接受 | 无硬限制 |
| 漏报率 | 1 - 召回率 | 驱动 gotcha 迭代 | 按严重度分层看 |
| 格式合规率 | 输出通过格式门禁的比例 | 硬门槛 | 100% |
| 护栏落地率 | 法条/条款号可验证的比例 | 防幻觉 | ≥ 90% |

---

## 三、数据集规格

### 3.1 规模（参照业界）

| 阶段 | 合同数量 | 说明 |
|------|---------|------|
| **MVP** | 5 份 | 覆盖 2-3 种子类型，够迭代用 |
| **正式** | 20-50 份 | 对标 ContractEval 内部验证集（20-50 份是常见起步规模） |
| **完整** | 100+ 份 | 对标 CUAD（510 份），长期目标 |

### 3.2 覆盖度要求

| 维度 | 要求 |
|------|------|
| 合同类型 | 设备采购、原材料采购、消费品采购、技术采购、通用货物买卖 |
| 合同来源 | 真实合同优先（脱敏后）；公开裁判文书中的合同次之 |
| 风险密度 | 每份合同 5-25 个标注风险项，覆盖四级风险等级 |
| 长度分布 | 短（<2页）、中（2-5页）、长（>5页）均有 |

### 3.3 标注规范

参照 ContractEval（法学生 70-100h 培训 + 律师监督）：

| 要求 | 规格 | 当前状态 |
|------|------|---------|
| 标注者 | ≥ 2 人独立标注 | ❌ 单标注者 |
| 标注一致性 | Cohen's Kappa ≥ 0.7 | ❌ 未测 |
| 标注指南 | 风险等级定义 + 示例 + 边界案例 | ✅ `risk-level-guide.md` |
| 争议解决 | 不一致项由第三名标注者裁决 | ❌ 未实现 |
| 冻结机制 | held-out 集不许在迭代中查看 | ❌ 未划分 |

### 3.4 标注字段

每个风险项标注：

```json
{
  "id": "C4-001",
  "risk_level": "严重",
  "dimension": "赔偿上限与免责范围",
  "clause_location": "第8.3条",
  "issue": "免责条款排除直接损失",
  "description": "第8.3条约定'卖方不承担任何间接损失及直接损失'，违反民法典第506条",
  "legal_basis": "民法典第506条",
  "severity_rationale": "排除直接损失 = 排除主要合同救济，属无效条款"
}
```

---

## 四、评估方法

### 4.1 匹配方式

| 方法 | 说明 | 当前 |
|------|------|------|
| **LLM 语义匹配** | LLM 判断 Skill 输出是否语义等价于 GT 项 | ✅ GLM-4-Plus |
| 人工校准 | LLM 裁判 vs 人工判定一致率 | ❌ 待做，目标 ≥ 85% |
| 字符串匹配 | 作为低成本快速检查 | ❌ 中文合同表述差异大，不适用 |

### 4.2 Baseline

| Baseline | 定义 | 目的 |
|----------|------|------|
| **纯 prompt** | 不加载 Skill，直接让同一模型审查合同 | 量化 Skill 本身的增量价值 |
| **上一轮** | 前一次 iteration 的得分 | 回归检测 |

### 4.3 评估流程

```
1. 在目标 Agent（Claude Code / API）中跑 Skill 于测试合同
2. LLM 语义匹配 → semantic_match.json
3. 自动计算：格式门禁 + 质量指标 + 回归对比 + 运营 KPI
4. 人工抽检：标低项 + 漏报项必须人工确认
5. 输出 benchmark report
```

---

## 五、报告模板

每次评估输出 `benchmark_report.json`，包含：

```json
{
  "version": "0.1",
  "timestamp": "2026-06-16T...",
  "dataset": { "contracts": 2, "total_gt_items": 30 },
  "scores": {
    "f2": 0.72,
    "recall": 0.794,
    "precision": null,
    "severity_stratified_recall": { "严重": "2/2", "重要": "8/10", "一般": "14/18" },
    "level_accuracy": 0.815,
    "clause_localization_accuracy": null,
    "grounding_rate": null
  },
  "auxiliary": {
    "deflation_rate": 0.05,
    "inflation_rate": 0.10,
    "format_compliance": 1.0,
    "grounding_clean_rate": null
  },
  "regression": {
    "vs_iteration": "iter-3",
    "recall_delta": +0.05,
    "level_accuracy_delta": +0.03,
    "newly_missed": [],
    "newly_fixed": ["C4-007", "C5-003"]
  },
  "calibration": {
    "llm_judge_vs_human_agreement": null,
    "annotator_kappa": null
  }
}
```

---

## 六、与业界标准对照

| 本项目指标 | ContractEval | CUAD | LegalBench-RAG |
|-----------|-------------|------|----------------|
| 语义召回率 | ✅ clause-level risk detection | ✅ span recall (P/R/F1) | — |
| 定级准确率 | ✅ risk severity | — | — |
| 条款定位 | ✅ clause localization | ✅ exact span match | ✅ Precision@k |
| 法条落地 | — (英美法无统一法典) | — | — |
| F2 综合分 | ✅ | — (用 F1) | — |
| 误报率 | ✅ "no relevant clause" error | — | — |
| 标注一致性 | ✅ expert + lawyer supervision | ✅ trained annotators | ✅ |

**独有项**：法条落地检查（`validate_grounding.py`）——中国成文法体系下的特殊需求，英美法 benchmark 无此维度。

---

## 七、落地路线

| 阶段 | 动作 | 优先级 |
|------|------|--------|
| **P0** | 跑一次纯 prompt baseline，量化 Skill 增量 | 🔴 |
| **P0** | F2 综合分加到 `run_eval.py`（一行代码） | 🔴 |
| **P1** | 数据集扩到 5 份（+3 份不同子类型） | 🟡 |
| **P1** | 现有 2 份合同做双人标注，测 Kappa | 🟡 |
| **P1** | LLM 裁判 vs 人工一致率验证 | 🟡 |
| **P2** | 数据集扩到 20+ 份，划分 held-out | ⚪ |
| **P2** | 精确率独立计算（需负样本标注） | ⚪ |

---

## 参考文献

- ContractEval: Benchmark for Commercial Contract Risk Evaluation (arXiv 2508.03080)
- CUAD: Contract Understanding Atticus Dataset (Hendrycks et al., 2021)
- LegalBench: A Collaboratively Built Benchmark for Measuring Legal Reasoning in Large Language Models (NeurIPS 2023)
- LegalBench-RAG: A Benchmark for Retrieval-Augmented Generation in the Legal Domain (arXiv 2408.10343)
- Sirion: How Procurement Teams Evaluate AI-Driven Contract Risk Detection
- Maxim: Building a Golden Dataset for AI Evaluation
- Evidently AI: LLM-as-a-Judge
