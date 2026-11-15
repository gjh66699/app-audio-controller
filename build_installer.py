# -*- coding: utf-8 -*-
"""
一键构建 Windows 安装包。

    1. PyInstaller 把 app_volume.pyw 打成不依赖 Python 的 onedir 目录
    2. Inno Setup 把该目录压成一个 setup.exe（含桌面快捷方式、卸载项）

用法：
    python build_installer.py

依赖（只需装一次）：
    pip install pyinstaller
    winget install JRSoftware.InnoSetup

产物：
    installer/Output/AppVolumeController-Setup-<版本>.exe
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "AppVolumeController"
ISS = os.path.join(HERE, "installer", "app.iss")
DIST = os.path.join(HERE, "dist", APP_NAME)

# PyInstaller 会顺着 site-packages 把环境里装的大库一起收进来 ——
# 实测 numpy 一家就占了 27 MB（numpy.libs 里的 OpenBLAS 21 MB），
# 而本程序只用到 tkinter + pycaw，一行 numpy 都没有。
# 排掉这些之后 onedir 从 59 MB 降到 28 MB。
EXCLUDES = [
    "numpy", "scipy", "pandas", "matplotlib",
    "PIL", "pytest", "setuptools", "pip",
    "IPython", "jupyter", "notebook",
    # OpenSSL 全家桶 5.8 MB。这个工具不联网，用不到；
    # 代价是以后要加「检查更新」之类的网络功能得把它加回来。
    "ssl", "_ssl", "_hashlib",
]

# PyInstaller 会把整个 Tcl/Tk 运行时搬进来，其中有些是纯冗余：
#   tzdata  时区数据库 2.1 MB —— 程序不碰 clock 的时区功能
#   msgs    多语言消息 353 KB —— Tcl 侧的，Tk 自己的对话框消息另有一份
#   images  Tk 自带的演示图 125 KB —— logo100.gif / pwrdLogo.gif 之类，纯装饰
# 删掉后已实测：窗口正常创建、界面渲染完整。
TRIM = [
    os.path.join("_internal", "_tcl_data", "tzdata"),
    os.path.join("_internal", "_tcl_data", "msgs"),
    os.path.join("_internal", "_tk_data", "images"),
]


def find_iscc():
    """ISCC.exe 可能装在 Program Files，也可能装在用户目录（winget 默认）。"""
    cands = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    found = shutil.which("iscc")
    if found:
        return found
    raise SystemExit(
        "找不到 ISCC.exe。请先安装 Inno Setup：\n"
        "    winget install JRSoftware.InnoSetup")


def run(cmd, **kw):
    print(">>", " ".join(cmd) if isinstance(cmd, list) else cmd)
    r = subprocess.run(cmd, cwd=HERE, **kw)
    if r.returncode != 0:
        raise SystemExit("命令失败（exit=%d）" % r.returncode)


def main():
    # ---- 1) PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("缺少 PyInstaller，请先安装：\n    pip install pyinstaller")

    print("\n[1/2] PyInstaller 打包 ...")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--windowed", "--onedir", "--name", APP_NAME,
           "--icon", os.path.join(HERE, "app_volume_icon.ico"),
           # ico 也要随包分发：运行时要用它设窗口图标（标题栏 / 任务栏），
           # 只设 --icon 只影响 exe 文件本身的图标，不影响运行中的窗口
           "--add-data",
           os.path.join(HERE, "app_volume_icon.ico") + os.pathsep + "."]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(os.path.join(HERE, "app_volume.pyw"))
    run(cmd)

    exe = os.path.join(DIST, APP_NAME + ".exe")
    if not os.path.exists(exe):
        raise SystemExit("打包后没有找到 %s" % exe)

    for rel in TRIM:
        p = os.path.join(DIST, rel)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            print("    裁掉 %s" % rel)

    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _, fs in os.walk(DIST) for f in fs)
    print("    产物 %s（%.0f MB）" % (DIST, size / 1024 / 1024))

    # ---- 2) Inno Setup
    print("\n[2/2] Inno Setup 编译安装包 ...")
    run([find_iscc(), ISS])

    outs = sorted(glob.glob(os.path.join(HERE, "installer", "Output", "*.exe")))
    if not outs:
        raise SystemExit("没有生成安装包")
    latest = max(outs, key=os.path.getmtime)
    print("\n完成：%s（%.1f MB）"
          % (latest, os.path.getsize(latest) / 1024 / 1024))


if __name__ == "__main__":
    main()
