"""从品牌素材 logo.png 生成 RyuuMD 全部图标。

大尺寸（≥64px）用整张 logo；小尺寸（≤48px）用有希头像特写裁切 + 圆角，
整张 logo 在小尺寸下文字与人物会糊成一团。
输出：
  assets/icon.ico          Windows 多尺寸图标（exe、窗口、文件关联、安装包）
  assets/icon.png          1024 整张，build-mac.sh 生成 icns 的大尺寸源
  assets/icon-small.png    256 头像，build-mac.sh 生成 icns 的 16/32pt 源
  app/web/assets/favicon.png  128 头像，前端 favicon 与标题栏/欢迎页徽标
  packaging/wizard*.bmp    Inno Setup 向导大图（左侧竖幅）与小图（右上角），2x 尺寸
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
SRC = ROOT / "logo.png"
HEAD_BOX = (560, 210, 1420, 1070)  # logo.png（2048²）中头像特写的正方形区域
SMALL_MAX = 48
PAPER = (247, 243, 240)  # logo 卡片纸色，向导大图底色


def full(size: int) -> Image.Image:
    return Image.open(SRC).convert("RGBA").resize((size, size), Image.LANCZOS)


def head(size: int) -> Image.Image:
    img = Image.open(SRC).convert("RGBA").crop(HEAD_BOX)
    side = img.width
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, side - 1, side - 1], radius=int(side * 0.22), fill=255)
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


def icon(size: int) -> Image.Image:
    return head(size) if size <= SMALL_MAX else full(size)


def on_paper(img: Image.Image, w: int, h: int, color=PAPER) -> Image.Image:
    bg = Image.new("RGB", (w, h), color)
    bg.paste(img, ((w - img.width) // 2, (h - img.height) // 2), img)
    return bg


def main() -> None:
    assets = ROOT / "assets"
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [icon(s) for s in sizes]
    imgs[-1].save(assets / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    full(1024).save(assets / "icon.png", format="PNG")
    head(256).save(assets / "icon-small.png", format="PNG")
    head(128).save(ROOT / "app" / "web" / "assets" / "favicon.png", format="PNG")
    packaging = ROOT / "packaging"
    on_paper(full(320), 328, 628).save(packaging / "wizard.bmp")
    # 小图在向导白底顶栏
    on_paper(head(110), 110, 110, (255, 255, 255)).save(packaging / "wizard-small.bmp")
    print("icons ->", assets)


if __name__ == "__main__":
    main()
