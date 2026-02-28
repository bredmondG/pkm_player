#!/usr/bin/env python3
"""state_forwarder.py

Watch a Pokémon state snapshot file and push updates into OpenClaw via the CLI.

Usage example:
    python state_forwarder.py \
        --state out/state_latest.json \
        --channel webchat \
        --target current \
        --prefix "POKÉMON STATE" \
        --min-interval 10

Whenever the state file changes, the script reads it, optionally truncates long
payloads, and invokes:

    openclaw message send --channel <channel> --to <target> --message <payload>

so the latest snapshot lands in the desired chat (like this assistant).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Optional


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Relay Pokémon state snapshots via OpenClaw messages.")
    parser.add_argument("--state", required=True, help="Path to the JSON file produced by --state-out")
    parser.add_argument("--channel", default="webchat", help="OpenClaw channel name (e.g. webchat, telegram)")
    parser.add_argument("--target", default="current", help="Recipient identifier (depends on channel")
    parser.add_argument(
        "--min-interval",
        type=float,
        default=5.0,
        help="Minimum seconds between messages to avoid flooding",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=3500,
        help="Maximum characters to send (JSON will be truncated with … if longer)",
    )
    parser.add_argument(
        "--prefix",
        default="POKÉMON STATE",
        help="Line prefix to add before the JSON payload",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payloads instead of calling openclaw message send",
    )
    return parser.parse_args()


def load_payload(path: Path, max_chars: int, prefix: str) -> str:
    data = json.loads(path.read_text())
    raw = json.dumps(data, indent=2)
    if len(raw) > max_chars:
        raw = raw[: max_chars - 1] + "\u2026"  # ellipsis
    header = prefix.strip()
    if header:
        return f"{header}:\n{raw}"
    return raw


def send_message(channel: str, target: str, payload: str, dry_run: bool) -> None:
    if dry_run:
        print("[dry-run] would send:\n", textwrap.indent(payload, "  "))
        return
    cmd = ["openclaw", "message", "send", "--channel", channel, "--to", target, "--message", payload]
    subprocess.run(cmd, check=True)


def main() -> None:
    args = build_args()
    state_path = Path(args.state).expanduser().resolve()
    if not state_path.exists():
        raise FileNotFoundError(state_path)

    last_mtime = 0.0
    last_sent = 0.0
    print(f"[state_forwarder] Watching {state_path}")
    while True:
        try:
            mtime = state_path.stat().st_mtime
        except FileNotFoundError:
            time.sleep(1)
            continue

        if mtime != last_mtime and time.time() - last_sent >= args.min_interval:
            try:
                payload = load_payload(state_path, args.max_chars, args.prefix)
                send_message(args.channel, args.target, payload, args.dry_run)
                last_sent = time.time()
                print(f"[state_forwarder] Relayed snapshot from {time.ctime(mtime)}")
            except Exception as exc:  # broad catch so the loop keeps running
                print(f"[state_forwarder] Failed to relay state: {exc}")
            last_mtime = mtime

        time.sleep(1)


if __name__ == "__main__":
    main()
