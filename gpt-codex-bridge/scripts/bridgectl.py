#!/usr/bin/env python3
"""Small CLI used by a persistent bridge heartbeat/transport adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.bridge_runtime import BridgeRuntime, StateStore, parse_reply  # noqa: E402


def _read_json(path: str | None, default: Any = None) -> Any:
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_evidence(path: str | None) -> list[str]:
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [json.dumps(value, ensure_ascii=False)]


def main() -> int:
    parser = argparse.ArgumentParser(description="persistent GPT-Codex bridge state controller")
    parser.add_argument("--state", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--task-id", required=True)
    init.add_argument("--bridge-run-id", required=True)
    init.add_argument("--git-sha", required=True)
    init.add_argument("--conversation-url", required=True)
    init.add_argument("--repo")
    init.add_argument("--branch")
    init.add_argument("--goal")
    init.add_argument("--acceptance-file", help="JSON array of {id, criterion, required}")
    init.add_argument("--authorization")
    init.add_argument("--scope", default="")

    sub.add_parser("recover")
    sub.add_parser("timeout")
    sub.add_parser("send-uncertain")
    sent = sub.add_parser("handoff-sent")
    sent.add_argument("--message-id")
    sent.add_argument("--git-sha")

    handoff = sub.add_parser("handoff")
    handoff.add_argument("--package-id")
    handoff.add_argument("--package-status", default="READY_FOR_REVIEW")
    handoff.add_argument("--delta", default="")
    handoff.add_argument("--evidence-file")
    handoff.add_argument("--base-sha")
    handoff.add_argument("--head-sha")

    reply = sub.add_parser("reply")
    reply.add_argument("--file", required=True)
    reply.add_argument("--reply-id")

    begin = sub.add_parser("begin-action")
    begin.add_argument("action_id")
    complete = sub.add_parser("complete-action")
    complete.add_argument("action_id")
    complete.add_argument("--git-sha", required=True)

    sub.add_parser("stats")

    args = parser.parse_args()
    runtime = BridgeRuntime(StateStore(args.state))
    if args.command == "init":
        result = runtime.init(
            task_id=args.task_id,
            bridge_run_id=args.bridge_run_id,
            git_sha=args.git_sha,
            conversation_url=args.conversation_url,
            repo=args.repo,
            branch=args.branch,
            goal=args.goal,
            acceptance=_read_json(args.acceptance_file),
            authorization=args.authorization,
            scope=args.scope,
        )
    elif args.command == "recover":
        result = runtime.recover()
    elif args.command == "timeout":
        result = runtime.mark_timeout()
    elif args.command == "send-uncertain":
        result = runtime.mark_send_uncertain()
    elif args.command == "handoff-sent":
        result = runtime.mark_handoff_sent(message_id=args.message_id, git_sha=args.git_sha)
    elif args.command == "handoff":
        result = runtime.prepare_handoff(
            package_id=args.package_id,
            package_status=args.package_status,
            delta=args.delta,
            evidence=_read_evidence(args.evidence_file),
            base_sha=args.base_sha,
            head_sha=args.head_sha,
        )
    elif args.command == "reply":
        result = runtime.apply_reply(parse_reply(Path(args.file).read_text(encoding="utf-8"), args.reply_id))
    elif args.command == "begin-action":
        result = runtime.begin_action(args.action_id)
    elif args.command == "stats":
        result = runtime.stats()
    else:
        result = runtime.complete_action(args.action_id, git_sha=args.git_sha)
    print(json.dumps(result if isinstance(result, dict) else result.__dict__, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
