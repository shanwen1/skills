# 示例：工作包交接与整体验收

以下是虚构仓库和虚构 SHA 占位符，不是实际运行记录。

## 首次交接：完整固定上下文

```text
【BRIDGE_HANDOFF】
run_id: sample-run-001
turn_id: sample-run-001:0
package_id: sample-run-001:pkg:1
package_status: READY_FOR_REVIEW
goal: 返回列表保留筛选、窄屏正常、相关测试通过
acceptance:
  - A1 返回后筛选不丢
  - A2 窄屏列表正常
  - A3 相关测试通过
authorization: 仅允许推送隔离分支 codex/example-fix；不合并、不发布
scope: 不修改无关路由
context_digest: sha256:<固定上下文摘要>
acceptance_digest: sha256:<验收清单摘要>
repo: owner/example-repo
branch: codex/example-fix
base_sha: <本轮基线提交 SHA>
head_sha: <本轮远端提交 SHA>
delta: 完成返回路径的查询参数恢复；补充两个回归用例
evidence:
  - pytest tests/test_filter.py -q → 2 passed
  - git show <head_sha> -- src/router.py tests/test_filter.py
未验证项及风险: 真实部署环境未检查
```

GPT 自己读取远端代码后发现筛选修复没有覆盖历史路由：

```text
【BRIDGE_REVIEW】
run_id: sample-run-001
turn_id: sample-run-001:0
package_id: sample-run-001:pkg:1
reviewed_sha: <同一个本轮远端 SHA>
context_digest: sha256:<固定上下文摘要>
verdict: FIX
goal_status: NOT_ACHIEVED
acceptance_status: A1 FAIL；A2 UNKNOWN；A3 PASS
acceptance_ok: false
verified_evidence: 已读取 Diff 和相关测试
main_finding: 详情页返回列表时丢失查询字符串
not_verified: 窄屏未实际运行
【BRIDGE_NEXT】
priority: 保留筛选查询参数，覆盖历史路由
acceptance: 筛选 → 详情 → 返回，筛选条件仍在
constraints: 最小修复；不合并、不发布
```

## 修复后的增量交接

执行 Agent 修复、自测并得到**新的**远端 head SHA。第二次交接只发变化和证据：

```text
【BRIDGE_HANDOFF】
run_id: sample-run-001
turn_id: sample-run-001:1
package_id: sample-run-001:pkg:2
package_status: READY_FOR_REVIEW
context_digest: sha256:<与首次相同>
acceptance_digest: sha256:<与首次相同>
context_ref: sample-run-001:0
repo: owner/example-repo
branch: codex/example-fix
base_sha: <上一轮 head SHA>
head_sha: <新提交 SHA>
delta: 历史路由也恢复查询字符串；新增回归用例
evidence:
  - pytest tests/test_filter.py -q → 3 passed
  - git show <新提交 SHA> -- src/router.py tests/test_filter.py
未验证项及风险: 真实部署环境未检查
```

GPT 对**新 SHA**重新审计，只有 `PASS + ACHIEVED + acceptance_ok: true` 才进入 `DONE`。

## 非代码任务

例如共享文档/报告：把 `repo/branch/base_sha/head_sha` 替换为 `artifact_id/source_version/content_digest`。审查者必须能够独立读取文件正文，不能只听 Agent 总结。没有远端 Git 的任务**不得捏造 Commit SHA**。

## 兼容历史项目

已有 `ONTO_SA_HANDOFF / ONTO_SA_REVIEW / ONTO_SA_NEXT` 可以作为标签别名；新增通用字段 `run_id`、`turn_id`、`package_id`、`context_digest`、`reviewed_sha`，字段含义不随项目改变。兼容前需确认旧回复结构真正满足这些关联条件。
