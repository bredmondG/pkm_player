#!/usr/bin/env python3
"""Utility to queue button presses for state_stream.py via its --actions-in file."""

import argparse
import json
import time
from pathlib import Path
from typing import List, Dict

MACROS = {
    "circle": [
        {"button": "UP", "hold_seconds": 0.5},
        {"button": "UP", "hold_seconds": 0.5},
        {"button": "RIGHT", "hold_seconds": 0.5},
        {"button": "RIGHT", "hold_seconds": 0.5},
        {"button": "DOWN", "hold_seconds": 0.5},
        {"button": "DOWN", "hold_seconds": 0.5},
        {"button": "LEFT", "hold_seconds": 0.5},
        {"button": "LEFT", "hold_seconds": 0.5},
    ],
}


def parse_args():
    parser = argparse.ArgumentParser(description="Queue button presses for state_stream.py")
    parser.add_argument(
        "actions",
        nargs="*",
        help="Button names like A, B, UP, DOWN. Provide them in the order to execute.",
    )
    if MACROS:
        parser.add_argument(
            "--macro",
            choices=sorted(MACROS.keys()),
            help="Optional macro name to send a predefined sequence (e.g. circle)",
        )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to repeat the supplied macro or action list",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=0.5,
        help="Hold duration (seconds) for simple action lists",
    )
    parser.add_argument(
        "--spacing",
        type=float,
        default=0.1,
        help="Extra pause (seconds) inserted between sequential actions",
    )
    parser.add_argument(
        "--output",
        default="pkm_actions.json",
        help="Path to the actions JSON file that state_stream.py watches",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=None,
        help="Optional frame number to include. Defaults to current epoch ms so it's always increasing.",
    )
    return parser.parse_args()


def build_actions(args) -> List[Dict[str, float]]:
    entries: List[Dict[str, float]] = []
    delay = 0.0

    def append_entry(button: str, hold: float, extra_delay: float = 0.0):
        nonlocal delay
        entries.append(
            {
                "button": button.upper(),
                "hold_seconds": hold,
                "delay_seconds": delay + extra_delay,
            }
        )
        delay += hold + args.spacing + extra_delay

    if getattr(args, "macro", None):
        template = MACROS[args.macro]
        for _ in range(args.repeat):
            for step in template:
                button = step["button"]
                hold = step.get("hold_seconds", args.hold)
                extra_delay = step.get("delay_seconds", 0.0)
                append_entry(button, hold, extra_delay)
    else:
        if not args.actions:
            raise SystemExit("Provide actions or choose --macro")
        for _ in range(args.repeat):
            for action in args.actions:
                append_entry(action, args.hold)

    # Normalize delay so the first action starts exactly at requested offset
    if entries:
        first_offset = entries[0]["delay_seconds"]
        if first_offset:
            for entry in entries:
                entry["delay_seconds"] -= first_offset
    return entries


def main():
    args = parse_args()
    actions_payload = build_actions(args)
    frame = args.frame if args.frame is not None else int(time.time() * 1000)
    payload = {"frame": frame, "actions": actions_payload}

    out_path = Path(args.output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload))
    print(f"Wrote {len(actions_payload)} action(s) at frame {frame} to {out_path}")


if __name__ == "__main__":
    main()
