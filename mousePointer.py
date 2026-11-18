import pyautogui
import time

print("マウスの現在位置を表示します。終了するには Ctrl+C を押してください。")

try:
    while True:
        x, y = pyautogui.position()
        print(f"X: {x}, Y: {y}")
        time.sleep(0.5)
except KeyboardInterrupt:
    print("\n終了しました。")
