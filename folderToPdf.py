from PIL import Image
import os
import sys

# フォルダ名を取得
folder_name = input("PDFに変換したいフォルダ名を入力してください：").strip()

if not os.path.isdir(folder_name):
    print(f"フォルダ '{folder_name}' が見つかりません。正しいパスを指定してください。")
    sys.exit(1)

# PNGファイルを取得
image_files = sorted(
    [f for f in os.listdir(folder_name) if f.lower().endswith('.png')]
)

if not image_files:
    print("PNGファイルが見つかりません。")
    sys.exit(1)

# 最初の画像
first_image = Image.open(os.path.join(folder_name, image_files[0])).convert('RGB')
rest_images = [
    Image.open(os.path.join(folder_name, f)).convert('RGB')
    for f in image_files[1:]
]

# PDF出力ファイル名
pdf_filename = f"{folder_name}.pdf"

# DPI（画質）指定付きで保存
first_image.save(pdf_filename, save_all=True, append_images=rest_images, dpi=(300, 300))

print(f"PDFを保存しました（高DPI設定）: {pdf_filename}")
