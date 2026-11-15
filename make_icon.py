# -*- coding: utf-8 -*-
"""
「应用音量控制器」logo —— 三柱均衡版（与程序内左上角标识同源）。

设计：
  * 三根圆头竖条，高低错落、**垂直居中** —— 递增会读成「手机信号格」，
    共底会读成「柱状图」，只有居中错落才是均衡器
  * 嵌在一块圆角方片里，方片只做底、不加阴影
  * 纯几何、单色，16px 下仍然分得清

颜色：深色方片 + 反白竖条 + 1px 细边（细边是为了在深色任务栏上不糊掉）。

运行：
  python make_icon.py

输出：logo.png（512 预览）/ app_volume.ico（16~256 多尺寸）
      icon_preview.png（深底/浅底各一排的尺寸对照）
"""

import io
import os
import struct

from PIL import Image, ImageDraw

BASE = 512
SS = 8                       # 超采样倍数，缩小后边缘平滑
OUT = os.path.dirname(os.path.abspath(__file__))

TILE = (13, 14, 16, 255)     # #0D0E10，和界面面板同一档
EDGE = (44, 47, 49, 255)     # #2C2F31，1px 细边
BAR = (244, 244, 246, 255)   # #F4F4F6，主文字色

ICO_NAME = "app_volume_icon.ico"   # 换过名字，避开 Windows 按路径缓存的旧图标
RADIUS = 0.23                # 方片圆角 / 边长
BW = 5.0 / 36.0              # 竖条宽度
GAP = 4.2 / 36.0             # 竖条间距
HS = (12.0 / 36.0, 26.0 / 36.0, 18.0 / 36.0)   # 三根高度，垂直居中


def draw(size, ss=SS):
    """按边长 size 画一张 logo（内部超采样后缩小）。"""
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    r = RADIUS * n
    edge = max(1.0, n * 0.008)        # 细边随尺寸缩放，16px 下约 0.5px
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=r, fill=TILE)
    d.rounded_rectangle([edge / 2, edge / 2, n - 1 - edge / 2, n - 1 - edge / 2],
                        radius=max(0.0, r - edge / 2), outline=EDGE,
                        width=max(1, int(round(edge))))

    # 小尺寸光学修正：16/20/24 下按比例画出来竖条只有 2px 出头，
    # 缩完就糊；这里把笔画加粗、间距收窄，保持同样的总宽度
    if size <= 24:
        bw = 6.4 / 36.0 * n
        gap = 3.4 / 36.0 * n
    else:
        bw = BW * n
        gap = GAP * n
    total = len(HS) * bw + (len(HS) - 1) * gap
    left = (n - total) / 2.0
    for h in HS:
        hh = h * n
        top = (n - hh) / 2.0
        d.rounded_rectangle([left, top, left + bw, top + hh],
                            radius=bw / 2.0, fill=BAR)
        left += bw + gap

    return img.resize((size, size), Image.LANCZOS)


def write_ico(path, sizes):
    """手写 ICO：每个尺寸独立渲染后以 PNG 形式内嵌。

    PIL 自带的 ICO 保存只能拿一张源图统一缩放，小尺寸会糊；
    这里每个尺寸都按矢量比例重画一遍，16px 也清楚。
    """
    blobs = []
    for s in sizes:
        buf = io.BytesIO()
        draw(s).save(buf, format="PNG")
        blobs.append((s, buf.getvalue()))

    head = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(head) + 16 * len(blobs)
    entries = b""
    for s, data in blobs:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(head + entries + b"".join(d for _s, d in blobs))


def write_svg(path, size=BASE):
    """同一套几何输出矢量版，方便放大或印在别处。"""
    def hx(c):
        return "#%02X%02X%02X" % c[:3]

    r = RADIUS * size
    edge = size * 0.008
    bw = BW * size
    gap = GAP * size
    total = len(HS) * bw + (len(HS) - 1) * gap
    left = (size - total) / 2.0
    bars = []
    for h in HS:
        hh = h * size
        top = (size - hh) / 2.0
        bars.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                    'rx="%.2f" fill="%s"/>'
                    % (left, top, bw, hh, bw / 2.0, hx(BAR)))
        left += bw + gap

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'width="%d" height="%d">\n'
        '  <rect width="%d" height="%d" rx="%.2f" fill="%s"/>\n'
        '  <rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" '
        'fill="none" stroke="%s" stroke-width="%.2f"/>\n'
        '  %s\n'
        '</svg>\n'
        % (size, size, size, size,
           size, size, r, hx(TILE),
           edge / 2, edge / 2, size - edge, size - edge,
           max(0.0, r - edge / 2), hx(EDGE), edge,
           "\n  ".join(bars)))
    io.open(path, "w", encoding="utf-8").write(svg)


def main():
    draw(BASE).save(os.path.join(OUT, "logo.png"))
    write_svg(os.path.join(OUT, "logo.svg"))

    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    write_ico(os.path.join(OUT, ICO_NAME), sizes)

    # 对照板：深底 / 浅底各一排，看小尺寸是否还认得出
    show = [16, 24, 32, 48, 64, 128, 256]
    pad, gap = 20, 24
    width = pad * 2 + sum(s + gap for s in show)
    rowh = pad * 2 + max(show)
    sheet = Image.new("RGB", (width, rowh * 2), (255, 255, 255))
    for row, bg in enumerate(((18, 19, 21), (247, 247, 248))):
        y0 = row * rowh
        ImageDraw.Draw(sheet).rectangle([0, y0, width, y0 + rowh - 1], fill=bg)
        x = pad
        for s in show:
            im = draw(s)
            sheet.paste(im, (x, y0 + pad), im)
            x += s + gap
    sheet.save(os.path.join(OUT, "icon_preview.png"))

    print("已生成 logo.png / logo.svg / %s (%s) / icon_preview.png"
          % (ICO_NAME, " ".join(str(s) for s in sizes)))


if __name__ == "__main__":
    main()
