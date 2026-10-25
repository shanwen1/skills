"""Persistent, deterministic state machine for the GPT <-> Luna bridge."""

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
from typing import Any, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


SCHEMA_VERSION = 1
TERMINAL_STATES = {"STOPPED"}


class BridgeError(RuntimeError):
    """A safe, operator-visible bridge failure."""


class CorrelationError(BridgeError):
    """A message cannot be associated with the current bridge turn."""


class SafetyBlock(BridgeError):
    """Execution is blocked until an operator reconciles state."""


@dataclass(frozen=True)
class Decision:
    kind: str
    action_id: str | None = None
    payload: dict[str, Any] | None = None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip())
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _field(text: str, name: str) -> str | None:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*([^\n\r]+)", text)
    return match.group(1).strip() if match else None


def parse_reply(text: str, reply_id: str | None = None) -> dict[str, Any]:
    label_match = re.search(r"(?im)^\s*[【\[]?\s*(ONTO_SA_REVIEW|ONTO_SA_NEXT|BRIDGE_REVIEW|BRIDGE_NEXT)\b", text)
    if not label_match:
        raise CorrelationError("reply has no supported ONTO_SA/BRIDGE envelope")
    label = label_match.group(1)
    return {
        "reply_id": reply_id or _fingerprint(text),
        "reply_id_source": "native" if reply_id else "content_fingerprint",
        "label": label,
        "kind": "REVIEW" if label.endswith("REVIEW") else "NEXT",
        "verdict": (_field(text, "verdict") or _field(text, "结论") or "").upper(),
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
        return state

    def _write(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now()
        self.store.save(state)

    def init(self, *, task_id: str, bridge_run_id: str, git_sha: str, conversation_url: str, repo: str | None = None, branch: str | None = None) -> dict[str, Any]:
        existing = self.store.load()
        if existing and existing.get("bridge_run_id") == bridge_run_id:
            return existing
        if existing and existing.get("state") not in (None, "STOPPED"):
            raise SafetyBlock("another active bridge run owns this state file")
        state = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "bridge_run_id": bridge_run_id,
            "conversation_url": conversation_url,
            "repo": repo,
            "branch": branch,
            "git_sha": git_sha,
            "turn": 0,
            "turn_id": f"{bridge_run_id}:0",
            "state": "READY",
            "processed_reply_ids": [],
            "processed_replies": {},
            "completed_action_ids": [],
            "pending_action": None,
            "last_handoff": None,
            "last_decision": None,
            "last_error": None,
            "history": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._write(state)
        return state

    def mark_handoff_sent(self, *, message_id: str | None = None, git_sha: str | None = None) -> dict[str, Any]:
        state = self._read()
        if state["state"] not in {"READY", "READY_TO_HANDOFF"}:
            raise SafetyBlock(f"cannot send handoff from state {state['state']}")
        if git_sha and git_sha != state["git_sha"]:
            raise SafetyBlock("handoff SHA differs from persisted current SHA")
        state["state"] = "WAITING_REVIEW"
        state["last_handoff"] = {"turn_id": state["turn_id"], "message_id": message_id, "git_sha": state["git_sha"], "sent_at": _now()}
        state["history"].append({"event": "HANDOFF_SENT", "turn_id": state["turn_id"], "at": _now()})
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
        state = self._read()
        if state["state"] in TERMINAL_STATES:
            return state
        pending = state.get("pending_action")
        if pending and pending.get("started_at") and not pending.get("completed_at"):
            state["state"] = "RECOVERY_RECONCILE"
            state["last_error"] = "action started before restart; completion must be reconciled before retry"
            state["history"].append({"event": "RECOVERY_RECONCILE", "action_id": pending["action_id"], "at": _now()})
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
        reviewed_sha = reply.get("reviewed_sha")
        if reviewed_sha and reviewed_sha != state["git_sha"]:
            raise CorrelationError("reply reviewed_sha does not match current git_sha")
        if reply.get("kind") not in {"REVIEW", "NEXT"}:
            raise CorrelationError("unsupported reply kind")
        state["processed_reply_ids"].append(reply_id)
        state["processed_replies"][reply_id] = {"turn_id": state["turn_id"], "label": reply.get("label"), "received_at": _now()}
        state["last_decision"] = reply
        decision = self._transition(state, reply, reply_id)
        self._write(state)
        return decision

    def _transition(self, state: dict[str, Any], reply: Mapping[str, Any], reply_id: str) -> Decision:
        kind = reply["kind"]
        verdict = (reply.get("verdict") or "").upper()
        payload = {"reply_id": reply_id, "text": reply.get("text", "")}
        if kind == "NEXT":
            action_id = f"{state['turn_id']}:next:{reply_id}"
            state["pending_action"] = {"action_id": action_id, "type": "NEXT", "payload": payload}
            state["state"] = "EXECUTE_NEXT"
            return Decision("EXECUTE_NEXT", action_id, payload)
        if verdict == "PASS":
            if reply.get("next") or reply.get("next_action"):
                action_id = f"{state['turn_id']}:next:{reply_id}"
                state["pending_action"] = {"action_id": action_id, "type": "NEXT", "payload": payload}
                state["state"] = "EXECUTE_NEXT"
                return Decision("EXECUTE_NEXT", action_id, payload)
            state["state"] = "WAITING_NEXT"
            return Decision("REQUEST_NEXT", payload=payload)
        if verdict == "FIX":
            state["state"] = "FIX_PENDING"
            return Decision("EXECUTE_FIX", payload=payload)
        if verdict == "UNVERIFIED":
            state["state"] = "EVIDENCE_PENDING"
            return Decision("COLLECT_EVIDENCE", payload=payload)
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
        state["state"] = "EXECUTE_NEXT"
        self._write(state)
        return Decision("EXECUTE", action_id, pending.get("payload"))

    def complete_action(self, action_id: str, *, git_sha: str) -> dict[str, Any]:
        state = self._read()
        if action_id in state["completed_action_ids"]:
            return state
        pending = state.get("pending_action") or {}
        if pending.get("action_id") != action_id:
            raise SafetyBlock("completion does not match persisted pending action")
        if git_sha != state["git_sha"]:
            raise SafetyBlock("completed action SHA differs from persisted current SHA")
        pending["completed_at"] = _now()
        state["completed_action_ids"].append(action_id)
        state["pending_action"] = None
        state["turn"] += 1
        state["turn_id"] = f"{state['bridge_run_id']}:{state['turn']}"
        state["state"] = "READY_TO_HANDOFF"
        state["history"].append({"event": "ACTION_COMPLETED", "action_id": action_id, "at": _now()})
        self._write(state)
        return state

    def mark_stop(self, reason: str) -> dict[str, Any]:
        state = self._read()
        state["state"] = "STOPPED"
        state["last_error"] = reason
        self._write(state)
        return state

