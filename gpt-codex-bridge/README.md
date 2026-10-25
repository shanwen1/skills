# GPT ↔ Codex Bridge

> One request → executor works and pushes evidence → GPT independently reviews → FIX triggers repair and re-review → PASS stops.

一个利用**现有 ChatGPT 会话 + Codex / Luna + 浏览器操作 + GitHub** 的通用 Agent Skill。它让执行 Agent 自动往返指定 ChatGPT 对话，由 GPT 独立审计真实成果；不是将多个角色伪装成同一个 Agent，也不要求为 ChatGPT 另开 API 会话。

## 状态

- **有实战反馈**：用户确认已经完成过 GPT 给出 FIX → Luna 修复并 Push → GPT 审查新 SHA，以及此前 Chrome 双向通信。
- **未独立覆盖**：换电脑/新 Agent 安装、完整中断恢复、所有浏览器工具与所有任务类型。
- **不包含**：通用 Chrome 驱动、永久授权、自动合并/发布/部署、无需登录的 ChatGPT API。

## 安装（Codex）

```bash
git clone https://github.com/shanwen1/skills.git
mkdir -p ~/.agents/skills
cp -R skills/gpt-codex-bridge ~/.agents/skills/
```

在 Codex 使用 `$gpt-codex-bridge`，提供本轮目标与要继续的 **ChatGPT 完整对话链接**。需先确认现有浏览器工具能在用户许可范围内**读取并发送该已登录对话的消息**，以及本轮是否授权 Git push。不能访问时应报告阻断，不模拟自动桥接。

GPT 端可以将 [CHATGPT_INSTRUCTIONS.md](CHATGPT_INSTRUCTIONS.md) 放入相关 ChatGPT 项目指令。无需创建新 ChatGPT 对话；执行 Agent 必须绑定用户指定的会话。

## 用法

1. 用户在有目标上下文的 ChatGPT 对话中确定任务与验收，授权执行 Agent 向该会话发送交接消息。
2. Codex/Luna 完成任务与自检；代码任务经允许只推独立 Git 分支。发送 `BRIDGE_HANDOFF` + 关联 ID + SHA。
3. GPT **自己**查 GitHub 或共享产物，返回 `BRIDGE_REVIEW`（可附 `BRIDGE_NEXT`）。
4. Codex/Luna 读取**完整且匹配**的回复：PASS 结束，FIX 修复并再次交付，UNVERIFIED 补证据。
5. 单轮固定上限与权限约束；异常停止，不以空闲或历史回复冒充当前回复。

协议细节见 [references/protocol.md](references/protocol.md)，浏览器接入见 [references/transport.md](references/transport.md)，恢复约束见 [references/recovery.md](references/recovery.md)。

## 对不同环境的要求

| 依赖 | 必须具备 | 不具备时 |
|---|---|---|
| 执行 Agent | 可执行本地任务与已有浏览器操作能力 | 停止或退回手动交接 |
| 已登录浏览器 | 获授权访问指定 ChatGPT 对话及发消息 | 不绕过登录/权限 |
| GPT 审查端 | 独立访问目标 GitHub 仓库/共享产物 | UNVERIFIED |
| Git push | 仅代码更新且用户当轮明确授权 | 不擅自上传代码 |
| 状态保留 | 本地仓库外最小记录 | 不能可靠恢复时停机 |

**隐私提示**：在已登录 Chrome 中自动化意味着浏览器工具可能具有广泛读取权限。优先使用已存在的细粒度授权；更大范围的浏览器权限需要单独征得用户同意。

**公开仓库不等于公开你的私有项目。** 本 Skill 不应包含客户名称、内部数据、私人对话 ID、GitHub 私有仓库代码或访问令牌。GitHub 仓库可见性由仓库所有者独立配置。
