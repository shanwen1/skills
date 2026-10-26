from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime.bridge_runtime import BridgeRuntime, CorrelationError, SafetyBlock, StateStore, _context_digest, parse_reply


RUN = "bridge-test-01"
TASK = "task-test-01"
SHA = "a" * 40
NEW_SHA = "b" * 40
GOAL = "ship the requested feature"
ACCEPTANCE = [{"id": "A1", "criterion": "feature works", "required": True}]
AUTHORIZATION = "push an isolated branch only"
CONTEXT_DIGEST = _context_digest(GOAL, ACCEPTANCE, AUTHORIZATION, "")


def runtime(tmp_path):
    rt = BridgeRuntime(StateStore(tmp_path / "state.json"))
    rt.init(
        task_id=TASK,
        bridge_run_id=RUN,
        git_sha=SHA,
        conversation_url="https://chatgpt.com/c/test",
        goal=GOAL,
        acceptance=ACCEPTANCE,
        authorization=AUTHORIZATION,
    )
    return rt


def reply(
    reply_id="in-0",
    *,
    kind="REVIEW",
    verdict="PASS",
    turn_id=f"{RUN}:0",
    run_id=RUN,
    task_id=TASK,
    package_id=f"{RUN}:pkg:1",
    sha=SHA,
    goal_status="ACHIEVED",
    main_finding="fix the gap",
    context_digest=CONTEXT_DIGEST,
):
    return {
        "reply_id": reply_id,
        "label": "ONTO_SA_REVIEW" if kind == "REVIEW" else "ONTO_SA_NEXT",
        "kind": kind,
        "package_id": package_id,
        "verdict": verdict,
        "goal_status": goal_status,
        "context_digest": context_digest,
        "acceptance_ok": True if verdict == "PASS" else None,
        "main_finding": main_finding if verdict in {"FIX", "UNVERIFIED"} else None,
        "run_id": run_id,
        "task_id": task_id,
        "turn_id": turn_id,
        "reviewed_sha": sha,
        "text": "test",
    }


def first_handoff(rt, message_id="out-0"):
    payload = rt.prepare_handoff(delta="initial package", evidence=["unit test passed"])
    rt.mark_handoff_sent(message_id=message_id)
    return payload


class ProtocolParsingTests(unittest.TestCase):
    def test_parse_reply_uses_fingerprint_when_native_id_is_unavailable(self):
        parsed = parse_reply(
            "【ONTO_SA_REVIEW】\nrun_id: bridge-test-01\nturn_id: bridge-test-01:0\n"
            "package_id: bridge-test-01:pkg:1\nreviewed_sha: " + SHA + "\nverdict: PASS\ngoal_status: ACHIEVED\n"
            "context_digest: " + CONTEXT_DIGEST + "\nacceptance_ok: true"
        )
        self.assertEqual(parsed["reply_id_source"], "content_fingerprint")
        self.assertTrue(parsed["reply_id"].startswith("sha256:"))
        self.assertEqual(parsed["goal_status"], "ACHIEVED")
        self.assertEqual(parsed["package_id"], f"{RUN}:pkg:1")
        self.assertEqual(parsed["context_digest"], CONTEXT_DIGEST)
        self.assertTrue(parsed["acceptance_ok"])


class BridgeRuntimeTests(unittest.TestCase):
    def test_pass_enters_done_and_duplicate_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            self.assertEqual(rt.apply_reply(reply()).kind, "DONE")
            self.assertEqual(rt.store.load()["state"], "DONE")
            self.assertEqual(rt.apply_reply(reply()).kind, "DUPLICATE")
            self.assertEqual(rt.store.load()["processed_reply_ids"], ["in-0"])

    def test_parsed_review_text_can_drive_done(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            text = f"""【BRIDGE_REVIEW】
run_id: {RUN}
turn_id: {RUN}:0
package_id: {RUN}:pkg:1
reviewed_sha: {SHA}
context_digest: {CONTEXT_DIGEST}
verdict: PASS
goal_status: ACHIEVED
acceptance_status: A1 PASS
acceptance_ok: true
main_finding: feature works
not_verified: none
"""
            self.assertEqual(rt.apply_reply(parse_reply(text)).kind, "DONE")

    def test_missing_reviewed_sha_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(reply(sha=None))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_reviewed_sha_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(reply(sha=NEW_SHA))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_pass_requires_goal_achieved(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(reply(goal_status=None))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_context_digest_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(reply(context_digest="sha256:wrong"))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_package_id_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(reply(package_id=f"{RUN}:pkg:2"))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_pass_requires_acceptance_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            payload = reply()
            payload["acceptance_ok"] = None
            with self.assertRaises(CorrelationError):
                rt.apply_reply(payload)
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_mismatch_never_drives_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(reply(run_id="wrong"))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_interrupted_action_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            decision = rt.apply_reply(reply(kind="NEXT", verdict=None, goal_status=None))
            rt.begin_action(decision.action_id)
            recovered = rt.recover()
            self.assertEqual(recovered["state"], "RECOVERY_RECONCILE")
            with self.assertRaises(SafetyBlock):
                rt.begin_action(decision.action_id)

    def test_legacy_schema_one_pass_keeps_old_waiting_next_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            legacy = rt.store.load()
            legacy["schema_version"] = 1
            rt.store.save(legacy)
            self.assertEqual(rt.apply_reply(reply()).kind, "REQUEST_NEXT")
            self.assertEqual(rt.store.load()["state"], "WAITING_NEXT")

    def test_legacy_schema_one_fix_can_complete_with_new_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            legacy = rt.store.load()
            legacy["schema_version"] = 1
            rt.store.save(legacy)
            decision = rt.apply_reply(reply(verdict="FIX", goal_status="NOT_ACHIEVED"))
            self.assertEqual(decision.kind, "EXECUTE_FIX")
            rt.begin_action(decision.action_id)
            state = rt.complete_action(decision.action_id, git_sha=NEW_SHA)
            self.assertEqual(state["state"], "READY_TO_HANDOFF")

    def test_timeout_keeps_loop_alive_without_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            state = rt.mark_timeout()
            self.assertEqual(state["state"], "WAITING_REVIEW")
            self.assertIn("do not resend", state["last_error"])

    def test_send_uncertain_can_be_reconciled_without_duplicate_count(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            rt.prepare_handoff(delta="package", evidence=["test"])
            rt.mark_send_uncertain()
            state = rt.mark_handoff_sent(message_id="out-0")
            self.assertEqual(state["state"], "WAITING_REVIEW")
            self.assertEqual(state["formal_handoff_count"], 1)


class WorkPackageTests(unittest.TestCase):
    def test_first_handoff_full_and_later_incremental(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first = first_handoff(rt)
            self.assertEqual(first["mode"], "full")
            self.assertEqual(first["goal"], "ship the requested feature")
            decision = rt.apply_reply(reply(verdict="FIX", goal_status="NOT_ACHIEVED"))
            rt.begin_action(decision.action_id)
            rt.complete_action(decision.action_id, git_sha=NEW_SHA)
            second = rt.prepare_handoff(delta="fix the gap", evidence=["new test passed"])
            self.assertEqual(second["mode"], "incremental")
            for key in ("goal", "acceptance", "authorization", "scope"):
                self.assertNotIn(key, second)
            self.assertEqual(second["context_ref"], f"{RUN}:0")
            self.assertEqual(second["context_digest"], first["context_digest"])
            self.assertEqual(second["head_sha"], NEW_SHA)

    def test_completion_requires_started_action(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            decision = rt.apply_reply(reply(verdict="FIX", goal_status="NOT_ACHIEVED"))
            with self.assertRaises(SafetyBlock):
                rt.complete_action(decision.action_id, git_sha=NEW_SHA)

    def test_fix_requires_new_sha_before_next_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            decision = rt.apply_reply(reply(verdict="FIX", goal_status="NOT_ACHIEVED"))
            self.assertEqual(decision.kind, "EXECUTE_FIX")
            rt.begin_action(decision.action_id)
            with self.assertRaises(SafetyBlock):
                rt.complete_action(decision.action_id, git_sha=SHA)
            state = rt.complete_action(decision.action_id, git_sha=NEW_SHA)
            self.assertEqual(state["git_sha"], NEW_SHA)
            self.assertEqual(state["base_sha"], SHA)
            self.assertEqual(state["state"], "READY_TO_HANDOFF")

    def test_repeat_failure_is_detected_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            first = rt.apply_reply(reply(reply_id="fix-1", verdict="FIX", goal_status="NOT_ACHIEVED", main_finding="same gap"))
            rt.begin_action(first.action_id)
            rt.complete_action(first.action_id, git_sha=NEW_SHA)
            rt.prepare_handoff(delta="first repair", evidence=["test"])
            rt.mark_handoff_sent(message_id="out-1")
            second = rt.apply_reply(
                reply(reply_id="fix-2", verdict="FIX", goal_status="NOT_ACHIEVED", turn_id=f"{RUN}:1", package_id=f"{RUN}:pkg:2", sha=NEW_SHA, main_finding="same gap")
            )
            self.assertTrue(second.payload["repeat_failure"])
            self.assertTrue(second.payload["diagnose_required"])
            self.assertEqual(rt.store.load()["repeat_failure_count"], 1)

    def test_duplicate_handoff_send_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            rt.prepare_handoff(delta="package", evidence=["test"])
            rt.mark_handoff_sent(message_id="out-0")
            rt.mark_handoff_sent(message_id="out-0")
            self.assertEqual(rt.store.load()["formal_handoff_count"], 1)

    def test_done_allows_a_new_run_on_the_same_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            rt.apply_reply(reply())
            state = rt.init(
                task_id="task-test-02",
                bridge_run_id="bridge-test-02",
                git_sha=NEW_SHA,
                conversation_url="https://chatgpt.com/c/test",
                goal="ship the next task",
                acceptance=[{"id": "A1", "criterion": "next task works"}],
                authorization="push an isolated branch only",
            )
            self.assertEqual(state["state"], "READY")
            self.assertEqual(state["turn"], 0)
            self.assertEqual(state["processed_reply_ids"], [])
            self.assertEqual(len(state["completed_runs"]), 1)
            rt.prepare_handoff(delta="next package", evidence=["test"])
            rt.mark_handoff_sent(message_id="out-0")
            self.assertEqual(
                rt.apply_reply(
                    reply(
                        reply_id="next-in",
                        run_id="bridge-test-02",
                        task_id="task-test-02",
                        turn_id="bridge-test-02:0",
                        package_id="bridge-test-02:pkg:1",
                        sha=NEW_SHA,
                        context_digest=_context_digest(
                            "ship the next task",
                            [{"id": "A1", "criterion": "next task works", "required": True}],
                            AUTHORIZATION,
                            "",
                        ),
                    )
                ).kind,
                "DONE",
            )

    def test_stats_derives_counts_from_existing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            state = rt.store.load()
            state["formal_handoff_count"] = 0
            state["work_package_count"] = 0
            state["history"] = [
                {"event": "HANDOFF_SENT", "turn_id": f"{RUN}:0"},
                {"event": "HANDOFF_SENT", "turn_id": f"{RUN}:1"},
            ]
            state["processed_replies"] = {
                "one": {"verdict": "FIX"},
                "two": {"verdict": "PASS"},
            }
            rt.store.save(state)
            stats = rt.stats()
            self.assertEqual(stats["formal_handoff_count"], 2)
            self.assertEqual(stats["work_package_count"], 2)
            self.assertEqual(stats["fix_count"], 1)
            self.assertEqual(stats["accepted_count"], 1)

    def test_stats_uses_last_review_when_legacy_counters_are_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            state = rt.store.load()
            state["fix_count"] = 0
            state["processed_replies"] = {}
            state["last_review"] = {"verdict": "FIX", "turn_id": f"{RUN}:0"}
            rt.store.save(state)
            self.assertEqual(rt.stats()["fix_count"], 1)

    def test_stats_reports_work_packages_handoffs_fixes_and_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = runtime(Path(directory))
            first_handoff(rt)
            decision = rt.apply_reply(reply(verdict="FIX", goal_status="NOT_ACHIEVED"))
            rt.begin_action(decision.action_id)
            rt.complete_action(decision.action_id, git_sha=NEW_SHA)
            rt.prepare_handoff(delta="repair", evidence=["test"])
            rt.mark_handoff_sent(message_id="out-1")
            rt.apply_reply(reply(reply_id="pass-1", turn_id=f"{RUN}:1", package_id=f"{RUN}:pkg:2", sha=NEW_SHA))
            stats = rt.stats()
            self.assertEqual(stats["work_package_count"], 2)
            self.assertEqual(stats["formal_handoff_count"], 2)
            self.assertEqual(stats["fix_count"], 1)
            self.assertEqual(stats["accepted_count"], 1)
            self.assertIsNone(stats["token_usage"])


class IsolatedGitWorkflowTests(unittest.TestCase):
    def test_fix_commit_new_sha_review_and_done(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "feature.txt").write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
            base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            rt = BridgeRuntime(StateStore(root / "state.json"))
            rt.init(
                task_id=TASK,
                bridge_run_id=RUN,
                git_sha=base_sha,
                conversation_url="https://chatgpt.com/c/test",
                repo="example/repo",
                branch="codex/test",
                goal="ship the requested feature",
                acceptance=[{"id": "A1", "criterion": "feature works"}],
                authorization="push an isolated branch only",
            )
            rt.prepare_handoff(delta="initial package", evidence=["test passed"])
            rt.mark_handoff_sent(message_id="out-0")
            fix = rt.apply_reply(reply(verdict="FIX", goal_status="NOT_ACHIEVED", sha=base_sha))
            rt.begin_action(fix.action_id)
            (repo / "feature.txt").write_text("v2\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "fix"], cwd=repo, check=True)
            new_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.assertNotEqual(base_sha, new_sha)
            rt.complete_action(fix.action_id, git_sha=new_sha)
            second = rt.prepare_handoff(delta="fix commit", evidence=[f"git show {new_sha}"])
            self.assertEqual(second["head_sha"], new_sha)
            rt.mark_handoff_sent(message_id="out-1")
            done = rt.apply_reply(reply(reply_id="pass-1", turn_id=f"{RUN}:1", package_id=f"{RUN}:pkg:2", sha=new_sha))
            self.assertEqual(done.kind, "DONE")
            self.assertEqual(rt.store.load()["state"], "DONE")


if __name__ == "__main__":
    unittest.main()
