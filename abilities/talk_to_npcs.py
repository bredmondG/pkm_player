from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from state_stream import GameState, PokemonStateStreamer

from state_stream import AbilityResult


class TalkToNPCsAbility:
    """Mashes A whenever a dialog/text box is open in the overworld."""

    name = "talk_to_npcs"
    priority = 80

    def __init__(self, mash_interval_frames: int = 8):
        self.mash_interval_frames = mash_interval_frames
        self._next_allowed_frame = 0

    def should_run(self, state: "GameState", streamer: "PokemonStateStreamer") -> bool:
        if state.in_battle:
            return False
        if not state.dialog_open:
            return False
        return streamer._frame >= self._next_allowed_frame

    def run(self, state: "GameState", streamer: "PokemonStateStreamer") -> AbilityResult:
        self._next_allowed_frame = streamer._frame + self.mash_interval_frames
        return AbilityResult(actions=["A"], cooldown_frames=self.mash_interval_frames)


def register(streamer: "PokemonStateStreamer") -> None:
    streamer.register_ability(TalkToNPCsAbility())
