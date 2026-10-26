# 目标级状态与中断恢复（v0.3）

**恢复的对象是整体用户目标，不只是最后一条 GPT 消息。** Skill 指示现有 Agent 使用本机可用的轻量持久化机制；它本身不创建后台调度器、不保证关机后自动运行。

## 最小持久化状态（放在业务仓库外）

优先使用本机已有状态持久化；若没有，可将以下 JSON 放在 `$XDG_STATE_HOME/gpt-codex-bridge/` 或用户家目录下仅当前用户可读的目录，原子写入：

```json
{
  "run_id": "<稳定的用户目标 ID>",
  "goal": "<最短完整目标>",
  "acceptance": [
    {"id": "A1", "criterion": "<验收项>", "status": "UNKNOWN", "evidence": ""}
  ],
  "conversation_url": "<用户指定原 ChatGPT 对话 URL>",
  "repo": "<owner/repo>",
  "branch": "<已授权隔离分支>",
  "base_sha": "<本轮基线 SHA>",
  "head_sha": "<本轮远端最新 SHA>",
  "turn_id": "<本次交接 ID>",
  "state": "ACTIVE",
  "send_status": "NONE",
  "last_reply_id": null,
  "reviewed_sha": null,
  "verdict": null,
  "fix_count": 0,
  "last_progress": "<最近一次实际进展摘要>",
  "blocked_reason": null,
  "runtime_capability": "current-session-only"
}
```

状态字段枚举：
- `state`: `ACTIVE | WAIT_REVIEW | EVIDENCE | FIXING | DONE | BLOCKED | PAUSED | INTERRUPTED`；
- `send_status`: `NONE | SEND_UNCERTAIN | SENT | REVIEW_RECEIVED`。
- `runtime_capability`: `current-session-only` 或**已真实验证**的外部 runner/续跑机制；不要虚构已启用的常驻能力。

不保存账号密码、Cookie、Chrome Profile、客户文档原文、私人访问令牌。目标文本也不得包含敏感业务细节；用本地受保护的必要引用代替。

## 模型选型踩坑：Skill 规则不保证模型一定照做

执行模型是长循环可靠性的一部分，不能把所有提前中断都归因于桥接协议。

- 长任务开始前，用低风险任务实际验证完整多轮链路：执行 → GPT 审查 → FIX → 自动修复 → 新 SHA → 复审。
- 若模型反复在目标未完成时自行总结退出，先检查是否有明确阻断；没有阻断且状态仍为 ACTIVE 时，优先切换到已实测能继续执行的模型/运行器，而不是持续叠加更长的提示词。
- **用户在当前配置下的实测**：Luna 反复提前结束；DeepSeek Flash 收到一次桥接说明后仍持续工作。当前场景优先选 DeepSeek Flash；这是特定环境的经验，不应推广成对所有模型版本的绝对结论。

## 每轮自动续行，不以“模型回答完了”冒充完成

1. 每个阶段变更前后落盘一份状态快照：`goal`、验收项状态、远端 SHA、消息交接 ID、已完成工作和**下一项唯一动作**。
2. 在 Agent 会话仍运行时，`ACTIVE` 表示**马上继续执行**，`FIXING` 代表立即修复，`EVIDENCE` 代表马上补证据，`WAIT_REVIEW` 代表等待本轮 GPT 完整回复；不是输出总结然后等用户唤醒。
3. 收到 `PASS` 仍要确认 GPT 证明了**整体目标全部验收项**而非局部正确。未满足则继续，而不将状态写为 `DONE`。
4. 只有真正 `DONE`、用户要求 `PAUSED`，或出现不可自动解决的 `BLOCKED` 才能结束当前任务。因 Agent Turn 自然结束而尚未完成时写 `INTERRUPTED`，必须保留可恢复检查点。
5. 如果宿主已经支持、启用且授权了可靠的续跑能力，可以从 `INTERRUPTED` 恢复 `ACTIVE`。**没有则不要声称会自动通宵运行或下一轮自行醒来；报告这一结构性限制。**

## 异常恢复顺序

1. 同一 `run_id` 只允许一个活动控制者、同一个 Chrome 会话只允许一条浏览器操作队列。检查状态、目标对话完整 URL、远端 HEAD 与上次 `turn_id`。
2. **任何消息发送超时**，先只读核查对应 `turn_id` 是否已在正确对话出现；若已发送，恢复 `WAIT_REVIEW` 而不是重发。不能确认时 `SEND_UNCERTAIN`，阻断而非盲重试。
3. 回复已生成则验证完整性、`run_id`、`turn_id`、`reviewed_sha`，正确才推进；不使用旧回复、半截输出或错 SHA 的建议。
4. 浏览器/网络可恢复故障允许少量受控重试（例如最多两次，先检查副作用）；失败时 `BLOCKED`。
5. FIX 前检查基线与当前 SHA。若其他 Agent 并发修改或远端漂移，重新定位事实，无法保证安全则 `BLOCKED`。
6. 对连续两轮相同问题无实质进展、重复失败或明显进入循环，先诊断原因并采取已授权的不同验证/修复方式；仍无进展则 `BLOCKED`，不无限消耗 Token。

## 完成/中断回执

- `DONE`：`PASS + ACHIEVED`，提供各验收项证据、最新 SHA、仍需用户了解的非阻断限制。
- `BLOCKED`：报告阻断点、已做到哪一步、恢复所需**最小**权限/输入/外部动作，保留检查点。
- `INTERRUPTED`：不是成功；记录下一动作以及是否存在真实外部唤醒机制。
- 不为演示持续运行而制造缺陷，也不把之前某次 FIX 实测包装成跨机器无人值守能力证明。
