import pyautogui
import time
import os
import datetime
from PIL import ImageGrab, Image

# ユーザー指定のスクリーンショット座標
left = 540    # 左上X
top = 0     # 左上Y
right = 1380  # 右下X
bottom = 1080  # 右下Y

# PDFページサイズ（A4@300dpi）
PDF_WIDTH = 2480
PDF_HEIGHT = 3508

# スクリーンショット回数と間隔
page = 750
span = 0.2
h_foldername = "output"
h_filename = "picture"

# 出力フォルダ作成
folder_name = f"{h_foldername}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
os.makedirs(folder_name, exist_ok=True)

print("Kindle画面に移動してください（5秒後に開始）")
time.sleep(5)

for p in range(page):
    out_filename = f"{h_filename}_{str(p+1).zfill(4)}.png"

    # スクリーンショット範囲を取得
    screenshot = ImageGrab.grab(bbox=(left, top, right, bottom)).convert('RGB')

    # サイズ取得とスケール比率計算
    input_w, input_h = screenshot.size
    input_ratio = input_w / input_h
    pdf_ratio = PDF_WIDTH / PDF_HEIGHT

    # 拡大率計算（縦横比維持しながらA4に収める）
    if input_ratio > pdf_ratio:
        scale = PDF_WIDTH / input_w
    else:
        scale = PDF_HEIGHT / input_h

    new_w = int(input_w * scale)
    new_h = int(input_h * scale)

    # 拡大後の画像を生成
    resized_img = screenshot.resize((new_w, new_h), Image.LANCZOS)

    # 白紙A4キャンバスに中央貼り付け
    canvas = Image.new('RGB', (PDF_WIDTH, PDF_HEIGHT), color='white')
    paste_x = (PDF_WIDTH - new_w) // 2
    paste_y = (PDF_HEIGHT - new_h) // 2
    canvas.paste(resized_img, (paste_x, paste_y))

    # 保存
    canvas.save(os.path.join(folder_name, out_filename))
    pyautogui.press('right')
    time.sleep(span)
