from pyboy import PyBoy

pyboy = PyBoy("pokemon_red.gb", window="SDL2", cgb=True)
pyboy.set_emulation_speed(1)

while not pyboy.tick():
    pass