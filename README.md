# 为了省 Token，我做了一个让 GPT 和 Codex 自主对话的 Skill...

> **GPT-Codex Bridge** · Stop copy-pasting. Let agents review each other's work.

我本人是一个普通的 plus 用户 ，跑 sol 半小时 token 就不够用了，于是乎就想着，诶 GPT 不是可以免费薅吗，那我就薅 GPT 的免费高级大脑，执行的活就让 luna 或者 DeepSeek Flash 简单执行就好，这样又可以白嫖高级模型，又可以用便宜的模型多干几次了，具体怎么做呢：

在 ChatGPT 里讨论需求、定好验收标准 → 复制给 Codex / Luna → 等它执行完 → 再把结果复制回 GPT → GPT 发现问题 → 我继续转发给 Codex……

**之前：****我不是在开发，我是在给两个 AI 当传话筒。**

每轮还得重复解释背景、贴 Diff、核对结果，既费时间，也容易消耗不必要的 Token。

**之后：有了`gpt-codex-bridge`**，利用现有的 ChatGPT 对话、浏览器操作能力和 GitHub，让执行 Agent 自动交接成果、让 GPT 独立审查；不通过就自动修复、重新提交、再次审查。

**我只负责提出目标和必要授权，不用每轮手动复制粘贴。**

<img width="842" height="561" alt="image" src="https://github.com/user-attachments/assets/c3834173-f98a-4e8b-926f-97048e7caead" /> 

## v0.3：我希望它不要做完一轮就自己停下

之前 GPT ↔ Luna 的自动往返已经跑通了，但还有一个实际痛点：**Luna 可能修完眼前一个问题就结束执行，整个用户目标却还没达成。**

所以这版把规则改成 **Goal-first（目标优先）**：启动时锁定完整目标和逐项验收；每次 FIX 都自动修复、重测、Push、交回 GPT；UNVERIFIED 优先自主补证；**只有最新成果满足整个目标的全部验收项，才允许 DONE**。不再默认修满 3 轮就停止，但无实质进展、超出授权或额度必须安全阻断。中断必须保存恢复检查点，不能冒充完成。

> Skill 本身不是常驻服务：能约束**当前 Agent 会话内**持续运行，但会话被平台结束或电脑休眠后，只有存在已授权的外部续跑机制才能重新唤醒，不能凭一个 Markdown Skill 保证通宵执行。

## 它能做什么？

```text
         你：在 ChatGPT 聊清楚目标
                      │
                      ▼
            Codex / Luna 执行
          读文件 → 改代码 → 自测
                      │
                      ▼
              Commit & Push
                      │
                      ▼
               GitHub 证据
                      │
                      ▼
        GPT 回到原 ChatGPT 对话审计
        ├─ PASS       → 任务完成，停止
        ├─ FIX        → 自动反馈 Codex 修复
        └─ UNVERIFIED → 补充证据，不盲改
                           │
                           └─ 自测 → Push 新 SHA → 复审
```

关键不在于让两个模型聊天，而在于**让它们围绕同一个目标，把事情做完**：

- **保留原对话上下文**：继续使用你最初提出目标的 ChatGPT 会话，不强制创建一个什么都不知道的新 GPT 对话。
- **减少人工搬运**：在用户授权、浏览器工具可用的情况下，Agent 自动发送交接消息并读取对应回复。
- **真正独立审查**：代码任务由 GPT 自己通过 GitHub 读取实际 Commit、Diff、源码及测试，而不只是相信 Codex 的“已完成”。
- **自动修复与复验**：GPT 返回 `FIX`，Codex 只修最优先的问题；提交新 SHA 后再次送审。
- **围绕完整目标持续推进**：只有独立验证全部用户验收项后的 `PASS + ACHIEVED` 才是完成；FIX 自动修、UNVERIFIED 自动补证，真实阻断才暂停，不擅自合并、发布或部署。

- 后面我就安心呼呼睡大觉，第二天上午来看最新的进展了～

## 什么场景值得用？

| 场景 | 过去的做法 | 用 Bridge 之后 |
|---|---|---|
| **AI 编程 / Bug 修复** | 把报错、Diff、修复结果来回粘贴 | 执行 → Push → GPT 审计 → FIX 自动回流 |
| **前端 UI / 用户旅程优化** | 自己每轮充当产品经理和验收员 | GPT 按原需求审查证据，Codex 定向修复 |
| **复杂需求 / 多轮重构** | 经常重新讲背景，担心 Agent 偏离目标 | 原会话保留目标和边界，每轮用 SHA 锁定成果 |
| **PR 前代码审查** | Agent 自检通过，还得人工找第二个 Agent 复核 | GPT 读取真实代码独立审查；不会自动合并 |
| **文档、研究和数据任务** | 在不同工具间重复解释要求和贴产物 | 双方能独立读取共享产物时，同样可交接与复核 |

尤其适合：**验收标准明确、需要反复迭代，但你不想一直盯着 Agent 的任务。**

## 为什么一定要通过 GitHub 审计？

因为“执行 Agent 说测试通过”不等于“需求真的实现”。

Bridge 在代码任务中将 **GitHub 作为共享的代码证据源**：

1. Codex 完成任务、自测后，在**本轮获得授权的独立分支**提交并 Push。
2. 交接携带 `run_id`、`turn_id`、基线 SHA、最新 SHA 和自测摘要。
3. GPT **亲自读取**远端提交、必要的上下文源码和测试，返回 `PASS / FIX / UNVERIFIED`。
4. Codex 只接受关联 ID 和 `reviewed_sha` 匹配的完整回复；`FIX` 则修改后以**新的 SHA**重新送审。
5. 审查通过后停止，不把“审查通过”自动理解为“授权发布”。

不涉及 Git 的任务也能使用，但必须提供双方都能独立核验的实际产物，不能凭空生成提交记录。

## 3 步开始使用

### 1. 安装 Skill

```bash
git clone https://github.com/shanwen1/skills.git
mkdir -p ~/.agents/skills
cp -R skills/gpt-codex-bridge ~/.agents/skills/
```

在 Codex 中使用 `$gpt-codex-bridge`。如客户端需要，重新打开会话以刷新 Skill 列表。

### 2. 准备原来的 ChatGPT 对话

在 ChatGPT 中明确你的目标与验收标准，复制**这条现有对话的完整 URL**。

还需确认：你的 Codex / Luna 已有获授权的浏览器工具，能够访问已登录的 ChatGPT 页面并发送、读取消息；审查端的 GPT 能访问目标 GitHub 仓库或共享产物。**安装 Skill 不会自动取得这些权限。**

### 3. 给 Codex 一条指令

```text
使用 $gpt-codex-bridge。

沿用这条 ChatGPT 对话中已经明确的目标和验收要求：
<完整 ChatGPT 对话 URL>

使用已授权的浏览器能力与该对话自动交接。
本地完成任务并自检；在我授权的范围内提交到独立 Git 分支。
让 GPT 对原定整体目标的全部验收项独立审查。
FIX 则自主修复并再次送审；UNVERIFIED 则优先补证。
只有最新 SHA 被独立验收且整体目标达成才标记 DONE；
目标未达成时在当前 Agent 会话内继续，不因单轮结束而停止。
遇到权限、预算或不可恢复故障保存检查点并报告。
不要合并 main、发布、部署或修改生产数据。
```

如果是首次连接，Skill 会要求先验证一次 `BRIDGE_PING / BRIDGE_PONG`，防止把“能打开网页”误当成“通信已经打通”。

ChatGPT 端可选用 [审查指令](gpt-codex-bridge/CHATGPT_INSTRUCTIONS.md)；具体字段见 [协议](gpt-codex-bridge/references/protocol.md)，浏览器适配见 [传输说明](gpt-codex-bridge/references/transport.md)，中断恢复见 [恢复规则](gpt-codex-bridge/references/recovery.md)。

## 真的跑通过吗？

**跑通过一轮真实的自动修复闭环。**

在一次实际项目协作中，我使用已有 Chrome 会话，让 Codex / Luna 向当前 ChatGPT 对话自动交接；GPT 独立审查 GitHub 代码、返回 `FIX`；Luna 自动修复、Push 新提交，GPT 再次审计新的 SHA。

这是使用者确认的真实运行经历，并非跨所有环境的兼容性保证。

### 使用前要知道的边界

- **不是新的 Agent 平台，也不是内置了浏览器驱动的应用。** Skill 复用你环境中实际可用的浏览器工具，不能凭 `SKILL.md` 凭空连接 Chrome。
- **无需默认使用独立 GPT API**，前提是你已有可通过浏览器交互的 ChatGPT 会话；但浏览器访问本身需要用户授权。
- **“省 Token”是出发点，不是已测量的节省比例。** 减少的是背景重复、长内容搬运和反复交接；独立审计照样消耗 Token。
- **跨电脑安装与完整异常恢复尚未独立验证。** 若连接失败、回复不完整或 SHA 不匹配，应停止而不是假装通过。
- **不包含永久授权**：不会自动获取 Cookies、绕过浏览器授权，或默认允许 Push、合并、部署、访问敏感数据。

---

**一句话：少当两个 AI 之间的传话筒，把精力留给定义问题和真正重要的判断。**

如果你也天天在 ChatGPT 和 Codex 之间来回复制，欢迎试试 **GPT-Codex Bridge**。
