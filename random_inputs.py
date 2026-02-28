import pyautogui, random, time

buttons = ['up', 'down', 'left', 'right', 'z', 'x', 'enter', 'backspace']  # Map to your emulator's controls

weights = [20, 20, 20, 20, 15, 5, 1, 1]  # Movement and A are most likely


time.sleep(5)
for x in range(1000):
    # Pick a button randomly with weights
    key = random.choices(buttons, weights=weights, k=1)[0]
    print(key)
    pyautogui.keyDown(key)
    time.sleep(0.1)
    pyautogui.keyUp(key)
    time.sleep(0.2)