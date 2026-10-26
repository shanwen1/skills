# 目标级状态与中断恢复（v0.3.2）

**恢复的对象是整体用户目标，不只是最后一条 GPT 消息。** Skill 指示现有 Agent 使用本机已有的轻量状态控制器；它本身不创建后台调度器、不保证关机后自动运行。

## 最小持久化状态（放在业务仓库外）

优先复用 `runtime/bridge_runtime.py`；状态文件放在 `$XDG_STATE_HOME/gpt-codex-bridge/` 或用户家目录下仅当前用户可读的目录，原子写入。

```json
{
  "schema_version": 2,
  "task_id": "<任务 ID>",
  "bridge_run_id": "<稳定的用户目标 ID>",
  "goal": "<最短完整目标>",
  "acceptance": [
    {"id": "A1", "criterion": "<验收项>", "required": true}
  ],
  "authorization": "<允许的动作边界>",
  "scope": "<不做什么>",
  "context_digest": "sha256:<固定上下文摘要>",
  "acceptance_digest": "sha256:<验收清单摘要>",
  "conversation_url": "<用户指定原 ChatGPT 对话 URL>",
  "repo": "<owner/repo>",
  "branch": "<已授权隔离分支>",
  "base_sha": "<本轮基线 SHA>",
  "head_sha": "<本轮远端最新 SHA>",
  "turn_id": "<本次交接 ID>",
  "state": "READY | WAITING_REVIEW | WAITING_NEXT | EXECUTE_NEXT | FIX_PENDING | FIXING | EVIDENCE_PENDING | EVIDENCE | READY_TO_HANDOFF | RECOVERY_RECONCILE | DONE | BLOCKED | STOPPED",
  "work_package": null,
  "work_package_count": 0,
  "formal_handoff_count": 0,
  "fix_count": 0,
  "repeat_failure_count": 0,
  "accepted_count": 0,
  "token_usage": null,
  "prepared_handoff": null,
  "last_handoff": null,
  "last_review": null,
  "last_decision": null,
  "processed_reply_ids": [],
  "failure_fingerprints": [],
  "blocked_reason": null,
  "runtime_capability": "current-session-only"
}
```

不保存账号密码、Cookie、Chrome Profile、客户文档原文、私人访问令牌。目标文本也不得包含敏感业务细节；用本地受保护的必要引用代替。

## 运行器能力边界

- 状态控制器支持：原子状态、reply 去重、handoff 幂等、ID/SHA 关联、发送不确定、action 中断恢复、`DONE` 后新 `run_id` 接续。
- 状态控制器**不是**常驻监听器，也不驱动浏览器；没有独立 runner 时，只能在当前 Agent 会话存活期间持续循环。
- 如果宿主已有真实、已授权、已验证的续跑机制，可从 `INTERRUPTED` 恢复；不要自行重建调度系统。
- 不要仅凭“状态文件存在”宣称桥接正在监听；必须以实际进程、页面和消息证据为准。

## 模型选型踩坑：Skill 规则不保证模型一定照做

执行模型是长循环可靠性的一部分，不能把所有提前中断都归因于桥接协议。

- 长任务开始前，用低风险任务实际验证完整多轮链路：执行 → GPT 审查 → FIX → 自动修复 → 新 SHA → 复审。
- 若模型反复在目标未完成时自行总结退出，先检查是否有明确阻断；没有阻断且状态仍可推进时，优先切换到已实测能继续执行的模型/运行器，而不是持续叠加更长的提示词。
- **用户在当前配置下的实测**：Luna 反复提前结束；DeepSeek Flash 收到一次桥接说明后仍持续工作。当前场景优先选 DeepSeek Flash；这是特定环境的经验，不应推广成对所有模型版本的绝对结论。

## 状态控制器 CLI

```bash
python3 scripts/bridgectl.py --state "$STATE" init \
  --task-id <task> --bridge-run-id <run> --git-sha <base> \
  --conversation-url <url> --goal "<goal>" \
  --acceptance-file acceptance.json \
  --authorization "<authorization>"

python3 scripts/bridgectl.py --state "$STATE" handoff \
  --delta "<delta>" --evidence-file evidence.txt
python3 scripts/bridgectl.py --state "$STATE" handoff-sent --message-id <msg-id>
python3 scripts/bridgectl.py --state "$STATE" reply --file review.txt
python3 scripts/bridgectl.py --state "$STATE" stats
```

`acceptance.json` 是 `[{ "id": "A1", "criterion": "...", "required": true }]`。`handoff` 首次返回完整上下文，后续返回增量上下文。

## 恢复顺序

1. **先检查已有本地记录和 ChatGPT 页面，不立即发送。** 确认完整 URL、最新远端 SHA、最近一次已发送的 `turn_id` 与 GPT 回复。
2. 若消息已出现在对话，但本地状态还没记录发送：以页面上的真实消息为准，补状态，不重复发。
3. 若消息未明确发送成功：标记 `SEND_UNCERTAIN`；只读查找对应 ID。仍不确定则停止询问用户，不做“盲重试”。
4. 若 GPT 已生成完整且匹配的回复：解析并校验后恢复相应 NEXT/PASS/FIX/UNVERIFIED；PASS + ACHIEVED 才恢复为 `DONE`。
5. FIX 修复前重新核对分支 HEAD 与已审查 SHA；任何 SHA 漂移、分支更新或其他 Agent 并发修改均需重新定基线/停止，不沿用旧审查。
6. `DONE` 后若用户还有已授权的下一项，使用新 `run_id` 初始化；同一状态文件会保留上一 run 的轻量统计，不重放旧 action。
7. 状态落盘时尽量原子写入，且同一 `run_id` 由单控制者持有；不要让两个 Agent 在同一对话同时发送。

## 停止优于重复

- 回复不完整、超时、ChatGPT 未登录、页面 URL 变化、浏览器许可被拒绝 → 停止发送/执行并等待恢复；不得丢弃状态。
- GPT 返回 PASS 且满足整体验收 → `DONE`；不要把单轮 PASS 当作持续任务完成。
- action 已开始但进程中断 → `RECOVERY_RECONCILE`，先查真实执行证据；未确认完成前禁止重放。
- UNVERIFIED → 仅补证据，不默默重写代码。
- 同一 `main_finding` 连续出现 → 先诊断，不重复同一修法；状态控制器会标记 `diagnose_required`。
- 推送与合并是不同授权：Push 独立分支不等于许可合并、发布或部署。
- 传输可靠性 ≠ 审查可靠性；聊天中的 PASS 若没有 GPT 真实核查证据，不能视为独立验收成功。

## 可重复的验证策略

- 首次部署只用唯一 PING/PONG 验证收发。
- 已有成功 FIX 回路时，不为“满足测试”人工制造缺陷。
- 后续新会话只做一次低风险读写与消息校验；断线恢复、浏览器工具切换等特性只有真实跑过才标注“已验证”。
- 本地确定性测试覆盖去重、ID/SHA 一致性、恢复、重复失败和 DONE 接续；真实 ChatGPT 往返与跨 Turn 唤醒必须单独实测。
