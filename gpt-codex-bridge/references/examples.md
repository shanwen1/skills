# 示例（公开占位数据）

以下只是协议说明，不是实际桥接记录。不要把示例 URL、SHA、测试输出当成真实证据。

## 代码任务：用户提出一次目标，执行者反复修复

用户已指定当前 ChatGPT 对话及验收目标，授权本次交接和在隔离分支推送**必要代码**。

```text
【BRIDGE_HANDOFF】
run_id: sample-run-001
turn_id: sample-run-001:0
repo: owner/example-repo
branch: codex/example-fix
base_sha: <40位基线提交 SHA>
head_sha: <40位本轮远端提交 SHA>
目标: 修复详情返回列表后筛选丢失
产物: 修复涉及路由与状态恢复；详见远端 Diff
本地自检: 相关测试通过（执行者报告，供进一步核验）
未验证: 实际部署环境尚未检查
```

GPT 自己读取目标提交后：

```text
【BRIDGE_REVIEW】
run_id: sample-run-001
turn_id: sample-run-001:0
reviewed_sha: <同一个本轮远端 SHA>
verdict: FIX
evidence: 已读取 Diff；发现返回路径没有恢复筛选参数
gap: 详情页返回列表时丢失查询字符串
【BRIDGE_NEXT】
priority: 保留筛选查询参数
acceptance: 筛选 → 详情 → 返回，筛选条件仍在
constraints: 最小修复；不合并、不发布
```

执行 Agent 修复、测试，得到**新的**远端 head SHA，发送 `sample-run-001:1`；GPT 对**新 SHA**重新审计，符合条件才 PASS。

## 非代码任务

例如共享文档/报告：把 `repo/branch/base_sha/head_sha` 替换为 `artifact_id/source_version/content_digest`。审查者必须能够独立读取文件正文，不能只听 Agent 总结。没有远端 Git 的任务**不得捏造 Commit SHA**。

## 兼容历史项目

已有 `ONTO_SA_HANDOFF / ONTO_SA_REVIEW / ONTO_SA_NEXT` 可以作为标签别名；新增通用字段 `run_id`、`turn_id`、`head_sha/reviewed_sha`，字段含义不随项目改变。兼容前需确认旧回复结构真正满足这些关联条件。
