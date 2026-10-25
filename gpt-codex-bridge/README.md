# GPT ↔ Codex Bridge

跨 ChatGPT/GPT 与 Codex 的**通用协作 Skill**，可用于代码、文档、研究和数据任务。它规范任务交接、结果回报与验收，不自动打通两个平台，也不继承任何单独项目的业务规则。

## 安装到 Codex（个人全局）

解压 ZIP 后，将整个 `gpt-codex-bridge` 目录放到 `~/.agents/skills/`：

```bash
mkdir -p ~/.agents/skills
cp -R gpt-codex-bridge ~/.agents/skills/
```

启动/重启 Codex 后，可以在适合的跨平台任务中直接指定 `$gpt-codex-bridge`。

## 在 ChatGPT 端使用

将 `CHATGPT_INSTRUCTIONS.md` 的正文复制到相应 **ChatGPT 项目 → 项目设置 → 项目指令**；如果项目支持文件上下文，再把 `SKILL.md` 加为项目文件。两者在本包中分别维护，不代表 ChatGPT 会自动读取 Codex 用户目录下的 Skill。

## 日常用法

1. 在 GPT 侧说明目标并要求“生成给 Codex 的 BRIDGE_TASK”。
2. 把任务复制到 Codex（或通过已授权、确实可用的连接方式交接）。
3. Codex 完成并返回 `BRIDGE_HANDOFF`。
4. 粘贴回 GPT，获取 `BRIDGE_REVIEW`：`PASS` 则结束，`FIX` 则只有一条修正任务，`UNVERIFIED` 则补最小证据。

## 内容结构

- `SKILL.md`：跨平台通用 Skill；Codex 侧主要入口。
- `CHATGPT_INSTRUCTIONS.md`：ChatGPT 项目侧简版指令。
- `references/examples.md`：代码与非代码任务的交接示例。

提醒：Codex 仍应遵守工作区已有的 `AGENTS.md`、项目约束及用户授权。本包不启用自动消息传递、提交或发布。
