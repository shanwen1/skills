# 示例：整体目标不能被单轮完成回报替代

以下是虚构仓库和虚构 SHA 占位符，不是实际运行记录。

## 代码任务：某项修复通过，不等于整个用户目标通过

用户目标：**详情页返回列表后筛选仍在，同时保证窄屏布局正常、相关测试通过**。用户指定现有 ChatGPT 对话，并明确授权执行者向独立远端分支 Push。

```text
【BRIDGE_HANDOFF】
run_id: sample-run-001
turn_id: sample-run-001:0
goal: 返回列表保留筛选、窄屏正常、相关测试通过
acceptance_status:
  - A1 返回后筛选不丢: PASS（本地用例通过，待 GPT 核验）
  - A2 窄屏列表正常: UNKNOWN（尚未验证）
  - A3 相关测试通过: PASS（执行者报告）
repo: owner/example-repo
branch: codex/example-fix
base_sha: <本轮基线提交 SHA>
head_sha: <本轮远端提交 SHA>
本轮完成: 修复返回时查询字符串
本地测试/实际运行证据: 相关用例通过（执行者报告）
尚未完成及风险: 窄屏场景未验证
```

GPT 自己读取远端代码，发现筛选修复没有完全覆盖历史路由：

```text
【BRIDGE_REVIEW】
run_id: sample-run-001
turn_id: sample-run-001:0
reviewed_sha: <同一个 head SHA>
verdict: FIX
goal_status: NOT_ACHIEVED
acceptance_status:
  - A1: FAIL（历史路由仍丢参数）
  - A2: UNKNOWN
  - A3: PASS（已核对相关测试）
verified_evidence: 已读实际 Diff 与相关源码
main_finding: 旧路径仍不能恢复筛选
not_verified: 窄屏的真实 UI 表现
【BRIDGE_NEXT】
priority: 修复旧路径的筛选状态传递
acceptance: 所有入口进入详情再返回，筛选状态均保留
constraints: 最小改动，不合并、不发布
```

执行 Agent **无需用户再次说“继续”**：修复 A1、测试、授权范围内 Push 新 SHA、重新发送下一轮交接。即使新一轮 A1 通过，但 A2 仍未完成，**也不能宣称 DONE**；先验证/处理 A2，并在整体目标全部通过后停止。

## 非代码任务

共享文档/报告：将 `repo / branch / base_sha / head_sha` 替换为 `artifact_id / source_version / content_digest`。GPT 必须实际读取产物。执行者没有提交就不能编造 SHA。

## 跨轮次与中断

- 新一轮沿用原 `run_id`、完整 `goal`、全部 `acceptance`；更新 `turn_id`、`head_sha` 和验收证据。
- 聊天回复中断或 Agent Turn 自然退出：保留目标与进度为 `INTERRUPTED`；下次按 [恢复规则](recovery.md) 核查后续行，绝不把暂停状态写成 `DONE`。
- 项目可继续使用 `ONTO_SA_HANDOFF / ONTO_SA_REVIEW / ONTO_SA_NEXT` 标签别名，但新增的 `goal_status`、`acceptance_status` 和严格关联 ID/SHA 不可丢。
