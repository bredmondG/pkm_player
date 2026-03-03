#!/usr/bin/env python3
"""Utility to queue button presses for state_stream.py via its --actions-in file."""

import argparse
import json
import time
from pathlib import Path
from typing import List, Dict

DEFAULT_MACROS_PATH = Path("macros.json")


def parse_args():
    parser = argparse.ArgumentParser(description="Queue button presses for state_stream.py")
    parser.add_argument(
        "actions",
        nargs="*",
        help="Button names like A, B, UP, DOWN. Provide them in the order to execute.",
    )
    parser.add_argument(
        "--macro",
        help="Name of a macro defined in macros.json",
    )
    parser.add_argument(
        "--macro-file",
        default=str(DEFAULT_MACROS_PATH),
        help="Path to macros JSON file (default: macros.json)",
    )
    parser.add_argument(
        "--define-macro",
        nargs=2,
        metavar=("NAME", "ACTIONS"),
        help="Create/update a macro using a comma-separated action list (e.g. walk,UP,RIGHT,DOWN,LEFT)",
    )
    parser.add_argument(
        "--list-macros",
        action="store_true",
        help="Show available macros and exit",
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
        "--auto-control",
        default="auto_control.json",
        help="Path to the auto-action toggle JSON file",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=None,
        help="Optional frame number to include. Defaults to current epoch ms so it's always increasing.",
    )
    toggle_group = parser.add_mutually_exclusive_group()
    toggle_group.add_argument(
        "--enable-auto",
        action="store_true",
        help="Enable state_stream auto actions via the auto-control file",
    )
    toggle_group.add_argument(
        "--disable-auto",
        action="store_true",
        help="Disable state_stream auto actions via the auto-control file",
    )
    return parser.parse_args()


def load_macros(path: Path) -> Dict[str, List[Dict[str, float]]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse macros file {path}: {exc}")


def save_macros(path: Path, macros: Dict[str, List[Dict[str, float]]]):
    path.write_text(json.dumps(macros, indent=2))
    print(f"Saved macros to {path}")


def build_actions(args, macros) -> List[Dict[str, float]]:
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
        template = macros.get(args.macro)
        if template is None:
            raise SystemExit(f"Macro '{args.macro}' not found in {args.macro_file}")
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

    if entries:
        first_offset = entries[0]["delay_seconds"]
        if first_offset:
            for entry in entries:
                entry["delay_seconds"] -= first_offset
    return entries


def main():
    args = parse_args()
    macro_path = Path(args.macro_file).expanduser().resolve()
    macros = load_macros(macro_path)

    if args.list_macros:
        if not macros:
            print("No macros defined.")
        else:
            print("Available macros:")
            for name in macros:
                print(f"  - {name}")
        return

    if args.define_macro:
        name, actions_str = args.define_macro
        steps = []
        for token in actions_str.split(','):
            token = token.strip().upper()
            if not token:
                continue
            steps.append({"button": token, "hold_seconds": args.hold})
        macros[name] = steps
        save_macros(macro_path, macros)
        return

    control_written = False
    control_path = Path(args.auto_control).expanduser().resolve()
    if args.enable_auto or args.disable_auto:
        value = True if args.enable_auto else False
        control_payload = {
            "auto_enabled": value,
            "timestamp": time.time(),
        }
        control_path.parent.mkdir(parents=True, exist_ok=True)
        control_path.write_text(json.dumps(control_payload))
        state = "enabled" if value else "disabled"
        print(f"Set auto-actions {state} via {control_path}")
        control_written = True

    if args.macro and args.macro not in macros:
        raise SystemExit(f"Macro '{args.macro}' not defined. Use --define-macro or edit {macro_path}")

    if not args.actions and not args.macro:
        if control_written:
            return
        raise SystemExit("Provide actions or choose --macro")

    actions_payload = build_actions(args, macros)
    frame = args.frame if args.frame is not None else int(time.time() * 1000)
    payload = {"frame": frame, "actions": actions_payload}

    out_path = Path(args.output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload))
    print(f"Wrote {len(actions_payload)} action(s) at frame {frame} to {out_path}")


if __name__ == "__main__":
    main()
