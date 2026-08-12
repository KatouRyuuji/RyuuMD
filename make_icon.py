"""生成 RyuuMD 应用图标：圆角纯色（phycat sky 蓝）+ 白色 md。
   输出 assets/icon.ico（多尺寸）与 assets/icon.png（512）。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BLUE = (52, 152, 219, 255)   # phycat sky 核心蓝 #3498db
WHITE = (255, 255, 255, 255)
OUT = Path(__file__).parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)


def find_font(size: int):
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf",   # Segoe UI Bold
        r"C:\Windows\Fonts\arialbd.ttf",    # Arial Bold
        str(Path(__file__).parent / "app" / "web" / "assets" / "fonts" / "Cascadia-Code-Regular.ttf"),
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make(size: int) -> Image.Image:
    # 4x 超采样，保证圆角与文字平滑
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(s * 0.22)  # 圆角矩形
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=BLUE)

    text = "md"
    font = find_font(int(s * 0.46))
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (s - tw) / 2 - bbox[0]
    y = (s - th) / 2 - bbox[1]
    d.text((x, y), text, font=font, fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [make(s) for s in sizes]
    ico_path = OUT / "icon.ico"
    imgs[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
    make(512).save(OUT / "icon.png", format="PNG")
    # 同步一份到 web 资源，供前端 favicon 用
    web_assets = Path(__file__).parent / "app" / "web" / "assets"
    web_assets.mkdir(parents=True, exist_ok=True)
    make(64).save(web_assets / "favicon.png", format="PNG")
    print("icon.ico ->", ico_path, ico_path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
