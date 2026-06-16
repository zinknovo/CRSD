# Agent Skills 最佳实践 —— 官方文档总结

来源：[https://agentskills.io/skill-creation/](https://agentskills.io/skill-creation/) 下的 5 个 tab 页面

## Tab 1: Quickstart（快速入门）

**核心要点：**

- Skill = 一个文件夹 + `SKILL.md` 文件，最小结构只需不到 20 行
- 前端元数据（`---` 包裹的 YAML）只有两个必需字段：`name`（与目录名一致）和 `description`
- 三段式工作：**Discovery**（扫描 name + description）→ **Activation**（匹配用户提问）→ **Execution**（加载正文执行）
- 跨平台通用：同一 Skill 可在 VS Code Copilot、Claude Code、OpenAI Codex 中使用

## Tab 2: Best Practices（最佳实践）★核心页面

### 从真实经验出发

- **最大陷阱**：让 LLM 空泛生成 Skill → 产出"妥善处理错误""遵循最佳实践"等废话
- 有效 Skill 必须注入**领域特有上下文**，来源有两种：
  - **从实操中提取**：在对话中完成一个真实任务，记录成功步骤、你的修正、输入/输出格式、你提供的项目特有上下文
  - **从已有产物合成**：内部文档/runbook/style guide、API 规范/schema/配置文件、code review 评论/issue 记录、版本控制历史（补丁和修复模式）、真实故障案例

### 用真实执行打磨

- 第一次产出的 Skill 通常需要改进。**拿真实任务跑一遍**，把全部结果（不只是失败）送回创建流程
- 读取 Agent 的**执行轨迹**，不只是最终输出 → 发现 Agent 在哪些不产生价值的地方浪费时间
- 常见问题：指令太模糊、指令不适用当前任务、选项太多没有默认值

### 明智使用上下文（token 预算）

- **只加 Agent 不知道的东西，删掉它已经知道的**
- 每段内容通过测试："没有这个指令，Agent 会犯错吗？" → 不能通过就删
- **设计内聚单元**：Skill 范围太窄 → 一个任务需激活多个 Skill（冲突风险）；太宽 → 难以精准触发
- **适度的细节**：过分全面的 Skill 反而有害 → Agent 难以提取相关部分。简洁的分步指引 + 一个能跑的示例，优于穷举文档
- **渐进式披露**：SKILL.md 控制在 500 行/5000 token 以内，详细参考材料放 `references/`。关键是指明**何时**加载每个文件

### 控制力度校准

- **灵活指令**：给自由 + 解释"为什么" — Agent 理解意图后能做出更好的上下文决策
- **规定性指令**：操作脆弱、一致性要求高、必须遵循特定顺序时才用
- **提供默认值，而非菜单**：多个选择中挑一个最推荐的，附带简要替代方案
- **教方法，不教答案**：Skill 应传授"如何处理一类问题"，而非"针对某个具体实例输出什么"

### 有效指令模式

| 模式 | 说明 |
| --- | --- |
| Gotchas（陷阱） | 最高价值内容：环境特有的事实，违背合理假设。Agent 犯错后立即添加到这部分 |
| 输出模板 | 模板比文字描述更可靠，Agent 善于模式匹配具体结构 |
| 检查清单 | 多步骤工作流用显式 checklist，Agent 跟踪进度 |
| 验证循环 | 做事→运行验证器→修复→再验证，直至通过 |
| Plan-Validate-Execute | 批量/破坏性操作先建中间计划，验证后再执行 |
| 捆绑可复用脚本 | Agent 重复写同样逻辑 → 写一次测试过的脚本放 scripts/ |

## Tab 3: Optimizing Descriptions（优化触发器）

**核心洞察：description 是 Skill 的触发机制，承担全部路由责任。**

### 怎么写好 description

- **祈使句式**："Use this skill when..."，不是"This skill does..."
- **关注用户意图而非实现**：描述用户想达成什么，不是 Skill 内部怎么做
- **宁可偏主动**：显式列出应用场景，包括用户不提领域关键词的情况
- **保持简洁**：通常几句话说清楚，规范硬限制 1024 字符
- **重要细节**：Agent 只在自己无法处理的任务时才找 Skill。简单一步操作（"读这个 PDF"）可能不触发即使描述完全匹配

### 如何系统测试 trigger 准确性

1. 准备 **eval_queries.json**：~20 条查询，各 8-10 条 should-trigger 和 should-not-trigger
2. **should-trigger 的变化维度**：措辞（正式/随意/拼写错误）、显式程度（提/不提领域关键词）、详细程度（精炼 vs 大量上下文）、复杂度
3. **should-not-trigger 中 "near-miss" 最有价值**：共享关键词但需求完全不同（如"分析 CSV" vs "把 CSV 导入数据库"）
4. **多次运行**：模型行为非确定，每个查询跑 3 次，计算 trigger rate。阈值 0.5 合理
5. **防止过拟合**：训练集 60% / 验证集 40%，随机混合正负样本，固定分割跨迭代比较

### 优化循环

评估 → 用训练集识别失败 → 修改 description → 重复。选验证集通过率最高的版本，而非最后一个版本。5 轮通常足够。

## Tab 4: Evaluating Skills（评估技能质量）

**判断 Skill 是否真的改善了输出，而非"似乎可以"。**

### 测试用例设计

每条用例：**prompt**（真实用户消息）+ **expected_output**（人类可读的成功描述）+ 可选 **input files**

- 起步 2-3 条，不要过早投资
- 变化 prompt：措辞、细节量、正式度
- 覆盖边界情况（畸形输入、模糊指令）
- 用真实上下文（文件路径、列名、个人语境）

### 运行 evals

核心模式：**每条测试跑两次** — 有 Skill 和没有 Skill（或旧版本）

- 输出目录结构：`iteration-N/eval-xxx/with_skill/` 和 `without_skill/`
- 记录 `timing.json`（token 数 + 耗时）
- 添加 **assertions**（可验证语句），在**看到第一轮输出后**添加 — 事前往往不知道"好"长什么样
- 好 assertion：具体、可观察、可计数；坏 assertion：太模糊或太刻板

### 评分与聚合

- 每个 assertion 给 PASS/FAIL + 具体证据
- 聚合到 `benchmark.json`：计算 delta（Skill 的成本 vs 收益）
- **关键分析**：删掉在两种配置下都总是通过/总是失败的 assertion；深入分析有 Skill 通过但无 Skill 失败的 assertion
- **人工复审必不可少**：assertion 只检查你想到的东西，人工补上未预见的维度

### 迭代改进

将三类信号（失败 assertion + 人工反馈 + 执行轨迹）连同当前 SKILL.md 交给 LLM，让它提出改进建议：

- 从反馈中**泛化**，不要为特定测试加窄补丁
- **保持 Skill 精瘦**：更少更好的指令往往优于穷举规则
- 解释 **why**
- 将重复工作**打包为脚本**

## Tab 5: Using Scripts（使用脚本）

### 一次性命令

- 利用生态系统工具：`uvx`(推荐)、`pipx`、`npx`、`bunx`、`deno run`、`go run`
- 固定版本号（`npx eslint@9.0.0`）
- 明确声明先决条件

### 自包含脚本（PEP 723 等）

各语言内联声明依赖，Agent 一条命令即可运行：

- **Python**: PEP 723 `# /// script` TOML 块 + `uv run`
- **Deno**: `npm:` / `jsr:` 导入
- **Bun**: 版本号直接写在 import 路径中
- **Ruby**: `bundler/inline`

### 面向 Agent 的脚本设计原则

| 原则 | 做法 |
| --- | --- |
| 避免交互式提示 | 所有输入通过 CLI flags / env / stdin，Agent 无法响应 TTY 弹窗 |
| 文档化 --help | Agent 通过 --help 了解接口，包含 flags + 用法示例 |
| 有用错误信息 | 指出哪里错了、期望什么、下一步试什么 |
| 结构化输出 | JSON/CSV/TSV → stdout，进度/警告 → stderr |
| 幂等性 | "创建如果不存在" 优于 "创建并报重复错误" |
| dry-run 支持 | 破坏性/有状态操作必须有 |
| 有意义的退出码 | 不同错误类型不同码，在 --help 中说明 |
| 安全默认 | 破坏性操作要求 --confirm / --force |
| 可预测输出大小 | Agent 平台会截断过长输出，支持 --offset 或 --output flag |