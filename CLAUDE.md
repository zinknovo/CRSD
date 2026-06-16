# 合同审查 Skill 开发实验

## 背景

目标：为法律科技公司开发合同审查 Agent Skill。有法务背景，知道怎么审合同，需要把审查能力转化为 Agent Skill。

## Skill 开发核心原则（来自 Perplexity 实践）

### Skill ≠ 传统代码
- Skill 是文件夹，不是单个文件。复杂度就是特性
- 激活靠隐式模式匹配（description），不靠显式调用
- 每个 token 都要有信号量，能删就删
- Gotchas（陷阱/边界）是最高价值内容
- 如果一句话能解释清楚，模型早就知道了，删掉

### 渐进式加载（三级）
| 层级 | 内容 | 触发 |
|------|------|------|
| Index | name + description（~100 tokens） | 每次会话始终在线 |
| Load | 完整 SKILL.md 正文（~5,000 tokens） | Skill 被激活时 |
| Runtime | references/、scripts/ 等附件 | Agent 按需读取 |

### Description 是核心
- 以 "Load when..." 开头，是路由触发器，不是功能描述
- 控制在 50 词以内
- 微小措辞变化会产生巨大溢出效应，影响其他 Skill

### 每句话都要通过测试
"没有这个指令，Agent 会犯错吗？" —— 不能通过就删。

### 维护靠 Gotchas 飞轮
1. Agent 失败 → 加 gotcha
2. Skill 不该触发时触发 → 收紧 description
3. Skill 该触发但没触发 → 加关键词
4. 系统提示变更 → 检查冲突

---

## 合同审查 Skill 的架构思路

### 分工原则
- **SKILL.md** → 写审查逻辑：步骤、检查维度、输出格式
- **references/** → 定具体规则：法规条文、审查标准、条款模板、实务陷阱

### 审查维度（示例）
- 主体信息：签约方资质、授权是否完备
- 核心条款：价款/支付、交付/验收、违约责任、知识产权
- 风险条款：免责范围、赔偿责任上限、管辖约定
- 程序条款：生效条件、变更与解除、不可抗力

### 输出要求
- 按风险等级分级（严重/重要/一般/提示）
- 指出条款原文 + 风险说明 + 修改建议
- 可操作的建议，不是泛泛的"建议完善"

---

## 评估与优化闭环

### Eval 类型
1. **精确率**：Skill 是否只在合同审查场景触发？（准备非合同文本测试）
2. **召回率**：该查出的问题是否都查出了？（标注 ground truth 对比）
3. **端到端**：审查结果是否可用？（遗漏率、误报率、建议可操作性）

### 优化流水线
改 SKILL.md → 跑测试合同 → 对比预期结果 → 差异分析 → 调 description / 加 gotcha → 再跑

---

## 当前阶段要做什么

1. 选一个合同类型（如采购合同、保密协议）作为起点
2. 建 Skill 目录结构，写 SKILL.md + references/
3. 准备 2-3 份测试合同，标注已知问题
4. 用 Claude Code 跑一遍，记录效果
5. 进入 gotchas 飞轮迭代

## 参考资料
- Agent Skills 开放标准：https://github.com/agentskills/agentskills
- Perplexity Skills 实践：https://research.perplexity.ai/articles/designing-refining-and-maintaining-agent-skills-at-perplexity
- Claude Code 官方 Skill 编写指南
