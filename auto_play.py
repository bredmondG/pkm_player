import os
import time

# You may need to install a Game Boy emulator library such as 'pyboy'
# Run: pip install pyboy
from pyboy import PyBoy
from pyboy.utils import WindowEvent

# Define the path to the Game Boy ROM file
rom_path = "pokemon_red.gb"

# Ensure the rom_path exists
if not os.path.exists(rom_path):
    raise FileNotFoundError(f"The ROM file {rom_path} does not exist.")

# Initialize the emulator
pyboy = PyBoy(rom_path)

# Force the window into fullscreen right after startup so gameplay always begins maximized.
pyboy.send_input(WindowEvent.FULL_SCREEN_TOGGLE)

# Run the emulator for a certain number of frames
def play_game(frames=1000, frame_duration=0.01):
    for frame in range(frames):
        pyboy.tick()  # Advance the emulator by one frame
        time.sleep(frame_duration)  # Pause to match real-time playback
        
    # Either implement logic to interact with the game here or just let it run

try:
    play_game()
finally:
    pyboy.stop()  # Ensure the emulator shutdown cleanly

print("Pokemon Red is now playing...")