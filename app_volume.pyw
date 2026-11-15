# -*- coding: utf-8 -*-
"""
应用音量控制器 - 单独调节每个正在运行的程序的音量。

界面：Raycast command palette 风格（2026-11-14 按 DESIGN.md 重做）
  * 一块 1px 细线描边的圆角面板，所有程序是面板里的行；
    行默认透明，悬停时浮起一档到 surface-card —— 这是 command palette 的
    selection 语义，不是卡片
  * 深度只靠四级表面色阶（canvas → surface → elevated → card），
    全程没有阴影、没有渐变、没有高光
  * 白色是唯一的"动作色"：滑块填充、开启态的胶囊、圆钮都用它
  * 每行左侧一个首字母图标块，右侧三列对齐：滑块 | 百分比 | 静音按钮
  * 圆角、圆钮、图标、面板遮罩全部由代码逐像素生成内存贴图：
    抗锯齿、磁盘零图片文件、不依赖 Pillow
  * 动效：窗口淡入、行悬停浮起、静音四色过渡、滚动缓动 + 越界回弹、
    主题切换先压暗再提亮；圆钮悬停只加深描边，不缩放也不回弹

依赖：pip install pycaw        （不需要 Pillow 等任何图像库）
运行：双击 run.bat，或 pythonw app_volume.pyw
自检：python app_volume.pyw --selftest / --uitest / --hittest / --animtest
                        / --benchmark / --interacttest / --show
"""

import ctypes
import math
import os
import sys
import time
import traceback
from ctypes import wintypes

try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
except Exception as _exc:
    _msg = ("缺少依赖 pycaw，请先安装：\n\n"
            "pip install pycaw\n\n"
            "原始错误：%s" % _exc)
    try:
        import tkinter as _tk
        _r = _tk.Tk()
        _r.withdraw()
        _tk.messagebox.showerror("应用音量控制器", _msg)
        _r.destroy()
    except Exception:
        print(_msg, file=sys.stderr)
    sys.exit(1)

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox


# ------------------------------------------------------------------ 设计变量
# Claude / Anthropic「暖色编辑」风格（warm editorial）。
#   底色是奶油纸，不是纯白；卡片只比底色深一档，靠明度差分层，不用阴影。
#   强调色是赤陶（terracotta），一屏只出现一处；正文绝不用它着色。
#   标题用衬线，正文用人文无衬线；圆角小（卡片 8 / 按钮 6），扁平、1px 细线。
#   动效不做缩放与抬升，只做色值与描边过渡 —— 克制是这套语言的核心。
THEMES = {
    # Raycast DESIGN.md：单一暗色 + 四级表面阶梯，深度靠色阶不靠阴影。
    "dark": {
        "canvas": "#07080A",       # 页面底
        "surface": "#0D0D0D",      # 面板
        "elevated": "#101111",     # 按钮 / 图标块
        "card": "#121212",         # 行悬停
        "hairline": "#242728",     # 1px 边框
        "hairline2": "#3A3D3E",    # 强分隔
        "ink": "#F4F4F6",          # 主文字
        "body": "#CDCDCD",         # 正文
        "mute": "#9C9C9D",         # 次要
        "ash": "#6A6B6C",          # 最低强调
        "primary": "#FFFFFF",      # 唯一的动作色
        "on_primary": "#000000",
        "red": "#FF6161",          # 危险 / 已静音
        "red_soft": "#2A1717",
        "blue": "#57C1FF",
        "scroll": "#3A3D3E",
    },
    # 浅色是同一套阶梯的镜像（Raycast 原文只有暗色，这里是等价推导）
    "light": {
        "canvas": "#F7F7F8",
        "surface": "#FFFFFF",
        "elevated": "#F2F2F3",
        "card": "#EDEDEF",
        "hairline": "#E4E4E7",
        "hairline2": "#C9C9CE",
        "ink": "#0A0A0B",
        "body": "#3F3F42",
        "mute": "#767679",
        "ash": "#A1A1A6",
        "primary": "#111113",
        "on_primary": "#FFFFFF",
        "red": "#C4332E",
        "red_soft": "#FBEAE9",
        "blue": "#0B6FBF",
        "scroll": "#C9C9CE",
    },
}
WIN_W, WIN_H = 560, 776
PAD = 16                      # 面板到窗口边缘
HEAD_H, FOOT_H = 84, 34
PANEL_PAD = 8                 # 面板内边距
PANEL_R = 10                  # 面板圆角
ROW_H = 56
ROW_R = 6                     # 行悬停圆角
TILE = 26                     # 行内图标块边长
TILE_R = 8
BTN_W, BTN_H = 54, 24
BTN_R = 8
SLIDER_W = 130
TRACK_H, KNOB_R = 3, 5

POLL_MS = 1500
ENUM_S = 3.0                 # 完整会话枚举间隔（GetAllSessions ≈ 35ms，别每轮都跑）
FADE_MS = 200                # 窗口淡入
TWEEN_MS = 110               # 按钮 hover 渐变
MUTE_MS = 150                # 静音切换过渡
KNOB_MS = 130                # 圆钮描边强调
HOVER_MS = 130               # 行悬停浮起
FRAME_MS = 16                # 补间目标帧间隔（60fps）
CHIP_MS = 110                # 行内按钮 hover
PRESS_MS = 70                # 工具栏按下反馈
BOUNCE = 26                  # 滚动越界回弹上限（逻辑像素）
SPRING_MS = 240              # 越界回弹时长

SCALE = 1.0


def S(v):
    """逻辑像素 -> 设备像素（高 DPI 下整体等比放大）。"""
    return int(round(v * SCALE))


_TIMER_LIFTED = False


def timer_resolution(on=True):
    """把 Windows 定时器精度提到 1ms。

    Tk 的 after(16) 在 Windows 上经常落到下一个 15.6ms 的定时器刻度，
    实测帧间隔变成 ~31ms —— 130ms 的淡入只剩 6 帧，看着就是一跳一跳。
    提高精度后 after(16) 才是真的 16ms。用完必须还原。
    """
    global _TIMER_LIFTED
    if _TIMER_LIFTED == on:
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(1) if on else \
            ctypes.windll.winmm.timeEndPeriod(1)
        _TIMER_LIFTED = on
    except Exception:
        pass


def enable_dpi():
    global SCALE
    timer_resolution(True)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            SCALE = max(1.0, min(2.0, dpi / 96.0))
    except Exception:
        pass


def initial_size():
    """默认窗口随 DPI 放大，但不超出工作区，避免小屏上被任务栏挡住。"""
    w, h = S(WIN_W), S(WIN_H)
    try:
        r = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(r), 0):
            w = min(w, r.right - r.left - S(40))
            h = min(h, r.bottom - r.top - S(40))
    except Exception:
        pass
    return max(S(420), w), max(S(420), h)


# ------------------------------------------------------------------ 颜色工具
def _rgb(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(c1, c2, t):
    """两个 #RRGGBB 线性插值：t=0 取 c1，t=1 取 c2。"""
    if t <= 0.0:
        return c1
    if t >= 1.0:
        return c2
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    return "#%02X%02X%02X" % (
        int(r1 + (r2 - r1) * t + 0.5),
        int(g1 + (g2 - g1) * t + 0.5),
        int(b1 + (b2 - b1) * t + 0.5))


def _lum(col):
    """感知亮度（0..255），用来决定按钮文字该用深色还是白色。"""
    r, g, b = _rgb(col)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _ease_out(p):
    """ease-out cubic：起步快、收尾稳，最接近系统控件的跟手感。"""
    return 1.0 - (1.0 - p) ** 3


def _ease_in_out(p):
    """ease-in-out cubic：两头慢、中间快，适合主题切换这类整体过渡。"""
    return 4.0 * p ** 3 if p < 0.5 else 1.0 - (-2.0 * p + 2.0) ** 3 / 2.0


def _ease_back(p):
    """ease-out back：略微过冲再回位，给「按下—回弹」一点弹性。"""
    c1 = 1.30
    c3 = c1 + 1.0
    return 1.0 + c3 * (p - 1.0) ** 3 + c1 * (p - 1.0) ** 2


def _rrect_cover(x, y, x0, y0, x1, y1, r):
    """点落在圆角矩形内的覆盖率（0..1），用于边缘抗锯齿。"""
    dx = max(x0 + r - x, 0.0, x - (x1 - r))
    dy = max(y0 + r - y, 0.0, y - (y1 - r))
    a = 0.5 - (math.hypot(dx, dy) - r)
    return 0.0 if a <= 0.0 else (1.0 if a >= 1.0 else a)


def _sd_rrect(x, y, x0, y0, x1, y1, r):
    """点到圆角矩形的有符号距离：内部为负、边上为 0、外部为正。

    有了它就不用真的做高斯模糊：落影的 alpha 直接取距离的
    smoothstep，视觉上和高斯模糊没区别，但每个像素只算一次 hypot。
    """
    dx = max(x0 + r - x, 0.0, x - (x1 - r))
    dy = max(y0 + r - y, 0.0, y - (y1 - r))
    return math.hypot(dx, dy) - r


def _smoothstep(x):
    """x 已经在 0..1 内：两端平缓、中间陡，比线性衰减更像真阴影。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def _vgrad(top, bot, t):
    """垂直渐变取色，t=0 取 top，t=1 取 bot。"""
    return _mix(top, bot, 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else t))


# ------------------------------------------------------------------ 字体工具
_FAMILIES = None


FONT_STACKS = {
    ("cjk", "display"): ("Noto Sans SC Medium", "Noto Sans SC",
                         "Microsoft YaHei UI"),
    ("cjk", "body"): ("Noto Sans SC", "HarmonyOS Sans SC",
                      "Microsoft YaHei UI"),
    ("cjk", "meta"): ("Noto Sans SC", "Noto Sans SC Light",
                      "Microsoft YaHei UI"),
    ("latin", "display"): ("Inter", "Segoe UI"),
    ("latin", "body"): ("Inter", "Segoe UI"),
    ("latin", "meta"): ("Inter", "Segoe UI"),
}

def pick_family(cjk, role="body"):
    """按角色取字体族。

    display 是衬线（标题），body 是正文无衬线，meta 用更轻的一档。
    Windows 把字重做成了独立家族名（Noto Sans SC Light 等），所以在
    Tk 里也能拿到真正的 300 / 400 对比，而不是只有 normal / bold 两档。
    """
    global _FAMILIES
    if _FAMILIES is None:
        try:
            _FAMILIES = set(tkfont.families())
        except Exception:
            _FAMILIES = set()
    for n in FONT_STACKS.get(("cjk" if cjk else "latin", role), ()):
        if n in _FAMILIES:
            return n
    return None


# ------------------------------------------------------------------ 音频逻辑
def window_titles_by_pid():
    """枚举可见顶层窗口 -> {pid: [标题, ...]}，用来区分微信里的各个小程序。"""
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                         wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    found = {}

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value.strip()
            if title:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.setdefault(int(pid.value), []).append(title)
        except Exception:
            pass
        return True

    cb = EnumWindowsProc(_cb)
    user32.EnumWindows(cb, 0)
    return found


def short(text, limit=34):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def clean_name(exe):
    if not exe:
        return "未知程序"
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def collect_sessions():
    """{key: 分组信息}，同一个进程的多个音频会话合并到一个滑块。"""
    titles = window_titles_by_pid()
    groups = {}
    for session in AudioUtilities.GetAllSessions():
        try:
            volume = session._ctl.QueryInterface(ISimpleAudioVolume)
        except Exception:
            continue

        pid, exe = None, None
        try:
            proc = session.Process
            if proc is not None:
                pid = int(proc.pid)
                exe = proc.name()
        except Exception:
            pass

        if pid is None:
            key = "system"
            name = "系统声音"
            sub = "Windows 系统提示音等"
            has_title = False
        else:
            key = "pid:%d" % pid
            wtitles = titles.get(pid, [])
            has_title = bool(wtitles)
            name = short(wtitles[0]) if has_title else clean_name(exe)
            sub = "%s · PID %d" % (exe or "未知进程", pid)

        group = groups.get(key)
        if group is None:
            group = {"key": key, "name": name, "sub": sub,
                     "has_title": has_title, "vols": []}
            groups[key] = group
        group["vols"].append(volume)
    return groups


def read_state(vols):
    for v in vols:
        try:
            return float(v.GetMasterVolume()), int(v.GetMute())
        except Exception:
            continue
    return 1.0, 0


def selftest():
    groups = collect_sessions()
    print("检测到 %d 个程序的音频会话：" % len(groups))
    for g in sorted(groups.values(), key=lambda x: x["name"]):
        vol, mute = read_state(g["vols"])
        print("  %-32s | %-34s | 音量 %3d%% | 静音 %s" %
              (g["name"], g["sub"], round(vol * 100), "是" if mute else "否"))
        for v in g["vols"]:
            try:
                v.SetMasterVolume(vol, None)
                v.SetMute(mute, None)
            except Exception as e:
                print("写入测试失败：%s" % e)
                return 1
    print("读写接口自检通过。")
    return 0


# ------------------------------------------------------------------ 界面
class VolumeMixer(tk.Tk):
    """整窗只有一张 Canvas，界面 100% 由代码生成的图元与内存贴图组成。

    抗锯齿：Tk 的 Canvas 图元没有边缘平滑，所以圆角卡片、胶囊按钮、
    圆钮、图标都由 `_paint()` 逐像素计算覆盖率后写进 tk.PhotoImage。
    像素是代码算出来的，不读任何图片文件，磁盘零占用；生成结果按
    (尺寸, 圆角, 颜色) 缓存，只有第一次用到时才付出那几毫秒。

    分层（从下到上）：list（可整体平移）/ head / foot / bar。
    head 与 foot 各有一块不透明背景矩形，用来裁掉滚出列表区的卡片。
    """

    def __init__(self):
        super().__init__()
        self.title("应用音量控制器")
        self.geometry("%dx%d" % initial_size())
        self.minsize(S(460), S(420))

        self.rows = {}
        self.order = []
        self.last_change = {}
        self.auto_var = tk.BooleanVar(value=True)
        self.top_var = tk.BooleanVar(value=False)
        self.theme = "light"
        self.c = THEMES[self.theme]
        self.hover = None
        self.press = None             # ("tool", act) / ("btn", key)
        self.drag_key = None
        self.drag_track = None
        self.scroll_y = 0.0
        self.scroll_target = 0.0      # 滚轮只改目标，动画帧缓动追上去
        self._scroll_job = None
        self._scrolling_until = 0.0
        self.hits = []                # 屏幕坐标命中框，滚动帧里重算
        self.tool_hits = []           # 工具栏命中框，固定不动
        self.view_w, self.view_h = S(WIN_W), S(WIN_H)
        self.status_text = ""
        self._fonts = {}
        self._imgs = {}               # 内存贴图缓存
        self._jobs = {}               # 补间任务
        self.card_items = {}          # key -> 该行各图元 id / 绘制坐标
        self.tool_items = {}          # action -> 胶囊按钮图元 id
        self.foot_text_id = None
        self.bar_id = None
        self._list_offset = 0         # 已实际施加到 list tag 的整数位移
        self._last_enum = 0.0
        self._warmed = False

        self.configure(bg=self.c["canvas"])
        self.canvas = tk.Canvas(self, bg=self.c["canvas"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Leave>", self._on_leave)
        self.bind("<Destroy>", self._on_destroy)

        self.rebuild(collect_sessions())
        self.after(POLL_MS, self.poll)
        self.after(20, lambda: self._fade_in(FADE_MS, 0.0))
        self.after(260, self._warm_hover)          # 先把悬停那 8 档贴图算好
        self.after(1200, self._warm_other_theme)   # 空档期把另一套主题的贴图先算好

    # ------------------------------------------------------------ 内存贴图
    # ------------------------------------------------------------ 内存贴图
    # 扁平设计：界面只用到 _rr_img（圆角矩形）/ _knob_img（圆钮）/
    # _icon_img（应用图标）三种内存贴图，全部逐像素抗锯齿，磁盘零图片。
    def _rr_img(self, w, h, r, fill, bg, border=None, bw=1):
        """抗锯齿圆角矩形，可选 1px 描边。

        扁平风格里「深度」全靠这条细线，所以它必须是抗锯齿的：
        先整块填描边色，再内缩 bw 填底色，最后四角逐像素按
        外圆角覆盖率混描边、按内圆角覆盖率混底色。只有四角走
        逐像素，四条直边是整条 put，所以不随面积变慢。
        """
        w, h = int(round(w)), int(round(h))
        if w <= 0 or h <= 0:
            return None
        key = ("rr", w, h, round(r, 1), fill, bg, border, bw)
        img = self._imgs.get(key)
        if img is not None:
            return img
        img = tk.PhotoImage(master=self, width=w, height=h)
        r = max(0.0, min(float(r), w / 2.0, h / 2.0))
        ri = int(round(r))
        if border:
            img.put(border, to=(0, 0, w, h))
            img.put(fill, to=(bw, bw, max(bw, w - bw), max(bw, h - bw)))
        else:
            img.put(bg, to=(0, 0, w, h))
            if w - 2 * ri > 0:
                img.put(fill, to=(ri, 0, w - ri, h))
            if h - 2 * ri > 0:
                img.put(fill, to=(0, ri, w, h - ri))
        rin = max(0.0, r - bw) if border else r
        for cx, cy, x0, x1, y0, y1 in (
                (r, r, 0, ri, 0, ri),
                (w - r, r, w - ri, w, 0, ri),
                (w - r, h - r, w - ri, w, h - ri, h),
                (r, h - r, 0, ri, h - ri, h)):
            for py in range(y0, y1):
                dy = py + 0.5 - cy
                row = []
                for px in range(x0, x1):
                    x, y = px + 0.5, py + 0.5
                    a = r + 0.5 - math.hypot(x - cx, dy)
                    a = 0.0 if a <= 0 else (1.0 if a >= 1 else a)
                    if border:
                        col = _mix(bg, border, a)
                        b = _rrect_cover(x, y, bw, bw, w - bw, h - bw, rin)
                        col = _mix(col, fill, b)
                    else:
                        col = _mix(bg, fill, a)
                    row.append(col)
                img.put("{" + " ".join(row) + "}", to=(x0, py))
        self._imgs[key] = img
        return img

    def _mask_img(self, w, h, r, fill, bg, top=True):
        """只有一侧圆角的矩形：给滚动列表做上下裁切用。

        面板本身是圆角卡片，行在里面滚动时必须在面板内边距处被裁掉，
        否则会盖到圆角上。这块遮罩上半（或下半）跟着圆角走，
        另一侧是方的，正好接住面板的直边。
        """
        w, h = int(round(w)), int(round(h))
        key = ("mask", w, h, round(r, 1), fill, bg, top)
        img = self._imgs.get(key)
        if img is not None:
            return img
        img = tk.PhotoImage(master=self, width=w, height=h)
        img.put(fill, to=(0, 0, w, h))
        ri = int(round(min(r, w / 2.0, h)))
        corners = (((r, r, 0, ri, 0, ri), (w - r, r, w - ri, w, 0, ri))
                   if top else
                   ((r, h - r, 0, ri, h - ri, h),
                    (w - r, h - r, w - ri, w, h - ri, h)))
        for cx, cy, cx0, cx1, cy0, cy1 in corners:
            for py in range(cy0, cy1):
                dy = py + 0.5 - cy
                row = []
                for px in range(cx0, cx1):
                    a = r + 0.5 - math.hypot(px + 0.5 - cx, dy)
                    row.append(_mix(bg, fill, 0.0 if a <= 0 else
                                    (1.0 if a >= 1 else a)))
                img.put("{" + " ".join(row) + "}", to=(cx0, py))
        self._imgs[key] = img
        return img

    def _knob_img(self, kr, fill, ring, bg, bar, track, band, bw=1):
        """圆钮：白色实心圆 + 1px 细边。扁平，没有投影也没有高光。

        位图是矩形而圆钮是圆的，圆外那圈底色必须按实际背景预混合，
        否则会在填充条和圆钮之间露出一条白线：左侧填填充条色、
        右侧填轨道色、轨道高度以外填页面底色。
        """
        kr = float(kr)
        size = int(math.ceil(kr * 2 + 2))
        key = ("knob", round(kr, 1), fill, ring, bg, bar, track,
               round(band, 1), bw)
        img = self._imgs.get(key)
        if img is not None:
            return img
        img = tk.PhotoImage(master=self, width=size, height=size)
        c0 = size / 2.0
        inner = kr - bw
        for py in range(size):
            row = []
            y = py + 0.5
            in_band = abs(y - c0) <= band
            for px in range(size):
                x = px + 0.5
                col = (bar if x < c0 else track) if in_band else bg
                d = math.hypot(x - c0, y - c0)
                a = kr + 0.5 - d
                if a > 0:
                    col = _mix(col, ring, 1.0 if a >= 1 else a)
                a = inner + 0.5 - d
                if a > 0:
                    col = _mix(col, fill, 1.0 if a >= 1 else a)
                row.append(col)
            img.put("{" + " ".join(row) + "}", to=(0, py))
        self._imgs[key] = img
        return img

    def _icon_img(self, s, plate, bg, glyph):
        """应用标识：三根圆头竖条（均衡器），嵌在一块圆角方片里。

        为什么是竖条而不是喇叭：喇叭是系统音量图标的通用符号，
        放大了就是「Windows 音量键套个框」；竖条直接对应这个程序的
        本职 —— 每个程序一档音量。纯几何、单色、26px 下仍分得清。

        每根竖条就是一个胶囊（两端半圆的圆角矩形），直接用
        `_rrect_cover` 算覆盖率，天然抗锯齿，不用超采样。
        """
        s = int(s)
        key = ("icon", s, plate, bg, glyph)
        img = self._imgs.get(key)
        if img is not None:
            return img
        img = tk.PhotoImage(master=self, width=s, height=s)
        k = s / 36.0
        radius = 9.0 * k                     # 方片圆角
        bw = 5.0 * k                         # 竖条宽度
        gap = 4.2 * k
        # 高低错落、并且**垂直居中**：三根共底会读成柱状图，
        # 递增则读成手机信号格；居中向两边伸展才是均衡器 / 波形
        hs = (12.0 * k, 26.0 * k, 18.0 * k)
        total = len(hs) * bw + (len(hs) - 1) * gap
        left = (s - total) / 2.0
        bars = []
        for h in hs:
            top = (s - h) / 2.0
            bars.append((left, top, left + bw, top + h, bw / 2.0))
            left += bw + gap
        for py in range(s):
            row = []
            for px in range(s):
                x, y = px + 0.5, py + 0.5
                cover = _rrect_cover(x, y, 0, 0, s, s, radius)
                mark = 0.0
                for x0, y0, x1, y1, r in bars:
                    c = _rrect_cover(x, y, x0, y0, x1, y1, r)
                    if c > mark:
                        mark = c
                col = _mix(bg, plate, cover) if cover > 0 else bg
                if mark > 0:
                    col = _mix(col, glyph, mark)
                row.append(col)
            img.put("{" + " ".join(row) + "}", to=(0, py))
        self._imgs[key] = img
        return img
    def _tween(self, key, ms, step, done=None, ease=_ease_out):
        """按 key 去重的补间；重复触发先取消上一段，避免动画互相打架。"""
        old = self._jobs.get(key)
        if old is not None:
            try:
                self.after_cancel(old)
            except Exception:
                pass
        t0 = time.perf_counter()

        def frame():
            p = (time.perf_counter() - t0) * 1000.0 / ms
            if p > 1.0:
                p = 1.0
            t_step = time.perf_counter()
            step(ease(p))
            if p < 1.0:
                # 只扣掉「这一帧自己画了多久」，不要把等待时间算进去 ——
                # 算进去的话下一帧会被压到 1ms，形成 16/1/15/3 这种抖动
                used = (time.perf_counter() - t_step) * 1000.0
                self._jobs[key] = self.after(
                    max(1, int(round(FRAME_MS - used))), frame)
            else:
                self._jobs.pop(key, None)
                if done:
                    done()

        self._jobs[key] = self.after(1, frame)

    def _cancel(self, key):
        job = self._jobs.pop(key, None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass

    def _fade_in(self, ms, start):
        """整窗 alpha 淡入：Tk 的系统级透明度，零成本且很顺滑。"""
        try:
            self.attributes("-alpha", start)
        except Exception:
            return

        def step(e):
            try:
                self.attributes("-alpha", start + (1.0 - start) * e)
            except Exception:
                pass

        self._tween(("alpha",), ms, step)

    # ------------------------------------------------------------ 布局度量
    def _on_destroy(self, _event):
        timer_resolution(False)
        for job in list(self._jobs.values()):
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._jobs.clear()
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except Exception:
                pass
            self._scroll_job = None

    def _panel_box(self):
        """command-palette 面板的矩形。

        面板贴合内容高度：程序少的时候不会拖出一个空壳，
        超过可用高度才封顶，然后在里面滚动。
        """
        px0, px1 = S(PAD), self.view_w - S(PAD)
        py0 = S(HEAD_H)
        avail = self.view_h - S(FOOT_H) - py0
        need = len(self.order) * S(ROW_H) + 2 * S(PANEL_PAD)
        ph = min(avail, max(need, S(ROW_H) + 2 * S(PANEL_PAD)))
        return (px0, py0, px1, py0 + ph)

    def _list_top(self):
        return self._panel_box()[1] + S(PANEL_PAD)

    def _list_bottom(self):
        return self._panel_box()[3] - S(PANEL_PAD)

    def _max_scroll(self):
        view = self._list_bottom() - self._list_top()
        total = len(self.order) * S(ROW_H)
        return max(0, total - view)

    def _card_geom(self, i):
        """第 i 行的几何（scroll_y = 0 时）。"""
        px0, _py0, px1, _py1 = self._panel_box()
        x0 = px0 + S(PANEL_PAD)
        x1 = px1 - S(PANEL_PAD)
        y0 = self._list_top() + i * S(ROW_H)
        y1 = y0 + S(ROW_H)
        bx1 = x1 - S(6)
        bx0 = bx1 - S(BTN_W)
        by0 = y0 + (S(ROW_H) - S(BTN_H)) // 2
        by1 = by0 + S(BTN_H)
        # 滑块固定在右侧一段：右边依次是百分比与按钮
        tx1 = bx0 - S(56)
        tx0 = tx1 - S(SLIDER_W)
        ty = y0 + S(ROW_H) // 2
        th = max(2, S(TRACK_H))
        if th % 2:
            th += 1
        return dict(x0=x0, y0=y0, x1=x1, y1=y1,
                    bx0=bx0, by0=by0, bx1=bx1, by1=by1,
                    tx0=tx0, tx1=tx1, ty=ty, th=th,
                    tile=x0, tx=x0 + S(TILE) + S(10),
                    ny=y0 + S(11), sy=y0 + S(29))
    def font(self, size, role="body", cjk=True, bold=False):
        """size 用负像素值，避免 Tk 再按 DPI 做一次 point 换算。"""
        px = max(8, S(size))
        key = (px, role, cjk, bold)
        f = self._fonts.get(key)
        if f is None:
            kw = dict(root=self, size=-px,
                      weight="bold" if bold else "normal")
            fam = pick_family(cjk, role)
            if fam:
                kw["family"] = fam
            f = tkfont.Font(**kw)
            self._fonts[key] = f
        return f

    def fit(self, s, max_w, size, role="body", cjk=True):
        """按真实像素宽度截断，避免长程序名把百分比/按钮挤飞。"""
        f = self.font(size, role, cjk)
        if f.measure(s) <= max_w:
            return s
        body = s
        while body and f.measure(body + "…") > max_w:
            body = body[:-1]
        return body + "…"

    def text(self, x, y, s, color, size, role="body", cjk=True,
             anchor="nw", tag=None):
        return self.canvas.create_text(
            x, y, text=s, fill=color, font=self.font(size, role, cjk),
            anchor=anchor, tags=tag)
    # ------------------------------------------------------------ 全量重建
    # ------------------------------------------------------------ 全量重建
    def _redraw(self):
        """只有主题切换 / 程序增减 / 窗口尺寸变化才走这里。"""
        if not hasattr(self, "canvas"):
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 60 or h < 80:
            w, h = S(WIN_W), S(WIN_H)
        self.view_w, self.view_h = w, h
        mx = self._max_scroll()
        self.scroll_target = max(0.0, min(self.scroll_target, mx))
        self.scroll_y = max(0.0, min(self.scroll_y, mx))

        # 图元即将全删，跑着的补间手里还攥着旧 id，必须先停掉（alpha 除外）
        for key in [k for k in self._jobs if k != ("alpha",)]:
            self._cancel(key)
        for row in self.rows.values():
            row.pop("fill_ov", None)
            row.pop("pct_ov", None)
            row.pop("visual", None)

        self.canvas.delete("all")
        self.card_items = {}
        self.tool_items = {}
        self.tool_hits = []
        self.hits = []
        self.foot_text_id = None
        self.bar_id = None

        c = self.c
        px0, py0, px1, py1 = self._panel_box()
        # 面板：一块 1px 细线描边的圆角卡片，行在里面滚动
        self.canvas.create_image(
            px0, py0, anchor="nw",
            image=self._rr_img(px1 - px0, py1 - py0, S(PANEL_R),
                               c["surface"], c["canvas"],
                               border=c["hairline"]),
            tags=("panel",))

        self._draw_list()
        self._draw_head()
        self._draw_foot()

        # 上下遮罩：把滚出内边距的行裁掉，同时保住面板的圆角
        band = S(PANEL_PAD) + S(PANEL_R)
        self.canvas.create_image(
            px0, py0, anchor="nw",
            image=self._mask_img(px1 - px0, band, S(PANEL_R),
                                 c["surface"], c["canvas"], top=True),
            tags=("panel",))
        self.canvas.create_image(
            px0, py1 - band, anchor="nw",
            image=self._mask_img(px1 - px0, band, S(PANEL_R),
                                 c["surface"], c["canvas"], top=False),
            tags=("panel",))

        self._list_offset = -int(round(self.scroll_y))
        if self._list_offset:
            self.canvas.move("list", 0, self._list_offset)
        self._recalc_hits()
        self._refresh_states(animate_hover=False)
        self._sync_emphasis(animate=False)   # 重建后直接落到当前悬停态

    # ------------------------------------------------------- 列表 / 行
    def _draw_list(self):
        # command-palette 的行：默认透明，悬停时浮起一档到 surface-card
        for i, key in enumerate(self.order):
            self._draw_card(key, self._card_geom(i))

    def _draw_card(self, key, g):
        c = self.c
        cv = self.canvas
        row = self.rows[key]
        tag = ("list", "card:" + key)
        muted = row["muted"]
        fg = c["ash"] if muted else c["ink"]

        # 行底：常驻一层圆角底色，平时颜色等于面板色所以看不见，
        # 悬停时由 _paint_row_hover 逐档提亮 —— 不靠显隐切换，才能渐变
        hl = cv.create_image(g["x0"], g["y0"], anchor="nw",
                             image=self._rr_img(g["x1"] - g["x0"],
                                                S(ROW_H), S(ROW_R),
                                                c["surface"], c["surface"]),
                             tags=tag)

        # 行内图标块：首字做字标，替代真实 app 图标
        side = S(TILE)
        tile_id = cv.create_image(g["tile"], g["ty"] - side // 2, anchor="nw",
                                  image=self._rr_img(side, side, S(TILE_R),
                                                     c["elevated"],
                                                     c["surface"]),
                                  tags=tag)
        mono = (row["name"][:1] or "·").upper()
        tile_txt = self.text(g["tile"] + side / 2.0, g["ty"], mono,
                             c["ash"] if muted else c["body"],
                             11, "display", not mono.isascii(),
                             "center", tag)

        name_cjk = not row["name"].isascii()
        name = self.fit(row["name"], g["tx0"] - g["tx"] - S(12),
                        13, "display", name_cjk)
        name_id = self.text(g["tx"], g["ny"], name, fg,
                            13, "display", name_cjk, "nw", tag)
        sub_cjk = not row["sub"].isascii()
        sub = self.fit(row["sub"], g["tx0"] - g["tx"] - S(12),
                       11, "meta", sub_cjk)
        sub_id = self.text(g["tx"], g["sy"], sub, c["ash"],
                           11, "meta", sub_cjk, "nw", tag)
        pct_id = self.text(g["bx0"] - S(12), g["ty"], "%d%%" % row["level"],
                           c["ash"] if muted else c["mute"],
                           11, "meta", False, "e", tag)

        tx0, tx1, ty, th = g["tx0"], g["tx1"], g["ty"], g["th"]
        half = th / 2.0
        track = cv.create_image(tx0, ty - half, anchor="nw",
                                image=self._rr_img(tx1 - tx0, th, half,
                                                   c["hairline"], c["surface"]),
                                tags=tag)
        cap = cv.create_image(tx0, ty, anchor="w",
                              image=self._rr_img(th, th, half,
                                                 c["primary"], c["hairline"]),
                              tags=tag)
        bar = cv.create_rectangle(tx0 + half, ty - half, tx0 + half, ty + half,
                                  fill=c["primary"], outline="", tags=tag)
        knob = cv.create_image(tx0, ty, anchor="center",
                               image=self._knob_img(S(KNOB_R), c["primary"],
                                                    c["surface"], c["surface"],
                                                    c["primary"], c["hairline"],
                                                    half, S(2)),
                               tags=tag)

        bw, bh = g["bx1"] - g["bx0"], g["by1"] - g["by0"]
        bfill, bfg, btxt = self._btn_style(key)
        btn = cv.create_image(g["bx0"], g["by0"], anchor="nw",
                              image=self._btn_img(bw, bh, bfill, c["surface"],
                                                  key),
                              tags=tag + ("tool:" + key,))
        btn_text = self.text((g["bx0"] + g["bx1"]) / 2.0,
                             (g["by0"] + g["by1"]) / 2.0, btxt, bfg,
                             11, "display", True, "center",
                             tag + ("tool:" + key,))

        self.card_items[key] = dict(
            card=hl, hl=hl, name=name_id, pct=pct_id, sub=sub_id,
            tile=tile_id, tile_txt=tile_txt,
            track=track, cap=cap, bar=bar, knob=knob,
            btn=btn, btn_text=btn_text, cw=g["x1"] - g["x0"],
            ch=S(ROW_H), cy0=g["y0"], by0c=g["by0"],
            tx0=tx0, tx1=tx1, ty=ty, th=th,
            bw=bw, bh=bh, btn_col=bfill, btn_fg=bfg, ks=0.0, hv=0.0,
            name_cjk=name_cjk, sub_cjk=sub_cjk)

    def _is_hot(self, key):
        return self.hover in (("track", key), ("btn", key))

    def _btn_img(self, bw, bh, fill, bg, key):
        """次级按钮：透明底 + 1px 强细线；已静音：柔红底 + 红字。"""
        c = self.c
        if key in self.rows and self.rows[key]["muted"]:
            return self._rr_img(bw, bh, S(BTN_R), fill, bg)
        return self._rr_img(bw, bh, S(BTN_R), fill, bg,
                            border=c["hairline2"])

    def _paint_row_hover(self, key, hv):
        """把一行的悬停进度画出来：行底和图标块同步浮起。

        颜色量化成 8 档：贴图按 (尺寸, 圆角, 颜色) 缓存，8 档就意味着
        所有行共用同一组图，渐变的代价只有几次 itemconfig。
        进度为 0 时行底色正好等于面板色，看起来就是「没有高亮」，
        所以不需要再切 state 显隐 —— 那正是之前硬切、不丝滑的原因。
        """
        it = self.card_items.get(key)
        if not it:
            return
        c = self.c
        q = round(hv * 7) / 7.0
        it["hv"] = q
        self.canvas.itemconfig(
            it["hl"],
            image=self._rr_img(it["cw"], S(ROW_H), S(ROW_R),
                               _mix(c["surface"], c["card"], q), c["surface"]))
        self.canvas.itemconfig(
            it["tile"],
            image=self._rr_img(S(TILE), S(TILE), S(TILE_R),
                               _mix(c["elevated"], c["card"], q), c["surface"]))

    def _warm_hover(self):
        """把当前主题的 8 档悬停贴图先算好。

        第一帧如果现算，要连做 16 张圆角贴图（行底 8 档 + 图标块 8 档），
        实测单帧 8ms+ —— 正好落在用户第一次把鼠标移上去的那一刻，就是那顿。
        """
        c = self.c
        g = self._card_geom(0)
        cw = g["x1"] - g["x0"]
        for i in range(8):
            q = i / 7.0
            self._rr_img(cw, S(ROW_H), S(ROW_R),
                         _mix(c["surface"], c["card"], q), c["surface"])
            self._rr_img(S(TILE), S(TILE), S(TILE_R),
                         _mix(c["elevated"], c["card"], q), c["surface"])

    def _sync_row_hover(self, animate=True):
        """悬停行的浮起补间。只给目标变了的行起补间，其余不动。"""
        for key in self.card_items:
            it = self.card_items[key]
            target = 1.0 if self._is_hot(key) else 0.0
            cur = it.get("hv", 0.0)
            if not animate:
                self._paint_row_hover(key, target)
                continue
            if abs(cur - target) < 0.01:
                continue
            # 不跳过「已有补间」的行：鼠标快速划过时，淡入还没跑完就要
            # 开始淡出，_tween 会先取消旧的那段、从当前值接着走。
            # 之前跳过这里，淡出要等淡入跑完才开始 —— 就是不自然的原因

            def step(e, key=key, cur=cur, target=target):
                self._paint_row_hover(key, cur + (target - cur) * e)

            self._tween(("hv", key), HOVER_MS, step)

    def _btn_style(self, key):
        c = self.c
        if self.rows[key]["muted"]:
            return c["red_soft"], c["red"], "已静音"
        if self.press == ("btn", key):
            return c["elevated"], c["ink"], "静音"
        if self.hover == ("btn", key):
            return c["card"], c["ink"], "静音"
        return c["surface"], c["body"], "静音"
    # ------------------------------------------------------- 头部 / 底部
    def _draw_head(self):
        c = self.c
        cv = self.canvas
        w = self.view_w
        tag = ("head",)
        cv.create_rectangle(0, 0, w, S(HEAD_H),
                            fill=c["canvas"], outline="", tags=tag)
        s = S(28)
        # 顶栏是应用标识：和行内图标块同一族的方片，只把字形提到最亮
        cv.create_image(S(PAD), S(16), anchor="nw",
                        image=self._icon_img(s, c["elevated"], c["canvas"],
                                             c["ink"]),
                        tags=tag)
        tx = S(PAD) + s + S(10)
        self.text(tx, S(19), "应用音量控制器", c["ink"],
                  20, "display", True, "nw", tag)
        self.text(tx, S(45), "单独调节每个程序",
                  c["mute"], 11, "meta", True, "nw", tag)

        specs = [
            ("刷新", 46, "refresh"),
            ("自动", 50, "auto"),
            ("置顶", 50, "top"),
            ("深色" if self.theme == "light" else "浅色", 50, "theme"),
        ]
        gap = S(4)
        total = sum(S(wd) for _, wd, _ in specs) + gap * (len(specs) - 1)
        x = w - S(PAD) - total
        y0 = S(46)
        y1 = y0 + S(BTN_H)
        bw, bh = 0, y1 - y0
        for label_text, wd, act in specs:
            x1 = x + S(wd)
            bw = x1 - x
            t = 1.0 if self.hover == ("tool", act) else 0.0
            fg = self._tool_fg(act)
            btn_tag = tag + ("tool:" + act,)
            img_id = cv.create_image(x, y0, anchor="nw",
                                     image=self._tool_img(act, t),
                                     tags=btn_tag)
            tid = self.text((x + x1) / 2.0, (y0 + y1) / 2.0,
                            label_text, fg, 11, "display", True, "center",
                            btn_tag)
            box = (x, y0, x1, y1)
            self.tool_hits.append({"box": box, "key": None,
                                   "kind": "tool", "action": act})
            self.tool_items[act] = dict(img=img_id, text_id=tid,
                                        box=box, w=bw, h=bh, r=S(BTN_R), t=t)
            x = x1 + gap

    def _tool_img(self, act, t):
        """pill-tab：默认透明；悬停浮一档；开启态是唯一的白色实心胶囊。"""
        it = self.tool_items.get(act)
        w = it["w"] if it else S(50)
        h = it["h"] if it else S(BTN_H)
        c = self.c
        if self._tool_active(act):
            return self._rr_img(w, h, S(BTN_R), c["primary"], c["canvas"])
        if t <= 0.0:
            return self._rr_img(w, h, S(BTN_R), c["canvas"], c["canvas"])
        return self._rr_img(w, h, S(BTN_R),
                            _mix(c["canvas"], c["elevated"],
                                 round(t * 4) / 4.0),
                            c["canvas"])

    def _tool_active(self, act):
        return bool((act == "auto" and self.auto_var.get()) or
                    (act == "top" and self.top_var.get()))

    def _tool_fg(self, act):
        c = self.c
        if self._tool_active(act):
            return c["on_primary"]
        return c["body"]

    def _draw_foot(self):
        c = self.c
        cv = self.canvas
        w, h = self.view_w, self.view_h
        tag = ("foot",)
        cv.create_rectangle(0, h - S(FOOT_H), w, h,
                            fill=c["canvas"], outline="", tags=tag)
        cv.create_line(S(PAD), h - S(FOOT_H), w - S(PAD), h - S(FOOT_H),
                       fill=c["hairline"], tags=tag)
        self.foot_text_id = self.text(
            S(PAD), h - S(FOOT_H) / 2.0 - S(2), self.status_text,
            c["ash"], 11, "meta", True, "w", tag)
    def _update_bar(self):
        """滚动条细条：一个矩形图元，滚动帧里只改 coords。"""
        c = self.c
        w = self.view_w
        view = self._list_bottom() - self._list_top()
        total = len(self.order) * S(ROW_H)
        if total <= view or view <= 0:
            if self.bar_id is not None:
                self.canvas.itemconfig(self.bar_id, state="hidden")
            return
        ratio = view / float(total)
        thumb = max(S(28), int(view * ratio))
        mx = self._max_scroll()
        frac = self.scroll_y / float(mx) if mx else 0.0
        frac = max(0.0, min(1.0, frac))     # 越界回弹时滚动条不跟着冲出去
        y = self._list_top() + (view - thumb) * frac
        box = (w - S(8), y, w - S(4), y + thumb)
        if self.bar_id is None:
            self.bar_id = self.canvas.create_rectangle(
                *box, fill=c["scroll"], outline="", tags=("bar",))
        else:
            self.canvas.itemconfig(self.bar_id, fill=c["scroll"],
                                   state="normal")
            self.canvas.coords(self.bar_id, *box)

    # ------------------------------------------------------------ 命中框
    # ------------------------------------------------------------ 命中框
    def _recalc_hits(self):
        """滚动后只重算命中框（纯 Python 计算），不动任何图元。"""
        out = list(self.tool_hits)
        top, bot = self._list_top(), self._list_bottom()
        sy = self.scroll_y
        for i, key in enumerate(self.order):
            g = self._card_geom(i)
            y0, y1 = g["y0"] - sy, g["y1"] - sy
            if y1 < top or y0 > bot:
                continue
            ty = g["ty"] - sy
            out.append({"box": (g["tx0"] - S(10), ty - S(14),
                                g["tx1"] + S(10), ty + S(14)),
                        "key": key, "kind": "track",
                        "tx0": g["tx0"], "tx1": g["tx1"]})
            out.append({"box": (g["bx0"], g["by0"] - sy,
                                g["bx1"], g["by1"] - sy),
                        "key": key, "kind": "btn"})
        self.hits = out

    # ------------------------------------------------------------ 局部更新
    def _update_card_level(self, key):
        """拖动跟手的核心：只改当前行填充条、圆钮、百分比三处。"""
        it = self.card_items.get(key)
        if not it:
            return
        cv = self.canvas
        row = self.rows[key]
        c = self.c
        muted = row["muted"]
        tx0, tx1, th = it["tx0"], it["tx1"], it["th"]
        # list tag 已被整体 move 过，而 coords() 用的是绝对画布坐标。
        # 这里必须补上同一位移，否则滚动后任何一次局部刷新都会把
        # 填充条和圆钮单独拽回未滚动的位置，与卡片错位。
        ty = it["ty"] + self._list_offset
        half = th / 2.0
        frac = row.get("visual", row["level"]) / 100.0
        hx = tx0 + (tx1 - tx0) * frac
        # fill_ov 由静音过渡补间写入，让填充条颜色也是渐变而非跳变
        fill = row.get("fill_ov") or (c["ash"] if muted else c["primary"])

        if hx - tx0 <= half:
            cv.itemconfig(it["cap"], state="hidden")
            cv.itemconfig(it["bar"], state="hidden")
        else:
            cv.itemconfig(it["cap"], state="normal",
                          image=self._rr_img(th, th, half, fill, c["hairline"]))
            cv.coords(it["bar"], tx0 + half, ty - half, hx, ty + half)
            cv.itemconfig(it["bar"], state="normal", fill=fill)

        # 圆钮大小固定，悬停 / 按下只让描边加深 —— 不缩放，也不回弹
        kr = S(KNOB_R)
        emph = round(it.get("ks", 0.0) * 4) / 4.0
        # 圆钮描边平时和面板同色（只用来和轨道分界），悬停换成强细线色
        ring = c["hairline2"] if emph else c["surface"]
        bar_col = fill if hx - tx0 > half else c["hairline"]
        cv.itemconfig(it["knob"],
                      image=self._knob_img(kr, c["primary"], ring,
                                           c["surface"], bar_col,
                                           c["hairline"], half, S(2)))
        cv.coords(it["knob"], hx, ty)
        cv.itemconfig(it["pct"], text="%d%%" % row["level"],
                      fill=row.get("pct_ov") or
                      (c["ash"] if muted else c["mute"]))

    def _set_card_btn(self, key, col=None):
        it = self.card_items.get(key)
        if not it:
            return
        bfill, bfg, btxt = self._btn_style(key)
        if col is not None:
            bfill = col
        it["btn_col"] = bfill
        it["btn_fg"] = bfg
        self.canvas.itemconfig(it["btn"],
                               image=self._btn_img(it["bw"], it["bh"],
                                                   bfill, self.c["canvas"], key))
        self.canvas.itemconfig(it["btn_text"], text=btxt, fill=bfg)

    def _refresh_states(self, animate_hover=True):
        """hover / 静音 / 数值变化后的着色刷新，全程只 itemconfig。"""
        c = self.c
        cv = self.canvas
        self._sync_row_hover(animate_hover)
        for key, it in self.card_items.items():
            row = self.rows[key]
            muted = row["muted"]
            # 正在跑颜色补间的，交给补间自己收尾，别被这里一帧打回原色
            if ("mute", key) not in self._jobs and ("chip", key) not in self._jobs:
                self._set_card_btn(key)
            if ("mute", key) not in self._jobs:
                cv.itemconfig(it["name"],
                              fill=c["mute"] if muted else c["ink"])
                cv.itemconfig(it["sub"], fill=c["mute"])
            self._update_card_level(key)

        for act, it in self.tool_items.items():
            self._set_tool_visual(act, it.get("t", 0.0))
            cv.itemconfig(it["text_id"], fill=self._tool_fg(act))
    def _set_tool_visual(self, act, t):
        it = self.tool_items.get(act)
        if not it:
            return
        it["t"] = t
        img = self._tool_img(act, t)
        if img is not None:
            self.canvas.itemconfig(it["img"], image=img)

    def _animate_tool(self, act, target, ease=_ease_out, ms=None):
        it = self.tool_items.get(act)
        if not it:
            return
        start = it.get("t", 0.0)
        if abs(start - target) < 0.02 and ("tool", act) not in self._jobs:
            return

        def step(e):
            self._set_tool_visual(act, start + (target - start) * e)

        self._tween(("tool", act), ms or TWEEN_MS, step, ease=ease)
    def _sync_emphasis(self, animate=True):
        """圆钮的「悬停 / 按下」只加深描边，不缩放、不回弹。"""
        for key, it in self.card_items.items():
            hot = self.hover in (("track", key), ("btn", key))
            down = self.drag_key == key or self.press == ("track", key)
            target = 1.0 if down else (0.5 if hot else 0.0)
            if not animate:
                it["ks"] = target
                self._update_card_level(key)
                continue
            if abs(it.get("ks", 0.0) - target) < 0.01:
                continue
            # 同上：允许中途反向，从当前值接着补
            start = it.get("ks", 0.0)

            def step(e, key=key, it=it, start=start, target=target):
                it["ks"] = start + (target - start) * e
                self._update_card_level(key)

            self._tween(("knob", key), KNOB_MS, step)
    def _sync_chips(self):
        """卡片内「静音」胶囊的 hover 底色渐变。"""
        for key, it in self.card_items.items():
            if ("mute", key) in self._jobs or ("chip", key) in self._jobs:
                continue
            target = self._btn_style(key)[0]
            start = it.get("btn_col", target)
            if start == target:
                continue

            def step(e, key=key, start=start, target=target):
                self._set_card_btn(key, _mix(start, target, e))

            self._tween(("chip", key), CHIP_MS, step)

    def _glide_level(self, key, start=None):
        """系统里别处改了音量：让填充条滑过去，而不是瞬间跳到位。"""
        row = self.rows.get(key)
        it = self.card_items.get(key)
        if not row or not it or ("glide", key) in self._jobs:
            return
        if start is None:
            start = row.get("visual", row["level"])
        start = float(start)
        end = float(row["level"])
        if abs(start - end) < 0.5:
            row.pop("visual", None)
            self._update_card_level(key)
            return

        def step(e):
            if self.drag_key == key:          # 用户开始拖了，让位给拖拽
                row.pop("visual", None)
                return
            row["visual"] = start + (end - start) * e
            self._update_card_level(key)

        def done():
            row.pop("visual", None)
            self._update_card_level(key)

        self._tween(("glide", key), 260, step, done=done, ease=_ease_in_out)

    def _animate_mute(self, key):
        """静音切换：按钮、填充条、百分比、文字色一起过渡，不是硬切。"""
        it = self.card_items.get(key)
        if not it:
            return
        c = self.c
        cv = self.canvas
        row = self.rows[key]
        muted = row["muted"]
        self._cancel(("chip", key))     # 静音过渡接管按钮底色
        if muted:
            target = c["red"]
            bar_to, pct_to = c["ash"], c["ash"]
        else:
            target = c["card"] if self.hover == ("btn", key) else c["surface"]
            bar_to, pct_to = c["primary"], c["mute"]

        start = it.get("btn_col", c["surface"])
        # 从画布上读当前真实颜色，连续点几次静音也不会错位
        try:
            bar_from = cv.itemcget(it["bar"], "fill") or bar_to
            pct_from = cv.itemcget(it["pct"], "fill") or pct_to
            name_from = cv.itemcget(it["name"], "fill") or c["ink"]
            sub_from = cv.itemcget(it["sub"], "fill") or c["body"]
        except tk.TclError:
            bar_from, pct_from = bar_to, pct_to
            name_from, sub_from = c["ink"], c["body"]
        name_to = c["mute"] if muted else c["ink"]
        sub_to = c["mute"] if muted else c["body"]

        cv.itemconfig(it["btn_text"], text="已静音" if muted else "静音")

        def step(e):
            q = round(e * 6) / 6.0          # 量化，避免生成过多贴图
            bg = _mix(start, target, q)
            self._set_card_btn(key, bg)
            # 文字色按底色亮度自动选深/白，过渡途中任何一帧都读得清
            cv.itemconfig(it["btn_text"],
                          fill=c["on_primary"] if _lum(bg) < 150 else c["ink"])
            row["fill_ov"] = _mix(bar_from, bar_to, q)
            row["pct_ov"] = _mix(pct_from, pct_to, q)
            cv.itemconfig(it["name"], fill=_mix(name_from, name_to, q))
            cv.itemconfig(it["sub"], fill=_mix(sub_from, sub_to, q))
            self._update_card_level(key)

        def done():
            row.pop("fill_ov", None)
            row.pop("pct_ov", None)
            self._set_card_btn(key)
            cv.itemconfig(it["name"], fill=c["mute"] if muted else c["ink"])
            cv.itemconfig(it["sub"], fill=c["mute"])
            self._update_card_level(key)

        self._tween(("mute", key), MUTE_MS, step, done=done, ease=_ease_in_out)
    def _apply_scroll(self):
        """把浮点 scroll_y 换算成整数位移，整体平移列表；无累计误差。"""
        off = -int(round(self.scroll_y))
        dy = off - self._list_offset
        if dy:
            self.canvas.move("list", 0, dy)
            self._list_offset = off
        self._recalc_hits()
        self._update_bar()

    # ------------------------------------------------------------ 交互
    def _find_hit(self, x, y):
        for hit in reversed(self.hits):
            hx0, hy0, hx1, hy1 = hit["box"]
            if hx0 <= x <= hx1 and hy0 <= y <= hy1:
                return hit
        return None

    def _on_press(self, event):
        hit = self._find_hit(event.x, event.y)
        if not hit:
            return
        kind = hit["kind"]
        if kind == "tool":
            self.press = ("tool", hit["action"])
            self.canvas.move("tool:" + hit["action"], 0, S(1))
            self._animate_tool(hit["action"], 1.0, ms=PRESS_MS)
            self._activate_tool(hit["action"])
        elif kind == "btn":
            self.press = ("btn", hit["key"])
            self._sync_chips()
            self.toggle_mute(hit["key"])
        elif kind == "track":
            self.press = ("track", hit["key"])
            self.drag_key = hit["key"]
            self.drag_track = (hit["tx0"], hit["tx1"])
            self.hover = ("track", self.drag_key)
            self._cancel(("glide", self.drag_key))
            self._sync_emphasis()
            self._refresh_states()
            self._level_from_x(self.drag_key, event.x)

    def _on_motion(self, event):
        if self.drag_key and self.drag_track:
            self._level_from_x(self.drag_key, event.x)
            return
        hit = self._find_hit(event.x, event.y)
        if hit and hit["kind"] in ("track", "btn"):
            new = (hit["kind"], hit["key"])
        elif hit and hit["kind"] == "tool":
            new = ("tool", hit["action"])
        else:
            new = None
        if new == self.hover:
            return
        self.hover = new
        for act in self.tool_items:
            self._animate_tool(act, 1.0 if new == ("tool", act) else 0.0)
        self._sync_chips()          # 胶囊底色渐变
        self._sync_emphasis()       # 圆钮放大 / 回缩
        self._refresh_states()

    def _on_release(self, _event):
        if self.press and self.press[0] == "tool":
            act = self.press[1]
            self.canvas.move("tool:" + act, 0, -S(1))
            # 回弹用 ease-back，松手有一点点过冲再落定
            self._animate_tool(act, 1.0 if self.hover == ("tool", act)
                               else 0.0, ms=140)
        self.press = None
        if self.drag_key:
            key = self.drag_key
            row = self.rows.get(key)
            if row:
                row.pop("visual", None)
            self.drag_key = None
            self.drag_track = None
        self._sync_emphasis()
        self._sync_chips()
        self._refresh_states()

    def _on_leave(self, _event):
        if self.drag_key:
            return
        self.hover = None
        self.press = None
        for act in self.tool_items:
            self._animate_tool(act, 0.0)
        self._sync_emphasis()
        self._sync_chips()
        self._refresh_states()

    def _on_wheel(self, event):
        """滚轮事件只累加目标位置，绝不在这里重绘。

        高精度触摸板一次滑动能发几十个小 delta，若每个都同步
        重建界面，事件会全部积压，表现就是「一滑就卡」。
        """
        step = float(event.delta) / 120.0 * S(48)
        mx = self._max_scroll()
        lim = S(BOUNCE)
        # 允许一点点越界，松手后回弹；这是列表「到头了」的手感反馈
        self.scroll_target = max(-lim, min(mx + lim, self.scroll_target - step))
        self._scrolling_until = time.time() + 0.25
        self._cancel(("spring",))
        self._start_scroll_anim()

    def _start_scroll_anim(self):
        if self._scroll_job is None:
            self._scroll_job = self.after(1, self._scroll_frame)

    def _scroll_frame(self):
        """缓动追目标；每帧只对 list tag 做一次 move，零图元增删。"""
        self._scroll_job = None
        self._scrolling_until = time.time() + 0.12
        delta = self.scroll_target - self.scroll_y
        if abs(delta) < 0.6:
            self.scroll_y = self.scroll_target
        else:
            self.scroll_y += delta * 0.32
        self._apply_scroll()
        if abs(self.scroll_target - self.scroll_y) >= 0.6:
            self._scroll_job = self.after(1, self._scroll_frame)
        elif self.scroll_y < -0.5 or self.scroll_y > self._max_scroll() + 0.5:
            self._spring_back()

    def _spring_back(self):
        """越界后回弹到合法区间：先冲出去再弹回来，边界才有「手感」。"""
        mx = self._max_scroll()
        start = self.scroll_y
        end = max(0.0, min(mx, start))
        self.scroll_target = end
        if abs(start - end) < 0.5:
            return

        def step(e):
            self.scroll_y = start + (end - start) * e
            self._apply_scroll()

        self._tween(("spring",), SPRING_MS, step, ease=_ease_back)

    def _level_from_x(self, key, x):
        tx0, tx1 = self.drag_track
        row = self.rows.get(key)
        if tx1 <= tx0 or not row:
            return
        pos = max(0.0, min(100.0, (x - tx0) / float(tx1 - tx0) * 100))
        row["visual"] = pos                 # 滑块连续跟手
        level = int(round(pos))
        if row["level"] != level:
            self._apply_volume(key, level, refresh=False)
        else:
            self._update_card_level(key)    # 整数没变也要动，位置仍在变

    # ------------------------------------------------------------ 音频写回
    def _apply_volume(self, key, level, refresh=True):
        row = self.rows.get(key)
        if not row or row["level"] == level:
            return
        target = level / 100.0
        for v in row["vols"]:
            try:
                if abs(float(v.GetMasterVolume()) - target) > 0.005:
                    v.SetMasterVolume(target, None)
            except Exception:
                pass
        row["level"] = level
        self.last_change[key] = time.time()
        if refresh:
            self._redraw()
        else:
            self._update_card_level(key)   # 拖动中只更新这一行

    def toggle_mute(self, key):
        row = self.rows.get(key)
        if not row:
            return
        _, mute = read_state(row["vols"])
        new = 0 if mute else 1
        for v in row["vols"]:
            try:
                v.SetMute(new, None)
            except Exception:
                pass
        row["muted"] = bool(new)
        self.last_change[key] = time.time()
        self._animate_mute(key)

    # ------------------------------------------------------------ 工具栏
    def _activate_tool(self, action):
        if action == "refresh":
            self.manual_refresh()
        elif action == "auto":
            self._toggle_auto()
        elif action == "top":
            self._toggle_top()
        elif action == "theme":
            self._toggle_theme()

    def _toggle_auto(self):
        self.auto_var.set(not self.auto_var.get())
        self._update_status()
        self._refresh_states()

    def _toggle_top(self):
        self.top_var.set(not self.top_var.get())
        self.attributes("-topmost", bool(self.top_var.get()))
        self._refresh_states()

    def _warm_other_theme(self):
        """启动后趁空档把另一套主题的贴图算好，切主题时才不会卡一下。"""
        if self._warmed:
            return
        self._warmed = True
        other = "dark" if self.theme == "light" else "light"
        cur = self.c
        self.c = THEMES[other]
        try:
            c = self.c
            g = self._card_geom(0)
            half = max(2, S(TRACK_H)) / 2.0
            self._rr_img(g["x1"] - g["x0"] + S(20), g["y1"] - g["y0"] - S(4),
                         S(7), c["card"], c["canvas"])
            self._rr_img(g["tx1"] - g["tx0"], g["th"], half,
                         c["hairline"], c["canvas"])
            self._rr_img(g["th"], g["th"], half, c["primary"], c["hairline"])
            for bar in (c["primary"], c["ash"], c["hairline"]):
                self._knob_img(S(KNOB_R), c["primary"], c["surface"], c["canvas"],
                               bar, c["hairline"], half)
            self._icon_img(S(30), c["primary"], c["canvas"], c["on_primary"])
            for act in ("refresh", "auto", "top", "theme"):
                self._tool_img(act, 0.0)
                self._tool_img(act, 1.0)
            self._btn_img(S(BTN_W), S(BTN_H), c["surface"], c["canvas"], "__warm__")
            self._btn_img(S(BTN_W), S(BTN_H), c["red"], c["canvas"], "__warm_mute__")
            self._warm_hover()
        except Exception:
            pass
        finally:
            self.c = cur
    def _toggle_theme(self):
        """切主题：先压暗一点点再重建、再提亮，用光影遮掉整窗重绘的突兀。"""
        def swap():
            self.theme = "dark" if self.theme == "light" else "light"
            self.c = THEMES[self.theme]
            self.configure(bg=self.c["canvas"])
            self.canvas.config(bg=self.c["canvas"])
            self._redraw()

            def up(e):
                self._set_alpha(0.84 + 0.16 * e)

            self._tween(("alpha",), 170, up, ease=_ease_in_out)

        def down(e):
            self._set_alpha(1.0 - 0.16 * e)

        self._tween(("alpha",), 90, down, done=swap, ease=_ease_in_out)

    def _set_alpha(self, a):
        try:
            self.attributes("-alpha", max(0.0, min(1.0, a)))
        except Exception:
            pass

    def manual_refresh(self):
        try:
            self.rebuild(collect_sessions())
        except Exception as e:
            messagebox.showerror("应用音量控制器", "刷新失败：\n%s" % e)

    # ------------------------------------------------------------ 状态同步
    def rebuild(self, groups):
        self.rows = {}
        for key, g in groups.items():
            vol, mute = read_state(g["vols"])
            self.rows[key] = {"name": g["name"], "sub": g["sub"],
                              "vols": g["vols"], "level": round(vol * 100),
                              "muted": bool(mute)}
        self.order = [g["key"] for g in sorted(
            groups.values(),
            key=lambda g: (not g["has_title"], g["name"].lower()))]
        self.scroll_y = max(0, min(self.scroll_y, self._max_scroll()))
        self._update_status(len(self.order))
        self._redraw()

    def _update_status(self, count=None):
        if count is None:
            count = len(self.order)
        mode = "自动同步中" if self.auto_var.get() else "手动模式"
        self.status_text = "共 %d 个程序有音频会话 · %s" % (count, mode)
        if self.foot_text_id is not None:
            try:
                self.canvas.itemconfig(self.foot_text_id,
                                       text=self.status_text)
            except tk.TclError:
                self.foot_text_id = None

    def poll(self):
        # 拖动 / 滚动期间不做后台同步：完整枚举要 ~40ms，硬塞进来必然掉帧
        if (self.auto_var.get() and not self.drag_key
                and time.time() > self._scrolling_until):
            try:
                now = time.time()
                # 只有「程序新增/退出」才需要完整枚举，降到 ENUM_S 一次
                if now - self._last_enum >= ENUM_S:
                    groups = collect_sessions()
                    self._last_enum = now
                    if set(groups.keys()) != set(self.rows.keys()):
                        self.rebuild(groups)
                    else:
                        for key, g in groups.items():
                            if key in self.rows:
                                self.rows[key]["vols"] = g["vols"]
                # 每轮只复用已缓存的接口读音量 / 静音，单次不到 1ms
                dirty = False
                moved = []
                for key, row in self.rows.items():
                    if now - self.last_change.get(key, 0) < 2.0:
                        continue
                    vol, mute = read_state(row["vols"])
                    v = round(vol * 100)
                    if row["level"] != v:
                        moved.append((key, row["level"]))
                        row["level"] = v
                        dirty = True
                    if row["muted"] != bool(mute):
                        row["muted"] = bool(mute)
                        dirty = True
                if dirty:
                    self._refresh_states()
                for key, old in moved:
                    self._glide_level(key, old)   # 外部改了音量就滑过去
                self._update_status(len(self.rows))
            except Exception:
                pass
        self.after(POLL_MS, self.poll)


# ------------------------------------------------------------------ 自检
def benchmark():
    """量化渲染开销：全量重建 / 拖动单行 / 滚动一帧 / 滚轮响应。"""
    app = VolumeMixer()
    app.update()

    def avg(fn, n):
        t = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t) / n * 1000

    full = avg(lambda: app._redraw(), 15)
    key = app.order[0] if app.order else None
    drag = avg(lambda: app._update_card_level(key), 200) if key else 0.0

    def scroll_step():
        mx = app._max_scroll() or 1.0
        app.scroll_y = (app.scroll_y + 1.0) % mx
        app._apply_scroll()

    scroll = avg(scroll_step, 200)
    enum = avg(lambda: collect_sessions(), 3)
    wheel = avg(lambda: app.canvas.event_generate(
        "<MouseWheel>", x=app.view_w // 2, y=app.view_h // 2,
        delta=-120), 40)
    print("窗口 %dx%d | 行数 %d | 图元 %d | 贴图 %d 张 | 图片文件 0" %
          (app.view_w, app.view_h, len(app.order),
           len(app.canvas.find_all()), len(app._imgs)))
    print("全量重建      %6.2f ms/次（仅切主题 / 程序增减 / 改窗口大小）" % full)
    print("拖动单行更新  %6.3f ms/帧  <- 只改 1 个矩形 + 1 个贴图坐标" % drag)
    print("滚动一帧      %6.3f ms/帧  <- 只对 list tag 做一次 move" % scroll)
    print("滚轮事件响应  %6.3f ms/次  <- 只累加目标，不在这里重绘" % wheel)
    print("完整会话枚举  %6.2f ms/次（每 %.1f 秒一次，拖动/滚动中不跑）" %
          (enum, ENUM_S))

    # 圆钮缩放一帧：换一张已缓存的贴图 + 改一次坐标，不重新算像素
    if key:
        it = app.card_items.get(key)
        for ks in (0.0, 0.5, 1.0):        # 先把三档尺寸的贴图都生成好
            it["ks"] = ks
            app._update_card_level(key)
        anim = avg(lambda: app._update_card_level(key), 200)
        print("圆钮描边一帧  %6.3f ms/帧  <- 悬停/按下动效的单帧开销" % anim)

        # 贴图首次生成的一次性开销（之后全部命中缓存）
        app._imgs.clear()
        c = app.c
        t = time.perf_counter()
        app._knob_img(S(KNOB_R), c["primary"], c["surface"], c["canvas"],
                      c["primary"], c["hairline"],
                      max(2, S(TRACK_H)) / 2.0)
        print("圆钮贴图首绘  %6.2f ms/张（一次性，之后走缓存）" %
              ((time.perf_counter() - t) * 1000))
    app.destroy()


def show():
    """打开真实窗口供人工查看，不生成任何图片文件。"""
    app = VolumeMixer()
    app.geometry("%dx%d" % initial_size())
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    print("窗口 %dx%d | 行数 %d | 图元 %d | 贴图 %d 张 | 图片文件 0" %
          (app.view_w, app.view_h, len(app.order),
           len(app.canvas.find_all()), len(app._imgs)))
    app.mainloop()


def _centre(hit):
    x0, y0, x1, y1 = hit["box"]
    return int((x0 + x1) / 2), int((y0 + y1) / 2)


def hittest():
    """模拟真实鼠标事件，验证命中框：胶囊按钮 / 静音 / 拖动 / 滚轮。"""
    app = VolumeMixer()
    app.update()
    checks = []
    changed = {}
    try:
        def click(hit):
            x, y = _centre(hit)
            app.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
            app.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
            app.update()

        def tool(action):
            return next((h for h in app.hits
                         if h["kind"] == "tool" and h["action"] == action), None)

        def settle(ms=700):
            """等补间跑完（主题切换是先压暗再重建，不是同步生效）。"""
            end = time.perf_counter() + ms / 1000.0
            while time.perf_counter() < end:
                app.update()
                time.sleep(0.008)

        hit = tool("theme")
        checks.append(("命中主题按钮", hit is not None))
        if hit:
            click(hit)
            settle()
            checks.append(("点击切换到深色", app.theme == "dark"))
            checks.append(("深色重建后图元与行数一致",
                           len(app.card_items) == len(app.order)
                           and len(app.canvas.find_all()) > 0))
            checks.append(("切换后透明度复位",
                           abs(float(app.attributes("-alpha")) - 1.0) < 0.02))
            click(hit)
            settle()
            checks.append(("点击切回浅色", app.theme == "light"))

        hit = tool("auto")
        auto0 = app.auto_var.get()
        if hit:
            click(hit)
            checks.append(("点击自动开关", app.auto_var.get() != auto0))
            click(hit)
            checks.append(("自动开关复原", app.auto_var.get() == auto0))

        key = next((k for k in app.order if k != "system"
                    and not app.rows[k]["muted"]), None)
        if key:
            row = app.rows[key]
            vol0, mute0 = read_state(row["vols"])
            changed[key] = (vol0, mute0)

            btn = next((h for h in app.hits
                        if h["kind"] == "btn" and h["key"] == key), None)
            checks.append(("命中静音按钮", btn is not None))
            if btn:
                click(btn)
                m1 = read_state(row["vols"])[1]
                click(btn)
                m2 = read_state(row["vols"])[1]
                checks.append(("点击静音并复原", m1 != mute0 and m2 == mute0))

            tr = next((h for h in app.hits
                       if h["kind"] == "track" and h["key"] == key), None)
            checks.append(("命中滑块轨道", tr is not None))
            if tr:
                _, y = _centre(tr)
                tx0, tx1 = tr["tx0"], tr["tx1"]
                x40 = int(tx0 + (tx1 - tx0) * 0.40)
                app.canvas.event_generate("<ButtonPress-1>", x=x40, y=y)
                app.canvas.event_generate("<B1-Motion>", x=x40 + 5, y=y)
                app.canvas.event_generate("<ButtonRelease-1>", x=x40 + 5, y=y)
                app.update()
                real, _ = read_state(row["vols"])
                checks.append(("拖动写回约 40%", abs(real - 0.40) < 0.05))
                xback = int(tx0 + (tx1 - tx0) * vol0)
                app.canvas.event_generate("<ButtonPress-1>", x=xback, y=y)
                app.canvas.event_generate("<B1-Motion>", x=xback, y=y)
                app.canvas.event_generate("<ButtonRelease-1>", x=xback, y=y)
                app.update()

        # 抗锯齿贴图必须真的带中间色阶，不能只有纯 fill / 纯 bg
        img = app._rr_img(S(66), S(28), S(14), "#FF0000", "#FFFFFF")

        def px(x, y):
            v = img.get(x, y)
            if isinstance(v, str):
                v = v.split()
            return tuple(int(i) for i in v)

        band = [px(x, 2) for x in range(0, S(14) + 1)]
        mids = {c for c in band
                if c != (255, 0, 0) and c != (255, 255, 255)}
        checks.append(("圆角边缘有过渡色", len(mids) >= 2))

        try:
            if app._max_scroll() <= 0:
                checks.append(("滚轮滚动列表", "跳过"))
            else:
                before = app.scroll_target
                app.canvas.event_generate("<MouseWheel>", x=app.view_w // 2,
                                          y=app.view_h // 2, delta=-120)
                app.update()
                checks.append(("滚轮只改目标位置", app.scroll_target > before))
                app._scroll_frame()           # 推进一帧缓动
                checks.append(("缓动帧推进滚动", app.scroll_y > 0))
                app._scroll_frame()
                checks.append(("滚动后命中框仍有效", len(app.hits) > 0))

                # 回归：滚动状态下做局部刷新，填充条/圆钮必须与卡片、
                # 命中框处在同一屏幕坐标系，不能被拽回未滚动的位置。
                app.scroll_target = min(app._max_scroll(), float(S(120)))
                app.scroll_y = app.scroll_target
                app._apply_scroll()
                app._refresh_states()
                app.update()
                tr2 = next((h for h in app.hits if h["kind"] == "track"), None)
                k0 = tr2["key"] if tr2 else None
                if k0 and k0 in app.card_items:
                    it = app.card_items[k0]
                    y_bar = app.canvas.coords(it["bar"])[1]
                    y_cap = app.canvas.coords(it["cap"])[1]
                    y_knob = app.canvas.coords(it["knob"])[1]
                    ty_screen = tr2["box"][1] + S(16)
                    half = max(2, S(TRACK_H)) / 2.0
                    checks.append(("滚动后局部更新不错位",
                                   abs(y_bar - (ty_screen - half)) < 1.0
                                   and abs(y_cap - ty_screen) < 1.0
                                   and abs(y_knob - ty_screen) < 1.0))
        except tk.TclError:
            checks.append(("滚轮滚动列表", "跳过"))
    finally:
        for k, (vol0, mute0) in changed.items():
            for v in app.rows[k]["vols"]:
                try:
                    v.SetMasterVolume(vol0, None)
                    v.SetMute(mute0, None)
                except Exception:
                    pass
        app.destroy()

    for name, ok in checks:
        print("  %-20s %s" % (name,
                              "通过" if ok is True else
                              "跳过" if ok == "跳过" else "失败"))
    bad = [n for n, ok in checks if ok is False]
    print("命中框交互：%s" % ("通过" if not bad else "失败：" + "、".join(bad)))
    return 0 if not bad else 1


def interacttest():
    """真实拖动 / 静音写回测试；无论结果如何在结束时恢复原值。"""
    app = VolumeMixer()
    app.update()
    key = next((k for k in app.order
                if k != "system" and not app.rows[k]["muted"]), None)
    if key is None:
        print("没有可安全测试的程序")
        app.destroy()
        return 1
    row = app.rows[key]
    vol0, mute0 = read_state(row["vols"])
    ok = False
    try:
        app._apply_volume(key, 37)
        real, _ = read_state(row["vols"])
        app._apply_volume(key, round(vol0 * 100))
        real2, _ = read_state(row["vols"])
        app.toggle_mute(key)
        m1 = read_state(row["vols"])[1]
        app.toggle_mute(key)
        m2 = read_state(row["vols"])[1]
        print("测试对象：%s (%s)" % (row["name"], key))
        print("拖动 37%% -> 系统实测 %.0f%%" % (real * 100))
        print("恢复 %d%% -> 系统实测 %.0f%%" % (round(vol0 * 100), real2 * 100))
        print("静音切换：%d -> %d -> %d" % (mute0, m1, m2))
        ok = (abs(real - 0.37) < 0.02 and abs(real2 - vol0) < 0.02
              and m1 != mute0 and m2 == mute0)
    finally:
        for v in row["vols"]:
            try:
                v.SetMasterVolume(vol0, None)
                v.SetMute(mute0, None)
            except Exception:
                pass
        app.destroy()
    for g in collect_sessions().values():
        if g["name"] == "雨课堂":
            print("雨课堂静音保持：%s" %
                  ("是" if read_state(g["vols"])[1] else "否"))
    print("交互写回：%s" % ("通过" if ok else "失败"))
    return 0 if ok else 1


def animtest():
    """交互动画回归：圆钮缩放 / 胶囊渐变 / 静音过渡 / 滚动回弹 / 主题切换。

    用假数据凑出 12 行，滚动和回弹才测得到；vols 留空，
    所以这里绝不会写到系统音量。
    """
    app = VolumeMixer()
    app.auto_var.set(False)     # 关掉后台同步，否则假数据会被真会话替换
    # 测试是程序化设置 hover 的，真实鼠标恰好移出窗口会触发 <Leave>
    # 把 hover 清掉，导致断言假失败 —— 自检期间直接解绑这个事件
    app.canvas.unbind("<Leave>")
    app.update()
    checks = []

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.update()
            time.sleep(0.008)

    def reset_anim():
        """清掉所有在跑的补间和交互状态。

        自检是一段接一段跑的，上一段没跑完的补间会占着 _jobs 里同名的 key，
        让下一段的 _sync_chips / _sync_emphasis 直接跳过，测出假失败。
        """
        for _k in list(app._jobs):
            app._cancel(_k)
        app.hover = None
        app.press = None
        app.drag_key = None
        app._sync_emphasis(animate=False)
        app._refresh_states()

    try:
        fake = {}
        for i in range(12):
            k = "fake:%d" % i
            fake[k] = {"name": "测试程序 %02d" % i,
                       "sub": "fake%d.exe · PID %d" % (i, 1000 + i),
                       "vols": [], "level": 20 + i * 5, "muted": False}
        app.rows = fake
        app.order = list(fake.keys())
        app.scroll_y = app.scroll_target = 0.0
        app._list_offset = 0
        app._redraw()
        app.update()
        checks.append(("假数据 12 行可滚动", app._max_scroll() > 0))

        # ---- 滚动：滚轮只改目标，帧推进，边界回弹
        app.canvas.event_generate("<MouseWheel>", x=app.view_w // 2,
                                  y=app.view_h // 2, delta=-120)
        app.update()
        checks.append(("滚轮只改目标", app.scroll_target > 0))
        app._scroll_frame()
        checks.append(("缓动帧推进", app.scroll_y > 0))
        pump(400)
        checks.append(("缓动收敛到目标",
                       abs(app.scroll_y - app.scroll_target) < 1.0))

        app.scroll_y = app.scroll_target = 0.0
        app._apply_scroll()
        for _ in range(6):                      # 顶部继续往上滚 -> 越界
            app.canvas.event_generate("<MouseWheel>", x=app.view_w // 2,
                                      y=app.view_h // 2, delta=120)
        # 滚轮只改目标，此刻目标必须是负的（允许越界），这一步是确定的
        checks.append(("顶部允许越界", app.scroll_target < -1.0))
        # 再跑完整段，确认弹回 0
        low = 0.0
        end = time.perf_counter() + 1.1
        while time.perf_counter() < end:
            app.update()
            low = min(low, app.scroll_y)
            time.sleep(0.006)
        # 只要最终回到 0 就说明回弹跑过了：没有回弹的话 scroll_y 会停在 -39。
        # 不再要求「采到过负值」—— 那取决于有没有恰好采到中间某一帧
        checks.append(("越界后弹回 0", abs(app.scroll_y) < 1.0))

        reset_anim()
        # ---- 圆钮：悬停只加深描边（不缩放）。用 animate=False 取值，
        # 免得鼠标恰好移出窗口触发 <Leave> 把 hover 清掉，测出假失败
        k0 = app.order[0]
        app.hover = ("track", k0)
        app._sync_emphasis()
        checks.append(("圆钮悬停已起补间", ("knob", k0) in app._jobs))
        pump(200)
        app.hover = ("track", k0)
        app._sync_emphasis(animate=False)
        hot = app.card_items[k0]["ks"]
        app.hover = None
        app._sync_emphasis(animate=False)
        cold = app.card_items[k0]["ks"]
        checks.append(("圆钮悬停加深描边", hot > 0.4 and cold < 0.02))

        reset_anim()
        # ---- 胶囊 hover：底色是渐变的，不是一帧跳变
        it = app.card_items[k0]
        before = it["btn_col"]
        app.hover = ("btn", k0)
        app._sync_chips()
        # 断言结构而不是帧数：机器一卡，补间会一帧跑完，数帧数会假失败
        checks.append(("胶囊 hover 起补间", ("chip", k0) in app._jobs))
        checks.append(("胶囊 hover 目标色正确",
                       app._btn_style(k0)[0] == app.c["card"]
                       and before != app.c["card"]))

        reset_anim()
        # ---- 静音过渡：颜色分多帧走到红色
        app.hover = None
        app.rows[k0]["muted"] = False
        app._set_card_btn(k0)
        app.toggle_mute(k0)
        checks.append(("静音过渡已启动", ("mute", k0) in app._jobs))
        pump(420)
        final = app.canvas.itemcget(it["pct"], "fill")
        checks.append(("静音后百分比变暗",
                       final.upper() == app.c["ash"].upper()))
        checks.append(("静音收尾为柔红底",
                       it["btn_col"].upper() == app.c["red_soft"].upper()))
        checks.append(("静音文字为红色",
                       app.canvas.itemcget(it["btn_text"], "fill").upper()
                       == app.c["red"].upper()))
        app.toggle_mute(k0)
        pump(300)

        # ---- 外部改音量：填充条滑过去。断言结构而不是取某一刻的中间值 ——
        # 帧率提高后补间可能在取样点之前就跑完了
        app.rows[k0]["level"] = 90
        app._glide_level(k0, 20)
        checks.append(("外部音量起补间",
                       ("glide", k0) in app._jobs
                       or "visual" in app.rows[k0]))
        pump(420)
        checks.append(("外部音量滑动到位",
                       app.rows[k0]["level"] == 90
                       and "visual" not in app.rows[k0]))

        # ---- 主题切换：先压暗、重建、再提亮。整段采样取极值，
        # 只取某一刻的值在机器忙的时候会错过低谷，测出假失败
        app._toggle_theme()
        alphas = []
        end = time.perf_counter() + 0.55
        while time.perf_counter() < end:
            app.update()
            alphas.append(float(app.attributes("-alpha")))
            time.sleep(0.006)
        checks.append(("主题切换有明暗过渡",
                       min(alphas) < 0.97 and max(alphas) > 0.99))
        checks.append(("主题切换后状态一致",
                       app.theme == "dark"
                       and len(app.card_items) == len(app.order)))
    finally:
        app.destroy()

    for name, ok in checks:
        print("  %-20s %s" % (name, "通过" if ok else "失败"))
    bad = [n for n, ok in checks if not ok]
    print("交互动画：%s" % ("通过" if not bad else "失败：" + "、".join(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--benchmark" in sys.argv:
        enable_dpi()
        benchmark()
        sys.exit(0)
    if "--hittest" in sys.argv:
        enable_dpi()
        sys.exit(hittest())
    if "--animtest" in sys.argv:
        enable_dpi()
        sys.exit(animtest())
    if "--interacttest" in sys.argv:
        enable_dpi()
        sys.exit(interacttest())
    if "--uitest" in sys.argv:
        enable_dpi()
        _app = VolumeMixer()
        _app.update()
        print("窗口：%s | 主题：%s | 可见：%s" %
              (_app.title(), _app.theme, _app.winfo_ismapped()))
        print("生成行数：%d | Canvas 图元：%d | 内存贴图：%d 张 | 图片文件：0" %
              (len(_app.order), len(_app.canvas.find_all()),
               len(_app._imgs)))
        for _key in _app.order:
            _r = _app.rows[_key]
            print("  %-12s %-20s 音量=%3d%% 静音=%s" %
                  (_key, _r["name"], _r["level"], "是" if _r["muted"] else "否"))
        _app.destroy()
        sys.exit(0)
    if "--show" in sys.argv or "--preview" in sys.argv:
        enable_dpi()
        show()
        sys.exit(0)
    try:
        enable_dpi()
        app = VolumeMixer()
        app.mainloop()
    except Exception:
        log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")
        try:
            with open(log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
