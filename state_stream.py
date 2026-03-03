#!/usr/bin/env python3
"""
state_stream.py
----------------
Runs the Pokémon Red ROM inside PyBoy, snapshots selected pieces of game state at
regular intervals, and provides a hook for decision logic to drive button presses.

Requirements:
    pip install pyboy

Usage:
    python state_stream.py --rom Pokemon_red.gb

This script will append JSON lines to state_stream.log with the latest snapshot so
an external agent (like this assistant) can review the data and suggest decisions.
Optionally, use --state-out to overwrite a single-file snapshot for easy sharing,
--actions-in to read JSON instructions like {"frame": 1234, "actions": ["UP", "A"]},
and --save to point at a battery save (.sav/.ram) that should be loaded on boot.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import signal
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from collections import defaultdict
from collections import deque

from pyboy import PyBoy

try:
    from pyboy import WindowEvent
except (ImportError, AttributeError):
    try:
        from pyboy.utils import WindowEvent  # type: ignore
    except ImportError:
        from pyboy.windowevent import WindowEvent  # type: ignore

# Known memory addresses for Pokémon Red (sourced from pret disassembly docs)
STATE_ADDRESSES: Dict[str, int] = {
    "player_x": 0xD361,
    "player_y": 0xD362,
    "map_id": 0xD35E,
    "in_battle_flag": 0xD057,
    # Party Pokémon 1 HP (stored as big-endian word)
    "party1_cur_hp_hi": 0xD16B,
    "party1_max_hp_hi": 0xD16D,
    # Additional state bytes
    "game_state": 0xD730,
    "text_box_id": 0xCFC6,
    "joy_ignore": 0xCFC8,
    "player_direction": 0xD05B,
    "party_count": 0xD163,
    "party1_status": 0xD16F,
}

BUTTON_MAP = {
    "A": WindowEvent.PRESS_BUTTON_A,
    "B": WindowEvent.PRESS_BUTTON_B,
    "START": WindowEvent.PRESS_BUTTON_START,
    "SELECT": WindowEvent.PRESS_BUTTON_SELECT,
    "UP": WindowEvent.PRESS_ARROW_UP,
    "DOWN": WindowEvent.PRESS_ARROW_DOWN,
    "LEFT": WindowEvent.PRESS_ARROW_LEFT,
    "RIGHT": WindowEvent.PRESS_ARROW_RIGHT,
}

BUTTON_RELEASE_MAP = {
    "A": WindowEvent.RELEASE_BUTTON_A,
    "B": WindowEvent.RELEASE_BUTTON_B,
    "START": WindowEvent.RELEASE_BUTTON_START,
    "SELECT": WindowEvent.RELEASE_BUTTON_SELECT,
    "UP": WindowEvent.RELEASE_ARROW_UP,
    "DOWN": WindowEvent.RELEASE_ARROW_DOWN,
    "LEFT": WindowEvent.RELEASE_ARROW_LEFT,
    "RIGHT": WindowEvent.RELEASE_ARROW_RIGHT,
}


def seconds_to_frames(seconds: float, frames_per_tick: int) -> int:
    return max(0, int(round(seconds * frames_per_tick)))


@dataclass
class GameState:
    frame: int
    timestamp: float
    in_battle: bool
    map_id: int
    player_x: int
    player_y: int
    party1_hp: int
    party1_max_hp: int
    game_state: int
    text_box_id: int
    joy_ignore: int
    player_direction: int
    party_count: int
    party1_status: int
    dialog_open: bool
    input_locked: bool


class PokemonStateStreamer:
    def __init__(
        self,
        rom_path: Path,
        log_path: Path,
        frames_per_tick: int = 60,
        state_out_path: Optional[Path] = None,
        actions_in_path: Optional[Path] = None,
        save_path: Optional[Path] = None,
        hold_frames: int = 30,
        auto_actions: bool = True,
        auto_toggle_path: Optional[Path] = None,
    ):
        self.rom_path = rom_path
        self.log_path = log_path
        self.frames_per_tick = frames_per_tick
        self.hold_frames = hold_frames
        self.auto_actions_enabled = auto_actions
        self.auto_toggle_path = auto_toggle_path
        self.state_out_path = state_out_path
        self.actions_in_path = actions_in_path
        self.save_path = save_path
        self.state_path = self.rom_path.with_suffix(self.rom_path.suffix + ".state")
        self._ram_buffer: Optional[io.BytesIO] = None
        self._auto_toggle_mtime: float = 0.0

        pyboy_kwargs = {}
        if self.save_path and self.save_path.exists():
            self._ram_buffer = io.BytesIO(self.save_path.read_bytes())
            self._ram_buffer.seek(0)
            pyboy_kwargs["ram_file"] = self._ram_buffer
            print(f"[state_stream] Loaded save data from {self.save_path}")

        self._pyboy = PyBoy(str(self.rom_path), **pyboy_kwargs)
        # Ensure the gameplay window starts fullscreen immediately.
        self._pyboy.send_input(WindowEvent.FULL_SCREEN_TOGGLE)
        if self.state_path.exists():
            try:
                with self.state_path.open("rb") as fh:
                    self._pyboy.load_state(fh)
                print(f"[state_stream] Loaded emulator state from {self.state_path}")
            except Exception as exc:
                print(f"[state_stream] Failed to load state {self.state_path}: {exc}")
        self._frame = 0
        self._last_action_frame = -1
        self._next_auto_action_frame = 0
        self._last_action_source: Optional[str] = None
        self._pending_press_logs: List[tuple[int, str, int]] = []
        self._position_history: Deque[tuple[int, int]] = deque(maxlen=30)
        self._overworld_direction_cycle = deque(["RIGHT", "UP", "LEFT", "DOWN"])
        self._battle_step = 0
        self._prev_game_state: Optional[GameState] = None
        self._recent_tiles: Deque[str] = deque(maxlen=200)
        self.map_learning_path = Path("map_learning.json")
        self._map_graph: Dict[str, Dict[str, Dict[str, int]]] = self._load_map_learning()
        self._map_dirty = False

        # Make sure log directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("a", buffering=1)

        if self.state_out_path:
            self.state_out_path.parent.mkdir(parents=True, exist_ok=True)

        signal.signal(signal.SIGINT, self._close_on_signal)
        signal.signal(signal.SIGTERM, self._close_on_signal)

    def _tile_key(self, map_id: int, x: int, y: int) -> str:
        return f"{map_id}:{x}:{y}"

    def _load_map_learning(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        path = self.map_learning_path
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return data
            except json.JSONDecodeError:
                print(f"[state_stream] Failed to parse {path}, starting fresh")
        return {}

    def _save_map_learning(self) -> None:
        if not self._map_dirty:
            return
        self.map_learning_path.write_text(json.dumps(self._map_graph, indent=2))
        self._map_dirty = False

    def _refresh_auto_toggle(self) -> None:
        if not self.auto_toggle_path:
            return
        try:
            path = self.auto_toggle_path
            if not path.exists():
                return
            mtime = path.stat().st_mtime
            if mtime <= self._auto_toggle_mtime:
                return
            data = json.loads(path.read_text())
        except Exception:
            return
        enabled = data.get("auto_enabled")
        if isinstance(enabled, bool):
            if enabled != self.auto_actions_enabled:
                status = "enabled" if enabled else "disabled"
                print(f"[state_stream] Auto-actions {status} via {path}")
            self.auto_actions_enabled = enabled
            self._auto_toggle_mtime = mtime

    def _normalize_actions(self, actions: Iterable[Any]) -> List[Dict[str, int]]:
        normalized: List[Dict[str, int]] = []
        for entry in actions:
            if isinstance(entry, str):
                button = entry.upper()
                normalized.append(
                    {
                        "button": button,
                        "delay_frames": 0,
                        "hold_frames": self.hold_frames,
                    }
                )
                continue
            if isinstance(entry, dict):
                raw_button = entry.get("button") or entry.get("action")
                if not raw_button:
                    continue
                button = str(raw_button).upper()
                delay_frames = entry.get("delay_frames")
                if isinstance(delay_frames, (int, float)):
                    delay_frames = max(0, int(delay_frames))
                else:
                    delay_seconds = entry.get("delay_seconds") or entry.get("delay") or 0
                    if not isinstance(delay_seconds, (int, float)):
                        delay_seconds = 0
                    delay_frames = seconds_to_frames(delay_seconds, self.frames_per_tick)

                hold_frames = entry.get("hold_frames")
                if isinstance(hold_frames, (int, float)):
                    hold_frames = max(1, int(hold_frames))
                else:
                    hold_seconds = entry.get("hold_seconds") or entry.get("hold")
                    if isinstance(hold_seconds, (int, float)):
                        hold_frames = max(1, seconds_to_frames(hold_seconds, self.frames_per_tick))
                    else:
                        hold_frames = self.hold_frames

                normalized.append(
                    {
                        "button": button,
                        "delay_frames": delay_frames,
                        "hold_frames": hold_frames,
                    }
                )
        return normalized

    def _drain_pending_press_logs(self) -> None:
        if not self._pending_press_logs:
            return
        ready = []
        future = []
        for target_frame, button, hold in self._pending_press_logs:
            if target_frame <= self._frame:
                ready.append((target_frame, button, hold))
            else:
                future.append((target_frame, button, hold))
        self._pending_press_logs = future
        for target_frame, button, hold in ready:
            print(f"[state_stream] Frame {target_frame}: sending {button} (hold={hold} frames)")


    def _update_map_learning(self, state: GameState) -> None:
        if self._prev_game_state is None:
            self._prev_game_state = state
            tile_key = self._tile_key(state.map_id, state.player_x, state.player_y)
            self._recent_tiles.append(tile_key)
            return

        prev = self._prev_game_state
        curr = state
        prev_key = self._tile_key(prev.map_id, prev.player_x, prev.player_y)
        curr_key = self._tile_key(curr.map_id, curr.player_x, curr.player_y)
        if curr_key != prev_key:
            dx = curr.player_x - prev.player_x
            dy = curr.player_y - prev.player_y
            direction = None
            if curr.map_id != prev.map_id:
                direction = "WARP"
            elif dx == 0 and dy == -1:
                direction = "UP"
            elif dx == 0 and dy == 1:
                direction = "DOWN"
            elif dx == 1 and dy == 0:
                direction = "RIGHT"
            elif dx == -1 and dy == 0:
                direction = "LEFT"

            if direction:
                entry = self._map_graph.setdefault(prev_key, {})
                entry[direction] = {"map_id": curr.map_id, "x": curr.player_x, "y": curr.player_y}
                self._map_dirty = True
            self._recent_tiles.append(curr_key)
        self._prev_game_state = state

    def _choose_known_direction(self, tile_key: str) -> Optional[str]:
        neighbors = self._map_graph.get(tile_key, {})
        if not neighbors:
            return None
        recent_set = set(self._recent_tiles)
        for direction, dest in neighbors.items():
            if direction == "WARP":
                continue
            dest_key = self._tile_key(dest["map_id"], dest["x"], dest["y"])
            if dest_key not in recent_set:
                return direction
        # fallback to first non-warp neighbor
        for direction in neighbors:
            if direction != "WARP":
                return direction
        return None

    def _close_on_signal(self, *_):
        self.shutdown()
        raise SystemExit

    def read_state(self) -> GameState:
        memory = self._pyboy.memory

        def word(addr_hi: int) -> int:
            hi = memory[addr_hi]
            lo = memory[addr_hi + 1]
            return (hi << 8) | lo

        in_battle = memory[STATE_ADDRESSES["in_battle_flag"]] == 0x01
        party1_hp = word(STATE_ADDRESSES["party1_cur_hp_hi"])
        party1_max_hp = word(STATE_ADDRESSES["party1_max_hp_hi"])

        map_id = memory[STATE_ADDRESSES["map_id"]]
        player_x = memory[STATE_ADDRESSES["player_x"]]
        player_y = memory[STATE_ADDRESSES["player_y"]]
        game_state = memory[STATE_ADDRESSES["game_state"]]
        text_box_id = memory[STATE_ADDRESSES["text_box_id"]]
        joy_ignore = memory[STATE_ADDRESSES["joy_ignore"]]
        player_direction = memory[STATE_ADDRESSES["player_direction"]]
        party_count = memory[STATE_ADDRESSES["party_count"]]
        party1_status = memory[STATE_ADDRESSES["party1_status"]]

        return GameState(
            frame=self._frame,
            timestamp=time.time(),
            in_battle=in_battle,
            map_id=map_id,
            player_x=player_x,
            player_y=player_y,
            party1_hp=party1_hp,
            party1_max_hp=party1_max_hp,
            game_state=game_state,
            text_box_id=text_box_id,
            joy_ignore=joy_ignore,
            player_direction=player_direction,
            party_count=party_count,
            party1_status=party1_status,
            dialog_open=text_box_id != 0,
            input_locked=joy_ignore != 0,
        )

    def log_state(self, state: GameState) -> None:
        self._log_file.write(json.dumps(asdict(state)) + "\n")
        self._log_file.flush()

    def publish_state(self, state: GameState) -> None:
        self.log_state(state)
        if not self.state_out_path:
            return
        payload = json.dumps(asdict(state), indent=2)
        tmp_path = self.state_out_path.with_suffix(self.state_out_path.suffix + ".tmp")
        tmp_path.write_text(payload)
        tmp_path.replace(self.state_out_path)

    def read_external_actions(self) -> List[Any]:
        if not self.actions_in_path or not self.actions_in_path.exists():
            return []
        try:
            data = json.loads(self.actions_in_path.read_text())
        except json.JSONDecodeError:
            return []

        frame = data.get("frame", -1)
        actions = data.get("actions", [])
        if not isinstance(actions, list):
            return []

        if frame <= self._last_action_frame:
            return []

        self._last_action_frame = frame
        try:
            self.actions_in_path.unlink()
        except FileNotFoundError:
            pass
        return actions

    def _overworld_actions(self, state: GameState) -> List[str]:
        self._position_history.append((state.player_x, state.player_y))
        stagnated = len(set(self._position_history)) <= 2 and len(self._position_history) == self._position_history.maxlen

        tile_key = self._tile_key(state.map_id, state.player_x, state.player_y)
        if stagnated:
            print(f"[state_stream] Frame {self._frame}: overworld stagnation detected, rotating direction")
            preferred = self._choose_known_direction(tile_key)
            if preferred:
                return [preferred]
            self._overworld_direction_cycle.rotate(-1)
            self._position_history.clear()

        # Prefer exploring unknown directions first, using the current cycle order
        neighbors = self._map_graph.get(tile_key, {})
        for direction in list(self._overworld_direction_cycle):
            if direction not in neighbors:
                return [direction]

        # Alternate between walking and tapping A every few frames to interact
        if self._frame % 180 == 0:
            return ["A"]
        if neighbors:
            preferred = self._choose_known_direction(tile_key)
            if preferred:
                return [preferred]
        return [self._overworld_direction_cycle[0]]

    def _battle_actions(self, state: GameState) -> List[str]:
        # Simple scripted cycle that repeatedly chooses Fight -> first move -> mash through text
        sequence = [
            ["A"],  # advance dialog / choose Fight
            ["A"],  # confirm move
            ["A"],  # attack / confirm
            ["A"],  # advance text
            ["A"],
        ]
        if state.party1_hp < max(5, int(state.party1_max_hp * 0.3)):
            sequence = [["B"], ["DOWN"], ["A"], ["A"], ["A"]]  # attempt to run

        actions = sequence[self._battle_step % len(sequence)]
        self._battle_step += 1
        return actions

    def default_actions(self, state: GameState) -> List[str]:
        if state.in_battle:
            return self._battle_actions(state)
        self._battle_step = 0
        return self._overworld_actions(state)

    def gather_actions(self, state: GameState) -> List[str]:
        external = self.read_external_actions()
        if external:
            print(f"[state_stream] Applying external actions at frame {state.frame}: {external}")
            self._last_action_source = "external"
            return external
        if not self.auto_actions_enabled:
            return []
        if self._frame < self._next_auto_action_frame:
            return []
        actions = self.default_actions(state)
        self._last_action_source = "auto" if actions else None
        return actions

    def apply_actions(self, actions: Iterable[Any]) -> None:
        specs = self._normalize_actions(actions)
        if not specs:
            return
        max_end_delay = 0
        base_frame = self._frame
        for spec in specs:
            button = spec["button"]
            event = BUTTON_MAP.get(button)
            if not event:
                continue
            press_delay = spec["delay_frames"]
            hold_frames = spec["hold_frames"]
            release_event = BUTTON_RELEASE_MAP.get(button)
            self._pyboy.send_input(event, delay=press_delay)
            if release_event:
                self._pyboy.send_input(release_event, delay=press_delay + hold_frames)
            target_frame = base_frame + press_delay
            self._pending_press_logs.append((target_frame, button, hold_frames))
            max_end_delay = max(max_end_delay, press_delay + hold_frames)
        if self._last_action_source == "auto":
            self._next_auto_action_frame = base_frame + max_end_delay

    def run(self) -> None:
        print(f"[state_stream] Starting ROM {self.rom_path}")
        print(f"Logging snapshots to {self.log_path}")
        try:
            while True:
                for _ in range(self.frames_per_tick):
                    self._pyboy.tick()
                    self._frame += 1
                    self._drain_pending_press_logs()

                state = self.read_state()
                if getattr(self, '_prev_game_state', None) is None:
                    self._prev_game_state = state
                self._update_map_learning(state)
                self.publish_state(state)
                self._refresh_auto_toggle()

                actions = self.gather_actions(state)
                self.apply_actions(actions)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._pyboy is not None:
            print("[state_stream] Shutting down emulator")
            ram_handle = None
            try:
                if self.state_path:
                    try:
                        self.state_path.parent.mkdir(parents=True, exist_ok=True)
                        with self.state_path.open("w+b") as state_handle:
                            self._pyboy.save_state(state_handle)
                        print(f"[state_stream] Saved emulator state to {self.state_path}")
                    except Exception as exc:
                        print(f"[state_stream] Failed to save state {self.state_path}: {exc}")
                if self.save_path:
                    self.save_path.parent.mkdir(parents=True, exist_ok=True)
                    ram_handle = open(self.save_path, "w+b")
                    print(f"[state_stream] Saving cartridge RAM to {self.save_path}")
                self._pyboy.stop(ram_file=ram_handle)
            finally:
                if ram_handle:
                    ram_handle.close()
                self._pyboy = None
        if self._map_dirty:
            self._save_map_learning()
        if not self._log_file.closed:
            self._log_file.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Pokémon Red and stream state snapshots.")
    parser.add_argument("--rom", default="pokemon_red.gb", help="Path to the Pokémon Red ROM")
    parser.add_argument(
        "--log", default="state_stream.log", help="JSONL file to append state snapshots"
    )
    parser.add_argument(
        "--state-out",
        default=None,
        help="Write the latest state snapshot to this JSON file (overwritten each tick)",
    )
    parser.add_argument(
        "--actions-in",
        default=None,
        help="Read desired button presses from this JSON file (format: {\"frame\": n, \"actions\": [...]})",
    )
    parser.add_argument(
        "--frames-per-tick",
        type=int,
        default=60,
        help="How many emulator frames to run between snapshots (≈60 per second)",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="Optional path to a .sav/.ram file to load on startup and write back on shutdown",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=0.5,
        help="How long to keep each button pressed before auto-release",
    )
    parser.add_argument(
        "--auto-control",
        default="auto_control.json",
        help="Path to a JSON file used to toggle auto actions at runtime",
    )
    parser.add_argument(
        "--disable-auto-actions",
        action="store_true",
        help="Disable built-in overworld/battle inputs so only external commands run",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rom_path = Path(args.rom).expanduser().resolve()
    log_path = Path(args.log).expanduser().resolve()
    state_out_path = Path(args.state_out).expanduser().resolve() if args.state_out else None
    actions_in_path = Path(args.actions_in).expanduser().resolve() if args.actions_in else None
    save_path = Path(args.save).expanduser().resolve() if args.save else None
    auto_toggle_path = Path(args.auto_control).expanduser().resolve() if args.auto_control else None

    if not rom_path.exists():
        raise FileNotFoundError(f"ROM not found: {rom_path}")

    if save_path is None:
        candidate = rom_path.with_suffix(".sav")
        if candidate.exists():
            save_path = candidate

    hold_frames = max(1, int(args.hold_seconds * args.frames_per_tick))
    streamer = PokemonStateStreamer(
        rom_path,
        log_path,
        frames_per_tick=args.frames_per_tick,
        state_out_path=state_out_path,
        actions_in_path=actions_in_path,
        save_path=save_path,
        hold_frames=hold_frames,
        auto_actions=not args.disable_auto_actions,
        auto_toggle_path=auto_toggle_path,
    )
    streamer.run()


if __name__ == "__main__":
    main()
