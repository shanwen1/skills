# 状态、幂等与中断恢复

## 最小本地记录（不放进工作仓库）

可用 JSON 或本机已有任务状态服务；不要为了此 Skill 新建数据库或后台 Agent。

```json
{
  "run_id": "<唯一用户任务>",
  "conversation_url": "<用户指定的现有 ChatGPT 对话 URL>",
  "repo": "<owner/repo>",
  "branch": "<isolated-branch>",
  "base_sha": "<当前轮基线 SHA>",
  "head_sha": "<当前轮远端 SHA>",
  "turn_id": "<本次交接唯一 ID>",
  "phase": "READY|SEND_UNCERTAIN|SENT|WAIT_REVIEW|FIX|EVIDENCE|DONE|STOPPED",
  "fix_count": 0,
  "max_fixes": 3,
  "last_reply_id": "<如可获得>",
  "reviewed_sha": "<GPT 实际审阅的 SHA>",
  "verdict": "PASS|FIX|UNVERIFIED|null"
}
```

建议状态目录位于 `$XDG_STATE_HOME/gpt-codex-bridge/` 或用户家目录下的等效私有位置；权限限制为当前用户。状态只保留任务 ID、证据标识和必要恢复锚点；不记录敏感材料、Browser Profile、Cookie、API Key。

## 恢复顺序

1. **先检查已有本地记录和 ChatGPT 页面，不立即发送。** 确认完整 URL、最新远端 SHA、最近一次已发送的 `turn_id` 与 GPT 回复。
2. 若消息已出现在对话，但本地状态还没记录发送：以页面上的真实消息为准，补状态，不重复发。
3. 若消息未明确发送成功：标记 `SEND_UNCERTAIN`；只读查找对应 ID。仍不确定则停止询问用户，不做“盲重试”。
4. 若 GPT 已生成完整且匹配的回复：解析并校验后恢复相应 PASS/FIX/UNVERIFIED；否则等待或超时停止。
5. FIX 修复前重新核对分支 HEAD 与已审查 SHA；任何 SHA 漂移、分支更新或其他 Agent 并发修改均需重新定基线/停止，不沿用旧审查。
6. 状态落盘时尽量原子写入，且同一 `run_id` 由单控制者持有；不要让两个 Agent 在同一对话同时发送。

## 停止优于重复

- 回复不完整、超时、ChatGPT 未登录、页面 URL 变化、浏览器许可被拒绝 → 停止或等待人工恢复。
- GPT 返回 PASS 且满足本轮验收 → `DONE`，不追加新需求。
- 连续 FIX 达上限 → `STOPPED`，报告已有证据和剩余障碍，不自动扩大模型预算或权限。
- UNVERIFIED → 仅补证据，不默默重写代码。
- 推送与合并是不同授权：Push 独立分支不等于许可合并、发布或部署。
- 传输可靠性 ≠ 审查可靠性；聊天中的 PASS 若没有 GPT 真实核查证据，不能视为独立验收成功。

## 可重复的验证策略

- 首次部署只用唯一 PING/PONG 验证收发。
- 已有成功 FIX 回路时，不为“满足测试”人工制造缺陷。
- 后续新会话只做一次低风险读写与消息校验；断线恢复、浏览器工具切换等特性只有真实跑过才标注“已验证”。
