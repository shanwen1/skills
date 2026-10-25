from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.bridge_runtime import BridgeRuntime, CorrelationError, SafetyBlock, StateStore, parse_reply


RUN = "bridge-test-01"
TASK = "task-test-01"
SHA = "a" * 40


def make_runtime(directory):
    rt = BridgeRuntime(StateStore(Path(directory) / "state.json"))
    rt.init(task_id=TASK, bridge_run_id=RUN, git_sha=SHA, conversation_url="https://chatgpt.com/c/test")
    rt.mark_handoff_sent(message_id="out-0")
    return rt


def make_reply(reply_id="in-0", *, kind="REVIEW", verdict="PASS", turn_id=f"{RUN}:0", run_id=RUN, task_id=TASK):
    return {"reply_id": reply_id, "label": "ONTO_SA_REVIEW" if kind == "REVIEW" else "ONTO_SA_NEXT", "kind": kind, "verdict": verdict, "run_id": run_id, "task_id": task_id, "turn_id": turn_id, "reviewed_sha": SHA, "text": "test"}


class BridgeRuntimeTests(unittest.TestCase):
    def test_pass_enters_wait_next_and_duplicate_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = make_runtime(directory)
            self.assertEqual(rt.apply_reply(make_reply()).kind, "REQUEST_NEXT")
            self.assertEqual(rt.store.load()["state"], "WAITING_NEXT")
            self.assertEqual(rt.apply_reply(make_reply()).kind, "DUPLICATE")

    def test_next_executes_then_advances_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = make_runtime(directory)
            decision = rt.apply_reply(make_reply(kind="NEXT", verdict=None))
            self.assertEqual(decision.kind, "EXECUTE_NEXT")
            rt.begin_action(decision.action_id)
            state = rt.complete_action(decision.action_id, git_sha=SHA)
            self.assertEqual((state["turn"], state["turn_id"], state["state"]), (1, f"{RUN}:1", "READY_TO_HANDOFF"))

    def test_mismatch_never_drives_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = make_runtime(directory)
            with self.assertRaises(CorrelationError):
                rt.apply_reply(make_reply(run_id="wrong"))
            self.assertEqual(rt.store.load()["state"], "WAITING_REVIEW")

    def test_interrupted_action_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = make_runtime(directory)
            decision = rt.apply_reply(make_reply(kind="NEXT", verdict=None))
            rt.begin_action(decision.action_id)
            self.assertEqual(rt.recover()["state"], "RECOVERY_RECONCILE")
            with self.assertRaises(SafetyBlock):
                rt.begin_action(decision.action_id)

    def test_timeout_keeps_loop_alive_without_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = make_runtime(directory)
            state = rt.mark_timeout()
            self.assertEqual(state["state"], "WAITING_REVIEW")
            self.assertIn("do not resend", state["last_error"])

    def test_pass_with_embedded_next_is_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            rt = make_runtime(directory)
            reply = make_reply()
            reply["next"] = {"priority": "continue"}
            self.assertEqual(rt.apply_reply(reply).kind, "EXECUTE_NEXT")

    def test_parse_reply_uses_fingerprint_without_native_id(self):
        parsed = parse_reply("【ONTO_SA_REVIEW】\nrun_id: bridge-test-01\nturn_id: bridge-test-01:0\nverdict: PASS")
        self.assertEqual(parsed["reply_id_source"], "content_fingerprint")
        self.assertTrue(parsed["reply_id"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()

