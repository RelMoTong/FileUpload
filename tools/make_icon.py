"""
make_icon.py — 将 PNG 转换为多尺寸 ICO

使用方法：
1) 将 PNG 图标保存为 assets/app.png（建议 512x512，带透明背景）
2) 运行：python tools/make_icon.py
3) 生成的 ICO：assets/app.ico

如未安装 Pillow：
    pip install pillow
"""
from pathlib import Path

try:
    from PIL import Image
    try:
        # Pillow >= 10
        RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    except Exception:
        # 兼容旧版 Pillow
        RESAMPLE = getattr(Image, 'LANCZOS', getattr(Image, 'BICUBIC', getattr(Image, 'BILINEAR', getattr(Image, 'NEAREST', 0))))
except Exception as e:
    print("[错误] 需要安装 Pillow 才能执行转换：pip install pillow")
    raise

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "app.png"
ICO_PATH = ASSETS / "app.ico"

ASSETS.mkdir(exist_ok=True)

if not PNG_PATH.exists():
    raise FileNotFoundError(f"未找到源文件：{PNG_PATH}，请将图标保存为 app.png 后重试")

# 典型 ICO 多尺寸集合（Windows 支持的常见尺寸）
SIZES = [(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]

img = Image.open(PNG_PATH).convert("RGBA")

# 生成多尺寸位图
icons = []
for size in SIZES:
    icons.append(img.resize(size, RESAMPLE))

# 保存 ICO（Pillow 支持保存多尺寸）
ICO_PATH.write_bytes(b"")  # 确保可以覆盖
img.save(ICO_PATH, format='ICO', sizes=SIZES)

print(f"[完成] 已生成图标：{ICO_PATH}")
