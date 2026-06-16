# CLAUDE.md 对照分析

## 对齐的地方 ✓

| 维度 | CLAUDE.md 现有内容 | 官方最佳实践 | 评估 |
|------|-------------------|-------------|------|
| **渐进式加载（三级）** | Index → Load → Runtime，给出了 token 预算 | 完全一致：discovery → activation → execution，建议 SKILL.md ≤ 500 行/5000 token | 完全对齐 |
| **"每句话都要通过测试"** | "没有这个指令，Agent 会犯错吗？" | "Would the agent get this wrong without this instruction?" | 几乎逐字对齐 |
| **Gotchas 飞轮** | Agent 失败→加 gotcha；不该触发→收紧 description；该触发没触发→加关键词 | Gotchas 是最高价值内容；Agent 犯错后立即加；description 优化有独立页面详述 | 思路一致，官方更结构化 |
| **输出模板优先于散文** | 未显式提及，但审查输出维度定义了清晰格式 | "模板比文字描述更可靠" | 隐性对齐 |
| **分工原则** | SKILL.md 写审查逻辑，references/ 定规则 | "核心指令放 SKILL.md，详细参考放 references/" | 完全对齐 |
| **评估维度** | 精确率、召回率、端到端 | 精确率（should-not-trigger 测试）、召回率（should-trigger 测试）、输出质量评估 | 概念对齐，但官方有完整方法论 |
| **渐进式披露的关键** | 未提及 | "告诉 Agent **何时**加载每个文件，而非泛泛说'见 references/'" | CLAUDE.md 遗漏了这一点 |

---

## CLAUDE.md 有但官方未强调的点

| 内容 | 说明 |
|------|------|
| **Description 以 "Load when..." 开头** | 官方的 phrasing 是 "Use this skill when..."，两者思路相同但措辞略有不同。CLAUDE.md 将 description 定位为"路由触发器"很精准 |
| **微小措辞变化产生巨大溢出效应** | 官方在 optimizing descriptions 页面深入讨论了这一点（near-miss 测试、过拟合避免），但其 best practices 页面没有展开 |
| **Skill 是文件夹不是单个文件** | 官方 quickstart 教程确实说"Skill 是一个文件夹包含 SKILL.md"，但没像 CLAUDE.md 那样强调"文件夹结构本身就是特性" |
| **复杂度就是特性** | 官方没有这个说法，但"coherent units"和"moderate detail"的讨论可视为对此的平衡观点 |

---

## 官方有但 CLAUDE.md 缺失的重要内容

### 缺失 1：从真实经验出发的方法论（★★★）
CLAUDE.md 说了"有法务背景，知道怎么审合同"，但没有结构化"如何把个人经验注入 Skill"的方法。官方给了两条明确路径：
- **路径 A（从实操提取）**：在对话中实际完成一个任务 → 记录成功步骤、修正点、I/O 格式、上下文
- **路径 B（从已有产物合成）**：喂入内部文档/runbook/schema/issue/code review/补丁历史

**对合同审查 Skill 的意义**：你手头的合同审查经验、审查清单、案例库，应该先喂进去再让 AI 写 Skill，而不是空泛让 AI 生成。

### 缺失 2："从真实执行中打磨"的具体方法（★★★）
CLAUDE.md 只说"跑一遍，记录效果"。官方给出了更精细的操作：
- 读取 Agent 的**执行轨迹**，不只是最终输出
- 识别浪费时间的原因：指令太模糊/不适用/选项太多没有默认值
- 更结构化的迭代方法在"Evaluating skills"页面完整展开

### 缺失 3：控制力度校准（★★）
CLAUDE.md 没有区分"什么时候应该给 Agent 自由，什么时候必须严格规定"。官方的指导：
- 灵活场景：多个方案都有效，任务容忍变化 → 解释 why
- 刚性场景：操作脆弱、一致性关键 → 精确指令，不准改

**对合同审查的意义**：某些审查步骤必须严格（如检查条款是否存在），某些可以灵活（如措辞建议）。

### 缺失 4：教方法 vs 教答案（★★）
官方强调 Skill 应"传授如何应对一类问题"而非"对特定实例输出什么"。CLAUDE.md 的审查维度（主体信息、价款、违约等）偏"检查什么"，没有"如何检查"的流程化指导。

### 缺失 5：Plan-Validate-Execute 模式（★★）
对合同审查这类多步骤+有风险的操作，官方推荐"先建中间计划→验证→再执行"。CLAUDE.md 没有这种执行安全保障。

### 缺失 6：验证循环（★★）
"做事→运行验证器→修复→再验证"。CLAUDE.md 的评估流程在高层次提到了"改→跑→对比→调"，但没有内嵌到 Skill 本身的执行流程中。

### 缺失 7：Description 的系统化测试方法（★★★）
CLAUDE.md 只说"收紧/加关键词"，官方给了完整方法论：
- 准备 20 条 eval queries（正负各半，含 near-miss）
- 每条跑 3 次算 trigger rate
- 60% 训练 / 40% 验证防过拟合
- 5 轮优化通常足够
- 有完整的 bash 脚本模板

### 缺失 8：脚本设计原则（★）
合同审查 Skill 可能涉及脚本（法规库检索、条款比对等），官方有系统的 Agent 友好脚本设计指南（--help、结构化输出、非交互式、幂等、dry-run 等）。

### 缺失 9：输出质量的结构化评估（★★★）
CLAUDE.md 提到"遗漏率、误报率、建议可操作性"，但没有展开：
- 如何设计 assertions（可验证的断言）
- 如何做 with/without skill 对比
- 记录 timing/token 数据计算 delta
- 人工复审机制

### 缺失 10：检查清单模式（★★）
多步骤审查流程适合用 checklist 让 Agent 跟踪进度，CLAUDE.md 没有应用这个模式。

---

## 行动建议（按优先级）

### 立即可做（不影响当前工作流）
1. **按"从真实经验出发"方法，写一份合同审查经验文档**，把你知道的审查要点、常见陷阱、法规条文整理成原始材料，喂给 LLM 生成 SKILL.md 初稿
2. **在 SKILL.md 中加 Gotchas 部分**，把法务实践中"违背外行人合理假设"的知识点写进去（如 "签字盖章" 不等于 "签字或盖章"）
3. **为审查维度加输出模板**，不只说"按风险等级分级"，给具体 Markdown 模板

### 后续迭代
4. 创建 **description 测试集**：准备 20 条 should/should-not trigger 查询
5. 准备 **2-3 个带标注的测试合同** + assertions，按 evaluating skills 的方法跑 with/without skill 对比
6. 审查流程加入 **checklist + validation loop** 模式

### 暂不需要
- 脚本设计原则 — 合同审查初期可能不需要 scripts/
- Plan-Validate-Execute — 等审查流程足够确定后再引入
