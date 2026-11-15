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
           "--icon", os.path.join(HERE, "app_volume_icon.ico")]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(os.path.join(HERE, "app_volume.pyw"))
    run(cmd)

    exe = os.path.join(DIST, APP_NAME + ".exe")
    if not os.path.exists(exe):
        raise SystemExit("打包后没有找到 %s" % exe)
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
