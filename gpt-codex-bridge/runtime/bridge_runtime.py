"""Persistent, deterministic state machine for the GPT <-> Luna bridge.

This module deliberately does not know how to drive a browser or execute a
project task.  It owns the part that was previously implicit: durable state,
message correlation, idempotency, and safe recovery decisions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # macOS/Linux; the bridge is local-only.
    import fcntl
except ImportError:  # pragma: no cover - kept importable on non-POSIX hosts.
    fcntl = None


SCHEMA_VERSION = 2
ACTIVE_STATES = {
    "READY",
    "HANDOFF_SENT",
    "WAITING_REVIEW",
    "WAITING_NEXT",
    "EXECUTE_NEXT",
    "FIX_PENDING",
    "FIXING",
    "EVIDENCE_PENDING",
    "EVIDENCE",
    "READY_TO_HANDOFF",
    "RECOVERY_RECONCILE",
    "BLOCKED",
    "STOPPED",
}
TERMINAL_STATES = {"DONE", "STOPPED"}


class BridgeError(RuntimeError):
    """A safe, operator-visible bridge failure."""


class CorrelationError(BridgeError):
    """A message cannot be associated with the current bridge turn."""


class SafetyBlock(BridgeError):
    """Execution is blocked until an operator reconciles state."""


@dataclass(frozen=True)
class Decision:
    """The next action requested from the state machine."""

    kind: str
    action_id: str | None = None
    payload: dict[str, Any] | None = None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip())
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _field(text: str, name: str) -> str | None:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*([^\n\r]+)", text)
    return match.group(1).strip() if match else None


def _bool_field(text: str, name: str) -> bool | None:
    value = _field(text, name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "pass", "ok"}


def _normalize_acceptance(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise BridgeError("acceptance entries must be objects")
        item_id = str(item.get("id") or f"A{index}").strip()
        criterion = str(item.get("criterion") or "").strip()
        if not item_id or not criterion:
            raise BridgeError("each acceptance entry needs id and criterion")
        if item_id in seen:
            raise BridgeError(f"duplicate acceptance id: {item_id}")
        seen.add(item_id)
        normalized.append(
            {
                "id": item_id,
                "criterion": criterion,
                "required": bool(item.get("required", True)),
            }
        )
    if not normalized:
        raise BridgeError("at least one acceptance entry is required")
    return normalized


def _context_digest(goal: str, acceptance: list[dict[str, Any]], authorization: str, scope: str) -> str:
    return _digest(
        {
            "goal": goal,
            "acceptance": acceptance,
            "authorization": authorization,
            "scope": scope,
        }
    )


def _run_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bridge_run_id": state.get("bridge_run_id"),
        "task_id": state.get("task_id"),
        "goal": state.get("goal", ""),
        "state": state.get("state"),
        "turn": state.get("turn", 0),
        "work_package_count": state.get("work_package_count", 0),
        "formal_handoff_count": state.get("formal_handoff_count", 0),
        "fix_count": state.get("fix_count", 0),
        "accepted_count": state.get("accepted_count", 0),
        "finished_at": _now(),
    }


def parse_reply(text: str, reply_id: str | None = None) -> dict[str, Any]:
    """Parse the small protocol envelope from a GPT reply.

    The body remains opaque.  Only protocol fields are interpreted.  If the
    browser adapter cannot expose a native message id, a normalized content
    fingerprint is used as a stable idempotency key.
    """

    label_match = re.search(
        r"(?im)^\s*[【\[]?\s*(ONTO_SA_REVIEW|ONTO_SA_NEXT|BRIDGE_REVIEW|BRIDGE_NEXT)\b",
        text,
    )
    if not label_match:
        raise CorrelationError("reply has no supported ONTO_SA/BRIDGE envelope")
    label = label_match.group(1)
    kind = "REVIEW" if label.endswith("REVIEW") else "NEXT"
    verdict = _field(text, "verdict") or _field(text, "结论")
    if verdict:
        verdict = verdict.upper()
    goal_status = _field(text, "goal_status") or _field(text, "目标状态")
    if goal_status:
        goal_status = goal_status.upper()
    return {
        "reply_id": reply_id or _fingerprint(text),
        "reply_id_source": "native" if reply_id else "content_fingerprint",
        "label": label,
        "kind": kind,
        "package_id": _field(text, "package_id") or _field(text, "包 ID"),
        "verdict": verdict,
        "goal_status": goal_status,
        "context_digest": _field(text, "context_digest"),
        "acceptance_status": _field(text, "acceptance_status") or _field(text, "验收状态"),
        "acceptance_ok": _bool_field(text, "acceptance_ok"),
        "main_finding": _field(text, "main_finding") or _field(text, "主要发现"),
        "priority": _field(text, "priority") or _field(text, "优先事项"),
        "run_id": _field(text, "run_id") or _field(text, "bridge_run_id"),
        "task_id": _field(text, "task_id") or _field(text, "任务 ID"),
        "turn_id": _field(text, "turn_id") or _field(text, "轮次"),
        "reviewed_sha": _field(text, "reviewed_sha") or _field(text, "sha"),
        "text": text,
    }


class StateStore:
    """Atomic JSON state outside any project worktree."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self._locked():
            return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: Mapping[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as temp:
                    temp.write(payload)
                    temp.flush()
                    os.fsync(temp.fileno())
                os.replace(temp_name, self.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)


class BridgeRuntime:
    """Stateful bridge controller; transport and task execution are adapters."""

    def __init__(self, store: StateStore):
        self.store = store

    def _read(self) -> dict[str, Any]:
        state = self.store.load()
        if state is None:
            raise BridgeError("bridge state does not exist")
        # Old state files stay readable; strict protocol checks are enabled only
        # for runs created after schema v2.
        state.setdefault("schema_version", 1)
        state.setdefault("git_sha", "")
        state.setdefault("base_sha", state.get("git_sha"))
        state.setdefault("head_sha", state.get("git_sha"))
        state.setdefault("goal", state.get("task_id", ""))
        state.setdefault("acceptance", [])
        state.setdefault("authorization", "")
        state.setdefault("scope", "")
        state.setdefault("context_digest", _context_digest(state["goal"], state["acceptance"], state["authorization"], state["scope"]))
        state.setdefault("acceptance_digest", _digest(state["acceptance"]))
        state.setdefault("work_package", None)
        state.setdefault("work_package_count", 0)
        state.setdefault("formal_handoff_count", 0)
        state.setdefault("fix_count", 0)
        state.setdefault("accepted_count", 0)
        state.setdefault("repeat_failure_count", 0)
        state.setdefault("failure_fingerprints", [])
        state.setdefault("token_usage", None)
        state.setdefault("completed_runs", [])
        state.setdefault("prepared_handoff", None)
        state.setdefault("send_status", "NONE")
        state.setdefault("runtime_capability", "current-session-only")
        state.setdefault("last_review", None)
        state.setdefault("last_handoff", None)
        state.setdefault("last_decision", None)
        state.setdefault("last_error", None)
        state.setdefault("pending_action", None)
        state.setdefault("processed_reply_ids", [])
        state.setdefault("processed_replies", {})
        state.setdefault("completed_action_ids", [])
        state.setdefault("history", [])
        return state

    def _write(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now()
        self.store.save(state)

    def init(
        self,
        *,
        task_id: str,
        bridge_run_id: str,
        git_sha: str,
        conversation_url: str,
        repo: str | None = None,
        branch: str | None = None,
        goal: str | None = None,
        acceptance: Sequence[Mapping[str, Any]] | None = None,
        authorization: str | None = None,
        scope: str = "",
    ) -> dict[str, Any]:
        existing = self.store.load()
        if existing and existing.get("bridge_run_id") == bridge_run_id:
            return existing
        if existing and existing.get("state") not in (None, "STOPPED", "DONE"):
            raise SafetyBlock("another active bridge run owns this state file")
        if not goal or not str(goal).strip():
            raise BridgeError("new bridge run requires goal")
        if not acceptance:
            raise BridgeError("new bridge run requires acceptance entries")
        if not authorization or not str(authorization).strip():
            raise BridgeError("new bridge run requires authorization boundary")

        normalized_acceptance = _normalize_acceptance(acceptance)
        goal = str(goal).strip()
        authorization = str(authorization).strip()
        scope = str(scope or "").strip()
        completed_runs = list(existing.get("completed_runs", [])) if existing else []
        if existing and existing.get("state") in {"DONE", "STOPPED"}:
            completed_runs.append(_run_summary(existing))

        state = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "bridge_run_id": bridge_run_id,
            "conversation_url": conversation_url,
            "repo": repo,
            "branch": branch,
            "goal": goal,
            "acceptance": normalized_acceptance,
            "authorization": authorization,
            "scope": scope,
            "context_digest": _context_digest(goal, normalized_acceptance, authorization, scope),
            "acceptance_digest": _digest(normalized_acceptance),
            "git_sha": git_sha,
            "base_sha": git_sha,
            "head_sha": git_sha,
            "turn": 0,
            "turn_id": f"{bridge_run_id}:0",
            "state": "READY",
            "work_package": None,
            "work_package_count": 0,
            "formal_handoff_count": 0,
            "fix_count": 0,
            "accepted_count": 0,
            "repeat_failure_count": 0,
            "failure_fingerprints": [],
            "token_usage": None,
            "completed_runs": completed_runs,
            "processed_reply_ids": [],
            "processed_replies": {},
            "completed_action_ids": [],
            "pending_action": None,
            "prepared_handoff": None,
            "send_status": "NONE",
            "runtime_capability": "current-session-only",
            "last_handoff": None,
            "last_decision": None,
            "last_review": None,
            "last_error": None,
            "history": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._write(state)
        return state

    def prepare_handoff(
        self,
        *,
        package_id: str | None = None,
        package_status: str = "READY_FOR_REVIEW",
        delta: str = "",
        evidence: Sequence[str] | None = None,
        base_sha: str | None = None,
        head_sha: str | None = None,
    ) -> dict[str, Any]:
        """Create one work-package handoff payload.

        The first handoff of a run carries the full fixed context.  Later
        handoffs carry only the digest/reference plus the delta and evidence.
        """
        state = self._read()
        if state["state"] not in {"READY", "READY_TO_HANDOFF"}:
            raise SafetyBlock(f"cannot prepare handoff from state {state['state']}")
        if state.get("prepared_handoff"):
            return state["prepared_handoff"]
        if package_status not in {"READY_FOR_REVIEW", "BLOCKED"}:
            raise BridgeError("package_status must be READY_FOR_REVIEW or BLOCKED")
        head = head_sha or state["git_sha"]
        if head != state["git_sha"]:
            raise SafetyBlock("handoff SHA differs from persisted current SHA")
        if base_sha and base_sha != state.get("base_sha"):
            raise SafetyBlock("handoff base_sha differs from persisted base SHA")
        base = base_sha or state.get("base_sha") or state["git_sha"]
        package_id = package_id or f"{state['bridge_run_id']}:pkg:{state['work_package_count'] + 1}"
        first = state["formal_handoff_count"] == 0
        package = {
            "id": package_id,
            "status": package_status,
            "base_sha": base,
            "head_sha": head,
            "delta": delta,
            "evidence": list(evidence or []),
            "created_at": _now(),
        }
        payload = {
            "run_id": state["bridge_run_id"],
            "turn_id": state["turn_id"],
            "package_id": package_id,
            "package_status": package_status,
            "mode": "full" if first else "incremental",
            "context_digest": state["context_digest"],
            "acceptance_digest": state["acceptance_digest"],
            "base_sha": base,
            "head_sha": head,
            "delta": delta,
            "evidence": list(evidence or []),
        }
        if first:
            payload.update(
                {
                    "goal": state["goal"],
                    "acceptance": state["acceptance"],
                    "authorization": state["authorization"],
                    "scope": state["scope"],
                }
            )
        else:
            payload.update(
                {
                    "context_ref": f"{state['bridge_run_id']}:0",
                    "context_hint": "Use the full goal/acceptance/authorization from the first handoff of this run; verify both digests before review.",
                }
            )
        state["work_package"] = package
        state["work_package_count"] += 1
        state["prepared_handoff"] = payload
        state["state"] = "READY_TO_HANDOFF"
        state["history"].append(
            {"event": "PREPARED_HANDOFF", "turn_id": state["turn_id"], "package_id": package_id, "at": _now()}
        )
        self._write(state)
        return payload

    def mark_handoff_sent(self, *, message_id: str | None = None, git_sha: str | None = None) -> dict[str, Any]:
        state = self._read()
        message_id = message_id or f"{state['turn_id']}:handoff"
        last = state.get("last_handoff") or {}
        if (
            state["state"] == "WAITING_REVIEW"
            and last.get("message_id") == message_id
            and last.get("turn_id") == state["turn_id"]
        ):
            return state
        if state["state"] not in {"READY", "READY_TO_HANDOFF", "RECOVERY_RECONCILE"}:
            raise SafetyBlock(f"cannot send handoff from state {state['state']}")
        if state["state"] == "RECOVERY_RECONCILE" and state.get("send_status") != "UNCERTAIN":
            raise SafetyBlock("reconcile interrupted action before sending another handoff")
        prepared = state.get("prepared_handoff")
        if state.get("schema_version", 1) >= SCHEMA_VERSION and not prepared:
            raise SafetyBlock("prepare the work-package handoff before sending it")
        if git_sha and git_sha != state["git_sha"]:
            raise SafetyBlock("handoff SHA differs from persisted current SHA")
        if prepared:
            if prepared.get("turn_id") != state["turn_id"] or prepared.get("head_sha") != state["git_sha"]:
                raise SafetyBlock("prepared handoff no longer matches the current turn/SHA")
        state["state"] = "WAITING_REVIEW"
        state["send_status"] = "SENT"
        state["formal_handoff_count"] += 1
        state["last_handoff"] = {
            "turn_id": state["turn_id"],
            "message_id": message_id,
            "git_sha": state["git_sha"],
            "package_id": prepared.get("package_id") if prepared else None,
            "sent_at": _now(),
        }
        state["prepared_handoff"] = None
        state["history"].append(
            {"event": "HANDOFF_SENT", "turn_id": state["turn_id"], "message_id": message_id, "at": _now()}
        )
        self._write(state)
        return state

    def mark_send_uncertain(self, *, message_id: str | None = None) -> dict[str, Any]:
        state = self._read()
        if state["state"] not in {"READY", "READY_TO_HANDOFF"}:
            raise SafetyBlock(f"send uncertainty is not meaningful in state {state['state']}")
        state["state"] = "RECOVERY_RECONCILE"
        state["send_status"] = "UNCERTAIN"
        state["last_error"] = "handoff send outcome uncertain; inspect the target conversation before retry"
        state["history"].append(
            {"event": "SEND_UNCERTAIN", "turn_id": state["turn_id"], "message_id": message_id, "at": _now()}
        )
        self._write(state)
        return state

    def mark_timeout(self) -> dict[str, Any]:
        state = self._read()
        if state["state"] not in {"WAITING_REVIEW", "WAITING_NEXT"}:
            raise SafetyBlock(f"timeout is not meaningful in state {state['state']}")
        state["last_error"] = "GPT reply timeout; keep waiting, do not resend blindly"
        state["history"].append({"event": "TIMEOUT", "turn_id": state["turn_id"], "at": _now()})
        self._write(state)
        return state

    def recover(self) -> dict[str, Any]:
        """Reconcile after process/browser restart without replaying actions."""
        state = self._read()
        if state["state"] in TERMINAL_STATES:
            return state
        pending = state.get("pending_action")
        if pending and pending.get("started_at") and not pending.get("completed_at"):
            state["state"] = "RECOVERY_RECONCILE"
            state["last_error"] = "action started before restart; completion must be reconciled before retry"
            state["history"].append(
                {"event": "RECOVERY_RECONCILE", "action_id": pending["action_id"], "at": _now()}
            )
        self._write(state)
        return state

    def apply_reply(self, reply: Mapping[str, Any], *, source: str = "gpt") -> Decision:
        state = self._read()
        if source != "gpt":
            raise CorrelationError("only GPT-origin replies can drive the bridge")
        reply = copy.deepcopy(dict(reply))
        reply_id = reply.get("reply_id") or _fingerprint(str(reply.get("text", reply)))
        if reply_id in state["processed_reply_ids"]:
            return Decision("DUPLICATE", payload={"reply_id": reply_id})
        if state["state"] not in {"WAITING_REVIEW", "WAITING_NEXT", "RECOVERY_RECONCILE"}:
            raise SafetyBlock(f"reply cannot be applied in state {state['state']}")
        if reply.get("run_id") != state["bridge_run_id"]:
            raise CorrelationError("reply bridge_run_id/run_id mismatch")
        if reply.get("task_id") not in (None, state["task_id"]):
            raise CorrelationError("reply task_id mismatch")
        if reply.get("turn_id") != state["turn_id"]:
            raise CorrelationError("reply turn_id mismatch")
        kind = reply.get("kind")
        if kind not in {"REVIEW", "NEXT"}:
            raise CorrelationError("unsupported reply kind")
        reviewed_sha = reply.get("reviewed_sha")
        strict = state.get("schema_version", 1) >= SCHEMA_VERSION
        if kind == "REVIEW":
            if not reviewed_sha:
                if strict:
                    raise CorrelationError("review is missing reviewed_sha")
            elif reviewed_sha != state["git_sha"]:
                raise CorrelationError("reply reviewed_sha does not match current git_sha")
        elif reviewed_sha and reviewed_sha != state["git_sha"]:
            raise CorrelationError("reply reviewed_sha does not match current git_sha")
        verdict = (reply.get("verdict") or "").upper()
        if strict and kind == "REVIEW":
            if reply.get("context_digest") != state["context_digest"]:
                raise CorrelationError("reply context_digest does not match the fixed run context")
            expected_package_id = (state.get("prepared_handoff") or state.get("last_handoff") or {}).get("package_id")
            if expected_package_id and reply.get("package_id") != expected_package_id:
                raise CorrelationError("reply package_id does not match the current work package")
            if verdict == "PASS" and reply.get("goal_status") != "ACHIEVED":
                raise CorrelationError("PASS requires goal_status: ACHIEVED")
            if verdict == "PASS" and reply.get("acceptance_ok") is not True:
                raise CorrelationError("PASS requires acceptance_ok: true")
            if verdict in {"FIX", "UNVERIFIED"} and not reply.get("main_finding"):
                raise CorrelationError(f"{verdict} requires main_finding")

        state["processed_reply_ids"].append(reply_id)
        state["processed_replies"][reply_id] = {
            "turn_id": state["turn_id"],
            "label": reply.get("label"),
            "verdict": verdict,
            "goal_status": reply.get("goal_status"),
            "reviewed_sha": reviewed_sha,
            "received_at": _now(),
        }
        state["last_decision"] = reply
        decision = self._transition(state, reply, reply_id)
        self._write(state)
        return decision

    def _transition(self, state: dict[str, Any], reply: Mapping[str, Any], reply_id: str) -> Decision:
        kind = reply["kind"]
        verdict = (reply.get("verdict") or "").upper()
        payload = {
            "reply_id": reply_id,
            "text": reply.get("text", ""),
            "main_finding": reply.get("main_finding"),
            "priority": reply.get("priority"),
        }

        if kind == "NEXT":
            action_id = f"{state['turn_id']}:next:{reply_id}"
            state["pending_action"] = {"action_id": action_id, "type": "NEXT", "payload": payload}
            state["state"] = "EXECUTE_NEXT"
            return Decision("EXECUTE_NEXT", action_id, payload)
        if verdict == "PASS":
            if state.get("schema_version", 1) < SCHEMA_VERSION:
                if reply.get("next") or reply.get("next_action"):
                    action_id = f"{state['turn_id']}:next:{reply_id}"
                    state["pending_action"] = {"action_id": action_id, "type": "NEXT", "payload": payload}
                    state["state"] = "EXECUTE_NEXT"
                    return Decision("EXECUTE_NEXT", action_id, payload)
                state["state"] = "WAITING_NEXT"
                return Decision("REQUEST_NEXT", payload=payload)
            state["accepted_count"] += 1
            state["last_review"] = {
                "turn_id": state["turn_id"],
                "verdict": verdict,
                "goal_status": reply.get("goal_status"),
                "reviewed_sha": reply.get("reviewed_sha"),
                "at": _now(),
            }
            state["state"] = "DONE"
            state["history"].append({"event": "DONE", "turn_id": state["turn_id"], "at": _now()})
            return Decision("DONE", payload=payload)
        if verdict == "FIX":
            state["fix_count"] += 1
            failure_text = reply.get("main_finding") or reply.get("priority") or reply_id
            failure_fingerprint = _fingerprint(str(failure_text))
            repeat = any(
                item.get("fingerprint") == failure_fingerprint
                for item in state.get("failure_fingerprints", [])
            )
            if repeat:
                state["repeat_failure_count"] += 1
            state["failure_fingerprints"].append(
                {"fingerprint": failure_fingerprint, "turn_id": state["turn_id"], "at": _now()}
            )
            state["failure_fingerprints"] = state["failure_fingerprints"][-20:]
            action_id = f"{state['turn_id']}:fix:{reply_id}"
            payload["repeat_failure"] = repeat
            payload["diagnose_required"] = repeat
            state["pending_action"] = {"action_id": action_id, "type": "FIX", "payload": payload}
            state["state"] = "FIX_PENDING"
            return Decision("EXECUTE_FIX", action_id, payload)
        if verdict == "UNVERIFIED":
            action_id = f"{state['turn_id']}:evidence:{reply_id}"
            state["pending_action"] = {"action_id": action_id, "type": "EVIDENCE", "payload": payload}
            state["state"] = "EVIDENCE_PENDING"
            return Decision("COLLECT_EVIDENCE", action_id, payload)
        if verdict in {"STOP", "STOPPED", "HALT"}:
            state["state"] = "STOPPED"
            return Decision("STOP", payload=payload)
        raise CorrelationError("review has no actionable PASS/FIX/UNVERIFIED/STOP verdict")

    def begin_action(self, action_id: str) -> Decision:
        state = self._read()
        pending = state.get("pending_action") or {}
        if action_id in state["completed_action_ids"]:
            return Decision("DUPLICATE_ACTION", action_id)
        if state["state"] == "RECOVERY_RECONCILE":
            raise SafetyBlock("reconcile interrupted action before beginning another")
        if pending.get("action_id") != action_id:
            raise SafetyBlock("action is not the persisted pending action")
        if pending.get("started_at"):
            raise SafetyBlock("action already started; do not replay it")
        pending["started_at"] = _now()
        state["state"] = {
            "FIX": "FIXING",
            "EVIDENCE": "EVIDENCE",
        }.get(pending.get("type"), "EXECUTE_NEXT")
        self._write(state)
        return Decision("EXECUTE", action_id, pending.get("payload"))

    def complete_action(self, action_id: str, *, git_sha: str) -> dict[str, Any]:
        state = self._read()
        if action_id in state["completed_action_ids"]:
            return state
        pending = state.get("pending_action") or {}
        if pending.get("action_id") != action_id:
            raise SafetyBlock("completion does not match persisted pending action")
        if not pending.get("started_at"):
            raise SafetyBlock("begin the action before completing it")
        if not git_sha:
            raise SafetyBlock("completed action requires a git SHA")
        previous_sha = state["git_sha"]
        if pending.get("type") == "FIX" and git_sha == previous_sha:
            raise SafetyBlock("FIX completion requires a new head SHA")
        pending["completed_at"] = _now()
        state["completed_action_ids"].append(action_id)
        state["pending_action"] = None
        state["base_sha"] = previous_sha
        state["git_sha"] = git_sha
        state["head_sha"] = git_sha
        state["turn"] += 1
        state["turn_id"] = f"{state['bridge_run_id']}:{state['turn']}"
        state["state"] = "READY_TO_HANDOFF"
        state["history"].append(
            {
                "event": "ACTION_COMPLETED",
                "action_id": action_id,
                "from_sha": previous_sha,
                "to_sha": git_sha,
                "at": _now(),
            }
        )
        self._write(state)
        return state

    def mark_stop(self, reason: str) -> dict[str, Any]:
        state = self._read()
        state["state"] = "STOPPED"
        state["last_error"] = reason
        self._write(state)
        return state

    def stats(self) -> dict[str, Any]:
        """Lightweight metrics derived from persisted state and history."""
        state = self._read()
        history = state.get("history", [])
        handoff_turns = {
            event.get("turn_id")
            for event in history
            if event.get("event") == "HANDOFF_SENT" and event.get("turn_id")
        }
        verdicts = [
            item.get("verdict")
            for item in (state.get("processed_replies") or {}).values()
            if isinstance(item, Mapping)
        ]
        failure_fingerprints = [
            item.get("fingerprint")
            for item in state.get("failure_fingerprints", [])
            if isinstance(item, Mapping) and item.get("fingerprint")
        ]
        formal_handoff_count = state.get("formal_handoff_count", 0) or len(handoff_turns)
        last_review = state.get("last_review")
        if not last_review and state.get("last_decision"):
            decision = state["last_decision"]
            last_review = {
                key: decision.get(key)
                for key in ("turn_id", "verdict", "goal_status", "reviewed_sha")
            }
        fix_count = state.get("fix_count", 0) or verdicts.count("FIX")
        if not fix_count and last_review and last_review.get("verdict") == "FIX":
            fix_count = 1
        accepted_count = state.get("accepted_count", 0) or verdicts.count("PASS")
        if not accepted_count and last_review and last_review.get("verdict") == "PASS":
            accepted_count = 1
        return {
            "run_id": state["bridge_run_id"],
            "task_id": state["task_id"],
            "state": state["state"],
            "goal": state.get("goal"),
            "context_digest": state.get("context_digest"),
            "runtime_capability": state.get("runtime_capability", "current-session-only"),
            "work_package_count": state.get("work_package_count", 0) or formal_handoff_count,
            "formal_handoff_count": formal_handoff_count,
            "fix_count": fix_count,
            "repeat_failure_count": state.get("repeat_failure_count", 0)
            or (len(failure_fingerprints) - len(set(failure_fingerprints))),
            "accepted_count": accepted_count,
            "token_usage": state.get("token_usage"),
            "last_handoff": state.get("last_handoff"),
            "last_review": last_review,
            "completed_runs": state.get("completed_runs", []),
        }
