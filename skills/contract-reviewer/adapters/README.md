# Agent 可插拔适配指南

本 Skill 的核心审查逻辑（SKILL.md 正文 + references/）是模型无关的，可在任何 Agent 框架中使用。

## 可移植性分层

```
references/          ← 100% 可移植（纯法律知识，无平台依赖）
SKILL.md 正文        ← 95% 可移植（审查流程 + 陷阱 + 自检）
SKILL.md frontmatter ← 需适配（触发方式、工具声明因框架而异）
scripts/             ← 需适配（可观测采集方式因部署而异）
```

## 已提供适配

| 接入方式 | 文件 | 适用场景 |
|---------|------|---------|
| Claude Code 原生 | SKILL.md（原始） | `.claude/skills/` 自动发现 |
| OpenAI Function Calling | adapters/openai-function.json | 任何兼容 OpenAI API 的框架（LangChain、LlamaIndex、Semantic Kernel 等） |
| System Prompt 注入 | adapters/system-prompt.md | 无工具调用的纯对话场景（直接注入 system message） |

## 国产模型通用

GLM/Qwen/DeepSeek/Kimi/MiniMax 的 API 均兼容 OpenAI function calling 格式，一份 `openai-function.json` 通用。

## 接入新 Agent 框架

1. **确认触发方式**：本 Skill 通过 description 隐式匹配触发，需将 description 注册到框架的路由/tool 发现机制
2. **确认工具调用**：Skill 声明 `Read`/`Grep`/`Glob`，映射到框架的文件读取能力
3. **确认 references 加载**：Skill 按需读取 `references/` 目录下的文件，映射到框架的文件系统访问
4. **确认可观测**：如需 trace，映射 `scripts/` 的采集逻辑到框架的日志/回调机制

## 可观测适配

| 部署方式 | 采集方式 | 参考 |
|---------|---------|------|
| Claude Code | `.claude/settings.local.json` hooks | 内置 5 事件 hook |
| OpenAI API | Python 中间件包装 | `scripts/openai-observer.py` |
| 自研 Agent | 框架回调/日志 | 按 `scripts/` 中 telemetry schema 适配 |

详见 `scripts/openai-observer.py`。
