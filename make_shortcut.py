# -*- coding: utf-8 -*-
"""
在桌面创建「应用音量控制器」快捷方式。

直接按 MS-SHLLINK 规范写 .lnk 二进制，不实例化任何 COM 组件。
已存在同名快捷方式时不会覆盖。

运行：python make_shortcut.py
"""

import ctypes
import os
import shutil
import struct
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))


def find_pythonw():
    """找到 pythonw.exe：优先当前解释器同目录，其次 PATH。"""
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(cand):
        return cand
    found = shutil.which("pythonw")
    if found:
        return found
    raise SystemExit("找不到 pythonw.exe，请确认 Python 已安装并在 PATH 中。")


TARGET = find_pythonw()
SCRIPT = os.path.join(HERE, "app_volume.pyw")
ICON = os.path.join(HERE, "app_volume_icon.ico")
WORKDIR = HERE
NAME = "应用音量控制器.lnk"
DESCRIPTION = "单独调节每个正在运行的程序的音量"

CLSID = bytes.fromhex("0114020000000000c000000000000046")
HAS_LINK_TARGET_ID_LIST = 0x00000001
HAS_LINK_INFO = 0x00000002
HAS_NAME = 0x00000004
HAS_WORKING_DIR = 0x00000010
HAS_ARGUMENTS = 0x00000020
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080

FLAGS = (HAS_LINK_TARGET_ID_LIST | HAS_LINK_INFO | HAS_NAME |
         HAS_WORKING_DIR | HAS_ARGUMENTS | HAS_ICON_LOCATION | IS_UNICODE)


def desktop_dir():
    """CSIDL_DESKTOPDIRECTORY，兼容 OneDrive 重定向的桌面。"""
    buf = ctypes.create_unicode_buffer(260)
    fn = ctypes.windll.shell32.SHGetFolderPathW
    fn.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                   wintypes.DWORD, wintypes.LPWSTR]
    hr = fn(0, 0x10, 0, 0, buf)
    if hr == 0 and buf.value:
        return buf.value
    return os.path.join(os.path.expanduser("~"), "Desktop")


def string_data(text):
    """StringData：字符数(2B) + UTF-16LE 文本，不含结尾 null。"""
    return struct.pack("<H", len(text)) + text.encode("utf-16-le")


def target_id_list():
    """用 Shell API 生成目标的 PIDL，作为 LinkTargetIDList。

    仅取 ITEMIDLIST 字节，不实例化 COM 组件。ILGetSize 已包含末尾
    2 字节的终止 SHITEMID，正好对应 .lnk 里的 TerminalID。
    """
    sh = ctypes.windll.shell32
    sh.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    sh.ILCreateFromPathW.restype = ctypes.c_void_p
    sh.ILGetSize.argtypes = [ctypes.c_void_p]
    sh.ILGetSize.restype = ctypes.c_uint
    sh.ILFree.argtypes = [ctypes.c_void_p]
    pidl = sh.ILCreateFromPathW(TARGET)
    if not pidl:
        raise RuntimeError("ILCreateFromPathW 失败：%s" % TARGET)
    try:
        size = sh.ILGetSize(pidl)
        raw = ctypes.string_at(pidl, size)
    finally:
        sh.ILFree(pidl)
    if len(raw) < 4 or raw[-2:] != b"\x00\x00":
        raise RuntimeError("PIDL 格式异常")
    return struct.pack("<H", size) + raw


def build():
    # ShellLinkHeader (76B)
    header = struct.pack("<I", 0x4C) + CLSID + struct.pack(
        "<IIQQQIIIHHII",
        FLAGS,            # LinkFlags
        0x20,             # FileAttributes = ARCHIVE
        0, 0, 0,          # Creation / Access / Write time
        0,                # FileSize
        0,                # IconIndex
        1,                # ShowCommand = SW_SHOWNORMAL
        0, 0, 0, 0)       # HotKey / Reserved1 / Reserved2 / Reserved3

    # LinkInfo：VolumeID + LocalBasePath(ANSI) + CommonPathSuffix
    # LocalBasePath 走系统代码页而不是 ascii：用户名带中文时 ascii 会直接抛
    volume = struct.pack("<IIII", 17, 3, 0, 0x10) + b"\x00"
    local_base = TARGET.encode("mbcs") + b"\x00"
    suffix = b"\x00"
    info_header_size = 0x1C
    volume_off = info_header_size
    base_off = volume_off + len(volume)
    suffix_off = base_off + len(local_base)
    info_size = suffix_off + len(suffix)
    link_info = struct.pack(
        "<IIIIIII", info_size, info_header_size, 1,
        volume_off, base_off, 0, suffix_off) + volume + local_base + suffix

    # StringData 顺序固定：Name / RelativePath / WorkingDir / Args / IconLocation
    strings = (string_data(DESCRIPTION) +
               string_data(WORKDIR) +
               string_data('"' + SCRIPT + '"') +
               string_data(ICON))

    terminal = struct.pack("<I", 0)
    # 顺序：Header -> LinkTargetIDList -> LinkInfo -> StringData -> TerminalBlock
    return header + target_id_list() + link_info + strings + terminal


def verify(blob):
    assert struct.unpack_from("<I", blob, 0)[0] == 0x4C, "HeaderSize 错误"
    assert blob[4:20] == CLSID, "LinkCLSID 错误"
    flags = struct.unpack_from("<I", blob, 20)[0]
    assert flags == FLAGS, "LinkFlags 错误"

    off = 76
    id_size = struct.unpack_from("<H", blob, off)[0]
    assert id_size >= 2, "LinkTargetIDList 长度异常"
    off += 2 + id_size

    (info_size, info_hdr, info_flags, vol_off, base_off, _,
     suffix_off) = struct.unpack_from("<IIIIIII", blob, off)
    assert info_hdr == 0x1C and info_flags == 1
    target = blob[off + base_off:off + suffix_off].rstrip(b"\x00").decode("mbcs")

    off += info_size
    values = []
    for _ in range(4):
        n = struct.unpack_from("<H", blob, off)[0]
        values.append(blob[off + 2:off + 2 + n * 2].decode("utf-16-le"))
        off += 2 + n * 2
    assert struct.unpack_from("<I", blob, off)[0] == 0, "缺少 TerminalBlock"

    print("LinkFlags  : 0x%X" % flags)
    print("目标       : %s" % target)
    print("备注       : %s" % values[0])
    print("工作目录   : %s" % values[1])
    print("参数       : %s" % values[2])
    print("图标       : %s" % values[3])
    print("TerminalBlock: OK")


def main():
    for p in (TARGET, SCRIPT, ICON):
        if not os.path.exists(p):
            raise SystemExit("缺少文件：%s" % p)
    path = os.path.join(desktop_dir(), NAME)
    existed = os.path.exists(path)
    if existed and "--force" not in sys.argv:
        print("快捷方式已存在，未覆盖（加 --force 可重建）：%s" % path)
        return
    blob = build()
    verify(blob)
    with open(path, "wb") as f:
        f.write(blob)
    print("%s：%s (%d bytes)" %
          ("已重建" if existed else "已创建", path, len(blob)))


if __name__ == "__main__":
    main()
