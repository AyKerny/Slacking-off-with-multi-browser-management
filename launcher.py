# -*- coding: utf-8 -*-
"""
mua — 多浏览器批量启动器
- 自动检测本机已安装浏览器（含国内浏览器），也可手动指定 exe
- 以「方案 -> 任务」组织：每个任务 = 一个浏览器 + 若干网址 + 启动模式
- 一键同时启动方案内所有任务，每个浏览器打开各自对应的网站
- 可在设置中让软件启动时自动调整屏幕缩放，退出时还原
- 可在启动浏览器后自动把窗口摆到指定位置（如左上四分之一屏）
- 可让打开的浏览器静音（全局默认 + 每个任务单独指定）
- 配置自动保存在脚本同目录的 config.json
"""

import ctypes
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import winreg
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog

APP_NAME = "mua"
APP_VERSION = "0.5.0"
APP_AUTHOR = "牧"

# 打包成 exe 后 __file__ 指向临时解压目录，配置必须落在 exe 所在目录才能持久保存
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

MODES = {
    "tabs": "单窗口多标签",
    "windows": "每个网址独立窗口",
    "private": "无痕/隐私模式",
}

# 浏览器窗口摆放方式；"" 表示跟随全局设置
LAYOUTS = {
    "": "跟随全局设置",
    "none": "不调整",
    "tl": "左上四分之一",
    "tr": "右上四分之一",
    "bl": "左下四分之一",
    "br": "右下四分之一",
    "left": "左半屏",
    "right": "右半屏",
    "top": "上半屏",
    "bottom": "下半屏",
    "center": "居中（2/3）",
    "max": "最大化",
}
LAYOUT_ORDER = ["", "none", "tl", "tr", "bl", "br", "left", "right", "top", "bottom", "center", "max"]
GLOBAL_LAYOUT_ORDER = [k for k in LAYOUT_ORDER if k]

# Windows 屏幕缩放档位（系统内部只认「相对推荐值的档位偏移」，见 DpiScaler）
DPI_STEPS = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)

DEFAULT_SETTINGS = {
    "dpi_enabled": False,        # 启动时自动调整屏幕缩放
    "dpi_target": None,          # None = 系统推荐；否则为 100/125/150...
    "dpi_restore_on_exit": True,  # 退出时还原
    "layout_enabled": True,      # 启动浏览器后自动调整窗口位置
    "layout_default": "none",    # 全局默认摆放方式
    "layout_delay": 900,         # 窗口出现后等待多久再摆放（毫秒）
    "mute_enabled": False,       # 启动浏览器后自动静音
    "mute_delay": 3000,          # 等网页出声后再静音（毫秒）
    "dpi_primary_key": "",       # 缓存主显示器对应的注册表键，便于读取当前缩放
}

# 任务级静音选项："" 表示跟随全局设置
MUTE_OPTIONS = {
    "": "跟随全局设置",
    "on": "静音",
    "off": "不静音",
}
MUTE_ORDER = ["", "on", "off"]

# 新建用户数据目录时抑制「首次运行引导 / 恢复会话气泡」，保证网址直接打开
CHROMIUM_BASE_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-restore-session-state",
]


def _usable_dir(d):
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def get_profile_dir():
    """浏览器独立用户数据目录的根目录（exe 装在 Program Files 等只读位置时自动回退）"""
    for d in (os.path.join(APP_DIR, "browser_profiles"),
              os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "BrowserLauncher", "profiles")):
        if _usable_dir(d):
            return d
    return tempfile.mkdtemp(prefix="browser_launcher_")


CHROMIUM_EXE = {
    "chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe",
    "launcher.exe", "chromium.exe", "360chrome.exe", "360se.exe",
    "qqbrowser.exe", "sogouexplorer.exe", "sogouexplorer_browser.exe",
    "yandex.exe", "whale.exe", "twinkstar.exe", "liebao.exe",
}
FIREFOX_EXE = {"firefox.exe", "waterfox.exe", "librewolf.exe", "palemoon.exe"}

# 常见浏览器候选路径（Windows）
BROWSER_CANDIDATES = [
    ("Google Chrome", [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ]),
    ("Microsoft Edge", [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ]),
    ("Mozilla Firefox", [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ]),
    ("Brave", [
        r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]),
    ("Vivaldi", [
        r"%LocalAppData%\Programs\Vivaldi\Application\vivaldi.exe",
        r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe",
    ]),
    ("Opera", [
        r"%LocalAppData%\Programs\Opera\launcher.exe",
        r"%ProgramFiles%\Opera\launcher.exe",
    ]),
    ("360安全浏览器", [
        r"%LocalAppData%\360Chrome\Chrome\Application\360chrome.exe",
        r"%ProgramFiles(x86)%\360\360se6\Application\360se.exe",
    ]),
    ("QQ浏览器", [
        r"%ProgramFiles(x86)%\Tencent\QQBrowser\QQBrowser.exe",
        r"%ProgramFiles%\Tencent\QQBrowser\QQBrowser.exe",
    ]),
    ("搜狗浏览器", [
        r"%LocalAppData%\SogouExplorer\SogouExplorer.exe",
    ]),
]


def expand(path):
    return os.path.expandvars(path)


def new_id():
    """任务 id：不用 uuid，避免打包时引入 hashlib/OpenSSL（约 3.4MB 的 libcrypto）"""
    return "%08x" % random.getrandbits(32)


def detect_kind(path):
    """按可执行文件名判断浏览器内核，用于拼接启动参数"""
    name = os.path.basename(path).lower()
    if name in FIREFOX_EXE:
        return "firefox"
    if name in CHROMIUM_EXE:
        return "chromium"
    # Chromium 系国内常见命名兜底
    if any(k in name for k in ("chrome", "edge", "brave", "browser")):
        return "chromium"
    return "generic"


def detect_browsers():
    """扫描本机常见浏览器安装路径"""
    found = {}
    for name, paths in BROWSER_CANDIDATES:
        for p in paths:
            real = expand(p)
            if os.path.exists(real):
                found[name] = {"path": real, "kind": detect_kind(real)}
                break
    return found


def migrate_tasks(cfg):
    """旧配置默认不隔离数据目录，容易被后台实例吞掉网址，这里统一升级为开启"""
    changed = 0
    for tasks in cfg.get("profiles", {}).values():
        for t in tasks:
            if "isolate" not in t:
                t["isolate"] = True
                t.pop("separate", None)
                changed += 1
    return changed


def normalize_url(u):
    u = (u or "").strip()
    if not u:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
        return u
    if u.startswith(("about:", "chrome://", "edge://", "file://", "view-source:")):
        return u
    return "https://" + u.lstrip("/")


def build_commands(path, kind, mode, urls, isolate=True, task_id="", mute=False):
    """生成启动命令参数列表，每个元素对应一次进程启动"""
    name = os.path.basename(path).lower()
    cmds = []

    if kind == "firefox":
        if mode == "windows":
            for u in urls:
                cmds.append(["-new-window", u])
        elif mode == "private":
            for u in urls:
                cmds.append(["-private-window", u])
        else:
            cmds.append(list(urls))
        return cmds

    if kind == "chromium":
        priv = ["--inprivate"] if name.startswith("msedge") else ["--incognito"]
        if mode == "windows":
            for u in urls:
                cmds.append(["--new-window", u])
        elif mode == "private":
            cmds.append(["--new-window"] + priv + list(urls))
        else:
            cmds.append(["--new-window"] + list(urls))
        prefix = []
        if isolate:
            # 关键：不使用默认数据目录，否则新进程会把网址转交给已在后台运行的
            # Edge/Chrome 常驻实例（--no-startup-window / Startup Boost），网址会被吞掉
            data_dir = os.path.join(get_profile_dir(), task_id or new_id())
            os.makedirs(data_dir, exist_ok=True)
            prefix = ["--user-data-dir=" + data_dir] + CHROMIUM_BASE_FLAGS
        if mute:
            # 内核自带开关，网页一出声就是静音的，比事后调系统音量可靠
            prefix = prefix + ["--mute-audio"]
        if prefix:
            cmds = [prefix + c for c in cmds]
        return cmds

    # 其它浏览器：直接把网址作为参数传进去
    cmds.append(list(urls))
    return cmds


# ======================= Windows 底层：屏幕缩放 / 窗口摆放 =======================
user32 = ctypes.WinDLL("user32", use_last_error=True)

SPI_GETLOGICALDPIOVERRIDE = 0x009E   # 未公开：读取「推荐缩放」档位
SPI_SETLOGICALDPIOVERRIDE = 0x009F   # 未公开：按档位偏移设置缩放（立即生效，仅主显示器）
SPIF_UPDATEINIFILE = 0x0001

SW_RESTORE = 9
SW_MAXIMIZE = 3
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
MONITOR_DEFAULTTONEAREST = 2
GA_ROOT = 2

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

# Chromium / Firefox 系主窗口类名
BROWSER_WINDOW_CLASSES = {
    "Chrome_WidgetWin_0", "Chrome_WidgetWin_1", "MozillaWindowClass",
    "ApplicationFrameWindow", "SunAwtFrame", "IronFrame",
}
# 明显不是浏览器窗口的类名，避免误移动
NON_BROWSER_WINDOW_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow", "ApplicationManager_DesktopShellWindow", "MultitaskingViewFrame",
    "XamlExplorerHostIslandWindow", "TaskManagerWindow", "ForegroundStaging", "#32770",
}


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class DEVMODEW(ctypes.Structure):
    """EnumDisplaySettings 用；只用到 dmPelsWidth"""
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
        ("dmPosition", POINTL), ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def _signed_dword(v):
    return v - 0x100000000 if v is not None and v > 0x7FFFFFFF else v


class DpiScaler:
    """调整 Windows 显示缩放（主显示器）。

    系统内部不理解百分比，只理解「相对推荐缩放的档位偏移」：
      DpiValue（注册表）/ 相对索引 = 目标档位索引 - 推荐档位索引
      DPI_STEPS 中的索引才是档位；100~250 每档 25%，之后每档 50%。
    """

    REG_PATH = r"Control Panel\Desktop\PerMonitorSettings"

    @classmethod
    def recommended_index(cls):
        v = ctypes.c_int(0)
        ok = user32.SystemParametersInfoW(SPI_GETLOGICALDPIOVERRIDE, 0,
                                          ctypes.byref(v), SPIF_UPDATEINIFILE)
        if not ok:
            return None
        idx = -v.value          # 接口返回 -推荐索引
        if 0 <= idx < len(DPI_STEPS):
            return idx
        return None

    @classmethod
    def recommended_percent(cls):
        idx = cls.recommended_index()
        return DPI_STEPS[idx] if idx is not None else None

    @classmethod
    def _set_relative(cls, rel):
        return bool(user32.SystemParametersInfoW(SPI_SETLOGICALDPIOVERRIDE,
                                                 rel & 0xFFFFFFFF, None, SPIF_UPDATEINIFILE))

    @classmethod
    def snapshot(cls):
        """{显示器键: DpiValue 有符号值}"""
        out = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.REG_PATH) as root:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            out[sub] = _signed_dword(winreg.QueryValueEx(k, "DpiValue")[0])
                    except OSError:
                        out[sub] = None
        except OSError:
            pass
        return out

    @classmethod
    def _write_value(cls, monitor_key, value):
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                    cls.REG_PATH + "\\" + monitor_key, 0,
                                    winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "DpiValue", 0, winreg.REG_DWORD,
                                  (value if value is not None else 0) & 0xFFFFFFFF)
        except OSError:
            pass

    @classmethod
    def current_percent(cls, primary_key=""):
        """读取主显示器当前缩放百分比：优先查注册表，其次用分辨率比值推算"""
        rec = cls.recommended_index()
        if rec is not None and primary_key:
            snap = cls.snapshot()
            if primary_key in snap:
                idx = rec + (snap.get(primary_key) or 0)
                idx = max(0, min(idx, len(DPI_STEPS) - 1))
                return DPI_STEPS[idx]
        return cls.current_percent_by_metrics()

    @classmethod
    def current_percent_by_metrics(cls):
        """逻辑分辨率(DPI 缩放后)与物理分辨率的比值即当前缩放，不依赖进程 DPI 感知"""
        logical = user32.GetSystemMetrics(0)
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if not logical or not user32.EnumDisplaySettingsW(None, 0xFFFFFFFF, ctypes.byref(dm)):
            return None
        physical = dm.dmPelsWidth
        if not physical:
            return None
        ratio = physical * 100.0 / logical
        nearest = min(DPI_STEPS, key=lambda s: abs(s - ratio))
        return nearest if abs(nearest - ratio) <= 10 else None

    @classmethod
    def apply(cls, percent, known_primary_key=""):
        """percent=None 表示系统推荐；返回还原所需的状态（写进 config）"""
        rec = cls.recommended_index()
        if rec is None:
            raise RuntimeError("无法读取系统推荐缩放，可能是系统版本不支持")
        if percent is None:
            rel = 0
        else:
            percent = int(percent)
            if percent not in DPI_STEPS:
                raise ValueError(f"不支持的缩放比例：{percent}%")
            rel = DPI_STEPS.index(percent) - rec

        before = cls.snapshot()
        if not cls._set_relative(rel):
            raise RuntimeError("调用系统接口修改缩放失败")
        after = cls.snapshot()

        # 只有主显示器对应的键会随接口变化，借此识别它（用于读取当前值/还原）
        primary_key, restore_rel = "", rel
        for key, val in after.items():
            if before.get(key) != val:
                primary_key = key
                restore_rel = before.get(key) or 0   # 原先没有该值 => 当时是推荐档位
                break
        else:
            # 目标缩放与当前一致时看不出变化，退而使用缓存/已知信息
            if known_primary_key and known_primary_key in before:
                primary_key = known_primary_key
                restore_rel = before.get(known_primary_key) or 0
            # 否则 restore_rel 保持为 rel：还原时等于不动，绝不会跳到别的档位
        return {
            "before": before,
            "primary_key": primary_key,
            "restore_rel": restore_rel,
            "rel": rel,
            "applied_percent": DPI_STEPS[max(0, min(rec + rel, len(DPI_STEPS) - 1))],
        }

    @classmethod
    def restore(cls, state):
        """按状态还原到调整前的缩放"""
        if not state:
            return False
        for key, val in (state.get("before") or {}).items():
            cls._write_value(key, val)
        rel = state.get("restore_rel")
        if rel is None:
            rel = state.get("rel", 0)
        return cls._set_relative(rel)


def list_top_windows():
    """当前所有可见顶层窗口的句柄集合"""
    found = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return set(found)


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def is_main_window(hwnd):
    """像不像一个真正能看见的浏览器主窗口（排除工具窗口、无标题的辅助窗口等）"""
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return False
    cls = _class_name(hwnd)
    if cls in NON_BROWSER_WINDOW_CLASSES:
        return False
    if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    if not _window_text(hwnd):          # 空标题多半是 Chromium 的辅助窗口
        return False
    return True


def find_new_browser_windows(before):
    """相对于 before 快照新出现的浏览器主窗口"""
    result = []
    for hwnd in list_top_windows():
        if hwnd in before:
            continue
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        if root_hwnd in before:
            continue
        if not is_main_window(root_hwnd):
            continue
        if _class_name(root_hwnd) not in BROWSER_WINDOW_CLASSES:
            continue        # 兜底路径只认已知浏览器窗口类，避免误动别的程序
        if root_hwnd not in result:
            result.append(root_hwnd)
    return result


TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def descendant_pids(root_pid):
    """包含自身的进程树（浏览器常会再由子进程创建窗口）"""
    pmap = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap:
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                pmap[entry.th32ProcessID] = entry.th32ParentProcessID
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
    pids = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in pmap.items():
            if ppid in pids and pid not in pids:
                pids.add(pid)
                changed = True
    return pids


def find_windows_by_pids(pids):
    """属于指定进程（树）的可见顶层浏览器窗口"""
    result = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        if not is_main_window(root_hwnd):
            return True
        if root_hwnd not in result:
            result.append(root_hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return result


def monitor_work_area(hwnd):
    """窗口所在显示器的工作区（已排除任务栏），返回 (x, y, w, h)"""
    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return None
    r = mi.rcWork
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return (r.left, r.top, w, h)


def layout_rect(layout, area):
    """把布局换算成 (x, y, w, h)"""
    left, top, w, h = area
    hw, hh = w // 2, h // 2
    rects = {
        "tl": (left, top, hw, hh),
        "tr": (left + hw, top, w - hw, hh),
        "bl": (left, top + hh, hw, h - hh),
        "br": (left + hw, top + hh, w - hw, h - hh),
        "left": (left, top, hw, h),
        "right": (left + hw, top, w - hw, h),
        "top": (left, top, w, hh),
        "bottom": (left, top + hh, w, h - hh),
        "center": (left + w // 6, top + h // 6, w * 2 // 3, h * 2 // 3),
        "full": (left, top, w, h),
    }
    return rects.get(layout)


def place_window(hwnd, layout):
    """按布局摆放窗口，成功返回 True"""
    if not layout or layout == "none":
        return False
    try:
        if layout == "max":
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            return True
        area = monitor_work_area(hwnd)
        rect = layout_rect(layout, area) if area else None
        if not rect:
            return False
        if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        x, y, w, h = rect
        user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        return True
    except Exception:
        return False


# ======================= Windows 底层：按进程静音（Core Audio） =======================
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioSessionManager2 = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
IID_IAudioSessionControl2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
IID_ISimpleAudioVolume = "{87CE5498-68D6-44E5-9215-6DA47EF883D8}"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
ole32.CLSIDFromString.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long


def _guid(text):
    g = GUID()
    ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


def _vcall(obj, index, restype, *argtypes):
    """按 vtable 下标调用 COM 方法"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _com_release(obj):
    if obj:
        try:
            _vcall(obj, 2, ctypes.c_ulong)(obj)
        except Exception:
            pass


class AudioMuter:
    """通过 Windows Core Audio 的音频会话，把指定进程（树）的声音静音/恢复"""

    _com_ready = False

    @classmethod
    def _ensure_com(cls):
        if not cls._com_ready:
            try:
                ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            except Exception:
                pass
            cls._com_ready = True

    @classmethod
    def set_mute_pids(cls, pids, mute=True):
        """返回被处理的音频会话数量"""
        if not pids:
            return 0
        cls._ensure_com()
        clsid = _guid(CLSID_MMDeviceEnumerator)
        iid_enum = _guid(IID_IMMDeviceEnumerator)
        p_enum = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL,
                                  ctypes.byref(iid_enum), ctypes.byref(p_enum)) != 0 or not p_enum:
            return 0

        p_dev = ctypes.c_void_p()
        p_mgr = ctypes.c_void_p()
        p_sessions = ctypes.c_void_p()
        handled = 0
        try:
            # GetDefaultAudioEndpoint(eRender, eMultimedia)
            if _vcall(p_enum, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int,
                      ctypes.POINTER(ctypes.c_void_p))(
                    p_enum, 0, 1, ctypes.byref(p_dev)) != 0 or not p_dev:
                return 0
            iid_mgr = _guid(IID_IAudioSessionManager2)
            # IMMDevice::Activate
            if _vcall(p_dev, 3, ctypes.c_long, ctypes.POINTER(GUID), wintypes.DWORD,
                      ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                    p_dev, ctypes.byref(iid_mgr), CLSCTX_ALL, None, ctypes.byref(p_mgr)) != 0 or not p_mgr:
                return 0
            # IAudioSessionManager2::GetSessionEnumerator
            if _vcall(p_mgr, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(
                    p_mgr, ctypes.byref(p_sessions)) != 0 or not p_sessions:
                return 0

            count = ctypes.c_int(0)
            _vcall(p_sessions, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(
                p_sessions, ctypes.byref(count))

            for i in range(max(0, count.value)):
                p_ctl = ctypes.c_void_p()
                if _vcall(p_sessions, 4, ctypes.c_long, ctypes.c_int,
                          ctypes.POINTER(ctypes.c_void_p))(
                        p_sessions, i, ctypes.byref(p_ctl)) != 0 or not p_ctl:
                    continue
                p_ctl2 = ctypes.c_void_p()
                p_vol = ctypes.c_void_p()
                try:
                    iid_ctl2 = _guid(IID_IAudioSessionControl2)
                    if _vcall(p_ctl, 0, ctypes.c_long, ctypes.POINTER(GUID),
                              ctypes.POINTER(ctypes.c_void_p))(
                            p_ctl, ctypes.byref(iid_ctl2), ctypes.byref(p_ctl2)) != 0 or not p_ctl2:
                        continue
                    pid = wintypes.DWORD(0)
                    if _vcall(p_ctl2, 14, ctypes.c_long, ctypes.POINTER(wintypes.DWORD))(
                            p_ctl2, ctypes.byref(pid)) != 0:
                        continue
                    if pid.value not in pids:
                        continue
                    iid_vol = _guid(IID_ISimpleAudioVolume)
                    if _vcall(p_ctl, 0, ctypes.c_long, ctypes.POINTER(GUID),
                              ctypes.POINTER(ctypes.c_void_p))(
                            p_ctl, ctypes.byref(iid_vol), ctypes.byref(p_vol)) != 0 or not p_vol:
                        continue
                    # ISimpleAudioVolume::SetMute
                    if _vcall(p_vol, 5, ctypes.c_long, wintypes.BOOL, ctypes.c_void_p)(
                            p_vol, 1 if mute else 0, None) == 0:
                        handled += 1
                finally:
                    _com_release(p_vol)
                    _com_release(p_ctl2)
                    _com_release(p_ctl)
        except Exception:
            pass
        finally:
            for obj in (p_sessions, p_mgr, p_dev, p_enum):
                _com_release(obj)
        return handled


class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1080x700")
        self.root.minsize(960, 620)

        self._migrated = 0
        self.config = self.load_config()
        self.settings = self.config["settings"]
        self.processes = []          # [(pid, 任务名)]
        self.current_task_id = None
        self._dpi_applied = False
        self._claimed_hwnds = set()  # 已被摆放过的窗口，避免重复/误判
        self._placed_logged = set()
        self._mute_logged = set()

        self._build_ui()
        if self._migrated:
            self._log(f"[提示] 已为 {self._migrated} 个任务启用「独立用户数据目录」，避免网址打不开。")
            self.save_config()
        self._refresh_profile_combo()
        self._refresh_task_list()
        self._refresh_browser_list()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if not os.path.exists(CONFIG_PATH):
            self.save_config()   # 首次运行立即生成配置文件，便于查看/手动编辑
        self.root.after(200, self._startup_dpi)

    # ---------------- 配置 ----------------
    def load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg.setdefault("profiles", {})
                cfg.setdefault("browsers", {})
                cfg.setdefault("delay", 0.5)
                settings = cfg.setdefault("settings", {})
                for k, v in DEFAULT_SETTINGS.items():
                    settings.setdefault(k, v)
                self._migrated = migrate_tasks(cfg)
                return cfg
            except Exception:
                pass
        browsers = detect_browsers()
        return {
            "profiles": {
                "默认方案": [
                    {
                        "id": new_id(),
                        "name": "示例任务",
                        "browser": next(iter(browsers), ""),
                        "mode": "tabs",
                        "isolate": True,
                        "layout": "",
                        "urls": ["https://www.bing.com"],
                    }
                ] if browsers else [],
            },
            "browsers": browsers,
            "current": "默认方案",
            "delay": 0.5,
            "settings": dict(DEFAULT_SETTINGS),
        }


    def save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"[错误] 保存配置失败：{e}")

    def current_profile(self):
        return self.config.get("current", "")

    def current_tasks(self):
        return self.config["profiles"].get(self.current_profile(), [])

    # ---------------- UI ----------------
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        tool_menu = tk.Menu(menubar, tearoff=0)
        tool_menu.add_command(label="打开设置", command=self._open_settings_tab)
        menubar.add_cascade(label="工具", menu=tool_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=f"关于 {APP_NAME}", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

    def _open_settings_tab(self):
        self.notebook.select(self.tab_settings)
        self._refresh_dpi_status()

    def _show_about(self):
        win = tk.Toplevel(self.root)
        win.title(f"关于 {APP_NAME}")
        win.resizable(False, False)
        win.transient(self.root)
        box = ttk.Frame(win, padding=28)
        box.pack()
        ttk.Label(box, text=APP_NAME, font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(box, text=f"版本 {APP_VERSION}").pack(pady=(8, 2))
        ttk.Label(box, text=f"作者：{APP_AUTHOR}").pack(pady=(2, 2))
        ttk.Label(box, text="多浏览器批量启动器").pack(pady=(2, 12))
        ttk.Button(box, text="确定", command=win.destroy).pack()
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_height()) // 3)
        win.geometry(f"+{x}+{y}")
        win.grab_set()

    def _build_ui(self):
        self._build_menu()
        # 顶部：方案管理
        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="方案：").pack(side="left")
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(top, textvariable=self.profile_var,
                                          state="readonly", width=22)
        self.profile_combo.pack(side="left", padx=4)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda e: self._on_switch_profile())
        ttk.Button(top, text="新建", command=self._new_profile).pack(side="left", padx=2)
        ttk.Button(top, text="复制", command=self._copy_profile).pack(side="left", padx=2)
        ttk.Button(top, text="重命名", command=self._rename_profile).pack(side="left", padx=2)
        ttk.Button(top, text="删除", command=self._delete_profile).pack(side="left", padx=2)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(top, text="启动间隔(秒)：").pack(side="left")
        self.delay_var = tk.StringVar(value=str(self.config.get("delay", 0.5)))
        ttk.Spinbox(top, from_=0, to=10, increment=0.5, width=5,
                    textvariable=self.delay_var,
                    command=self._on_delay_change).pack(side="left", padx=2)
        ttk.Button(top, text="▶ 启动本方案全部任务",
                   command=self._start_all).pack(side="right", padx=4)

        # 中部：标签页
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        tab_tasks = ttk.Frame(nb, padding=6)
        tab_browsers = ttk.Frame(nb, padding=6)
        tab_settings = ttk.Frame(nb, padding=6)
        nb.add(tab_tasks, text="任务配置")
        nb.add(tab_browsers, text="浏览器管理")
        nb.add(tab_settings, text="设置")
        self.notebook = nb
        self.tab_settings = tab_settings
        self._build_tasks_tab(tab_tasks)
        self._build_browsers_tab(tab_browsers)
        self._build_settings_tab(tab_settings)

        # 底部：日志
        bottom = ttk.Frame(self.root, padding=(8, 2, 8, 8))
        bottom.pack(fill="x")
        line = ttk.Frame(bottom)
        line.pack(fill="x")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(line, textvariable=self.status_var).pack(side="left")
        ttk.Button(line, text="关闭已启动的浏览器",
                   command=self._close_all).pack(side="right", padx=4)
        ttk.Button(line, text="清空日志", command=self._clear_log).pack(side="right")
        self.log = scrolledtext.ScrolledText(bottom, height=7, font=("Consolas", 10))
        self.log.pack(fill="x", pady=4)
        self.log.configure(state="disabled")

    def _build_tasks_tab(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="＋ 新建任务", command=self._new_task).pack(side="left", padx=2)
        ttk.Button(toolbar, text="复制", command=self._copy_task).pack(side="left", padx=2)
        ttk.Button(toolbar, text="删除", command=self._delete_task).pack(side="left", padx=2)
        ttk.Button(toolbar, text="↑", width=3, command=lambda: self._move_task(-1)).pack(side="left", padx=2)
        ttk.Button(toolbar, text="↓", width=3, command=lambda: self._move_task(1)).pack(side="left", padx=2)
        ttk.Button(toolbar, text="▶ 启动选中任务",
                   command=self._start_selected).pack(side="right", padx=2)

        pane = ttk.PanedWindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # 左：任务列表
        left = ttk.Frame(pane)
        pane.add(left, weight=1)
        cols = ("name", "browser", "mode", "count")
        self.task_tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
        self.task_tree.heading("name", text="任务名称")
        self.task_tree.heading("browser", text="浏览器")
        self.task_tree.heading("mode", text="启动模式")
        self.task_tree.heading("count", text="网址数")
        self.task_tree.column("name", width=150)
        self.task_tree.column("browser", width=130)
        self.task_tree.column("mode", width=130)
        self.task_tree.column("count", width=60, anchor="center")
        self.task_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.task_tree.yview)
        sb.pack(fill="y", side="right")
        self.task_tree.configure(yscrollcommand=sb.set)
        self.task_tree.bind("<<TreeviewSelect>>", lambda e: self._on_select_task())

        # 右：任务编辑
        right = ttk.Frame(pane, padding=(10, 0, 0, 0))
        pane.add(right, weight=2)
        self._build_task_editor(right)

    def _build_task_editor(self, parent):
        box = ttk.LabelFrame(parent, text="任务详情", padding=8)
        box.pack(fill="both", expand=True)

        row1 = ttk.Frame(box)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="名称：", width=10).pack(side="left")
        self.task_name = tk.StringVar()
        ttk.Entry(row1, textvariable=self.task_name).pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=3)
        ttk.Label(row2, text="浏览器：", width=10).pack(side="left")
        self.browser_var = tk.StringVar()
        self.browser_combo = ttk.Combobox(row2, textvariable=self.browser_var,
                                          state="readonly", width=26)
        self.browser_combo.pack(side="left")
        ttk.Button(row2, text="选择 exe…", command=self._pick_browser_exe).pack(side="left", padx=4)

        row3 = ttk.Frame(box)
        row3.pack(fill="x", pady=3)
        ttk.Label(row3, text="启动模式：", width=10).pack(side="left")
        self.mode_var = tk.StringVar(value="tabs")
        mode_combo = ttk.Combobox(row3, textvariable=self.mode_var, state="readonly", width=26)
        mode_combo["values"] = [f"{k} | {v}" for k, v in MODES.items()]
        mode_combo.pack(side="left")
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_mode_display())

        row3b = ttk.Frame(box)
        row3b.pack(fill="x", pady=3)
        ttk.Label(row3b, text="窗口位置：", width=10).pack(side="left")
        self.task_layout_var = tk.StringVar(value=LAYOUTS[""])
        self.task_layout_combo = ttk.Combobox(row3b, textvariable=self.task_layout_var,
                                              state="readonly", width=26)
        self.task_layout_combo["values"] = [LAYOUTS[k] for k in LAYOUT_ORDER]
        self.task_layout_combo.pack(side="left")

        row3c = ttk.Frame(box)
        row3c.pack(fill="x", pady=3)
        ttk.Label(row3c, text="声音：", width=10).pack(side="left")
        self.task_mute_var = tk.StringVar(value=MUTE_OPTIONS[""])
        self.task_mute_combo = ttk.Combobox(row3c, textvariable=self.task_mute_var,
                                            state="readonly", width=26)
        self.task_mute_combo["values"] = [MUTE_OPTIONS[k] for k in MUTE_ORDER]
        self.task_mute_combo.pack(side="left")

        row4 = ttk.Frame(box)
        row4.pack(fill="x", pady=3)
        self.isolate_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row4, text="独立用户数据目录（推荐）",
                        variable=self.isolate_var).pack(side="left")
        ttk.Label(box, text="说明：Edge/Chrome/国产 Chromium 浏览器常有后台常驻进程，若共用默认配置，网址会被后台实例吞掉导致打不开；\n勾选后每个任务用各自的配置文件启动，既能可靠打开网址，也能多开互不干扰。",
                  foreground="#666666").pack(anchor="w")

        # 网址列表
        ttk.Label(box, text="网址列表：").pack(anchor="w", pady=(8, 2))
        url_frame = ttk.Frame(box)
        url_frame.pack(fill="both", expand=True)
        self.url_list = tk.Listbox(url_frame, font=("Consolas", 10))
        self.url_list.pack(fill="both", expand=True, side="left")
        usb = ttk.Scrollbar(url_frame, orient="vertical", command=self.url_list.yview)
        usb.pack(fill="y", side="right")
        self.url_list.configure(yscrollcommand=usb.set)

        url_tools = ttk.Frame(box)
        url_tools.pack(fill="x", pady=4)
        ttk.Button(url_tools, text="添加", command=self._add_url).pack(side="left", padx=2)
        ttk.Button(url_tools, text="编辑", command=self._edit_url).pack(side="left", padx=2)
        ttk.Button(url_tools, text="删除", command=self._remove_url).pack(side="left", padx=2)
        ttk.Button(url_tools, text="↑", width=3, command=lambda: self._move_url(-1)).pack(side="left", padx=2)
        ttk.Button(url_tools, text="↓", width=3, command=lambda: self._move_url(1)).pack(side="left", padx=2)
        ttk.Button(url_tools, text="批量导入", command=self._import_urls).pack(side="left", padx=2)
        ttk.Button(url_tools, text="清空", command=self._clear_urls).pack(side="left", padx=2)

        ttk.Button(box, text="💾 保存任务修改", command=self._save_task).pack(fill="x", pady=(6, 0))

    def _build_browsers_tab(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="🔍 自动检测", command=self._auto_detect).pack(side="left", padx=2)
        ttk.Button(toolbar, text="＋ 手动添加", command=self._add_browser).pack(side="left", padx=2)
        ttk.Button(toolbar, text="修改路径", command=self._edit_browser).pack(side="left", padx=2)
        ttk.Button(toolbar, text="重命名", command=self._rename_browser).pack(side="left", padx=2)
        ttk.Button(toolbar, text="删除", command=self._remove_browser).pack(side="left", padx=2)
        ttk.Button(toolbar, text="测试启动", command=self._test_browser).pack(side="left", padx=2)

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        cols = ("name", "kind", "path")
        self.br_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.br_tree.heading("name", text="名称")
        self.br_tree.heading("kind", text="内核")
        self.br_tree.heading("path", text="路径")
        self.br_tree.column("name", width=160)
        self.br_tree.column("kind", width=90)
        self.br_tree.column("path", width=520)
        self.br_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.br_tree.yview)
        sb.pack(fill="y", side="right")
        self.br_tree.configure(yscrollcommand=sb.set)
        ttk.Label(parent, text="提示：内核用于决定启动参数（Chromium 系支持多标签/无痕/独立数据目录，Firefox 系用 -new-window / -private-window）。").pack(anchor="w", pady=4)

    # ---------------- 设置 ----------------
    def _build_settings_tab(self, parent):
        st = self.settings

        # —— 屏幕缩放 ——
        dpi_box = ttk.LabelFrame(parent, text="屏幕缩放（主显示器）", padding=10)
        dpi_box.pack(fill="x", pady=(0, 8))

        self.dpi_enabled_var = tk.BooleanVar(value=bool(st.get("dpi_enabled")))
        ttk.Checkbutton(dpi_box, text="启动软件时自动把屏幕缩放调整为：",
                        variable=self.dpi_enabled_var,
                        command=self._on_settings_change).grid(row=0, column=0, columnspan=2, sticky="w")

        self.dpi_target_var = tk.StringVar(value=self._dpi_target_label(st.get("dpi_target")))
        dpi_combo = ttk.Combobox(dpi_box, textvariable=self.dpi_target_var, state="readonly",
                                 width=14, values=self._dpi_target_labels())
        dpi_combo.grid(row=0, column=2, sticky="w", padx=8)
        dpi_combo.bind("<<ComboboxSelected>>", lambda e: self._on_settings_change())

        self.dpi_restore_var = tk.BooleanVar(value=bool(st.get("dpi_restore_on_exit", True)))
        ttk.Checkbutton(dpi_box, text="关闭软件时恢复为启动前的缩放（推荐开启）",
                        variable=self.dpi_restore_var,
                        command=self._on_settings_change).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        btns = ttk.Frame(dpi_box)
        btns.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 4))
        ttk.Button(btns, text="立即应用", width=12, command=self._apply_dpi_manual).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="立即恢复", width=12, command=self._restore_dpi_manual).pack(side="left")

        self.dpi_status_var = tk.StringVar(value="")
        ttk.Label(dpi_box, textvariable=self.dpi_status_var, foreground="#666666",
                  justify="left").grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Label(dpi_box, justify="left", foreground="#666666",
                  text="说明：Windows 只允许程序即时调整主显示器的缩放，多屏时副屏不受影响；\n"
                       "可选档位为 100/125/150/175/200/225/250%，250% 之后每档 50%。").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # —— 窗口位置 ——
        win_box = ttk.LabelFrame(parent, text="浏览器窗口位置", padding=10)
        win_box.pack(fill="x")

        self.layout_enabled_var = tk.BooleanVar(value=bool(st.get("layout_enabled", True)))
        ttk.Checkbutton(win_box, text="启动浏览器后自动调整窗口位置",
                        variable=self.layout_enabled_var,
                        command=self._on_settings_change).grid(row=0, column=0, columnspan=4, sticky="w")

        ttk.Label(win_box, text="默认摆放方式：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.layout_default_var = tk.StringVar(
            value=LAYOUTS.get(st.get("layout_default", "none"), LAYOUTS["none"]))
        lay_combo = ttk.Combobox(win_box, textvariable=self.layout_default_var, state="readonly",
                                 width=16, values=[LAYOUTS[k] for k in GLOBAL_LAYOUT_ORDER])
        lay_combo.grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        lay_combo.bind("<<ComboboxSelected>>", lambda e: self._on_settings_change())

        ttk.Label(win_box, text="生效延迟(毫秒)：").grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        self.layout_delay_var = tk.StringVar(value=str(st.get("layout_delay", 900)))
        delay_spin = ttk.Spinbox(win_box, from_=200, to=8000, increment=100, width=7,
                                 textvariable=self.layout_delay_var,
                                 command=self._on_settings_change)
        delay_spin.grid(row=1, column=3, sticky="w", padx=8, pady=(6, 0))
        delay_spin.bind("<FocusOut>", lambda e: self._on_settings_change())
        delay_spin.bind("<Return>", lambda e: self._on_settings_change())

        ttk.Label(win_box, justify="left", foreground="#666666",
                  text="说明：可为每个任务单独指定摆放方式（任务详情里的「窗口位置」），选「跟随全局设置」就用上面的默认方式。\n"
                       "浏览器窗口完全打开后才会被摆放，延迟越大越稳；窗口会放在它所在显示器的工作区内（自动避开任务栏）。").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        # —— 声音 ——
        snd_box = ttk.LabelFrame(parent, text="浏览器声音", padding=10)
        snd_box.pack(fill="x", pady=(8, 0))

        self.mute_enabled_var = tk.BooleanVar(value=bool(st.get("mute_enabled")))
        ttk.Checkbutton(snd_box, text="启动浏览器后自动静音（任务里可单独覆盖）",
                        variable=self.mute_enabled_var,
                        command=self._on_settings_change).grid(row=0, column=0, columnspan=4, sticky="w")

        snd_btns = ttk.Frame(snd_box)
        snd_btns.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(snd_btns, text="静音已启动的浏览器", width=18,
                   command=lambda: self._mute_all(True)).pack(side="left", padx=(0, 6))
        ttk.Button(snd_btns, text="取消静音", width=12,
                   command=lambda: self._mute_all(False)).pack(side="left")

        ttk.Label(snd_box, justify="left", foreground="#666666",
                  text="说明：Chromium 系（Chrome/Edge/国产双核等）启动时直接加 --mute-audio，网页一出声就是静音；\n"
                       "其它浏览器（Firefox 等）通过系统音频会话静音，网页开始播放后约 3 秒生效。").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

    def _dpi_target_labels(self):
        return ["系统推荐"] + [f"{p}%" for p in DPI_STEPS]

    def _dpi_target_label(self, percent):
        return "系统推荐" if not percent else f"{int(percent)}%"

    def _dpi_target_value(self):
        txt = (self.dpi_target_var.get() or "").strip()
        if not txt or txt == "系统推荐":
            return None
        try:
            return int(txt.rstrip("%"))
        except ValueError:
            return None

    def _on_settings_change(self):
        st = self.settings
        st["dpi_enabled"] = bool(self.dpi_enabled_var.get())
        st["dpi_target"] = self._dpi_target_value()
        st["dpi_restore_on_exit"] = bool(self.dpi_restore_var.get())
        st["layout_enabled"] = bool(self.layout_enabled_var.get())
        st["layout_default"] = next((k for k, v in LAYOUTS.items() if v == self.layout_default_var.get()), "none")
        try:
            st["layout_delay"] = max(200, int(float(self.layout_delay_var.get())))
        except ValueError:
            st["layout_delay"] = 900
        st["mute_enabled"] = bool(self.mute_enabled_var.get())
        self.save_config()
        self._refresh_dpi_status()

    def _refresh_dpi_status(self):
        rec = DpiScaler.recommended_percent()
        cur = DpiScaler.current_percent(self.settings.get("dpi_primary_key", ""))
        parts = []
        if cur:
            parts.append(f"当前缩放：{cur}%")
        if rec:
            parts.append(f"系统推荐：{rec}%")
        if self._dpi_applied:
            parts.append("已由本软件调整")
        elif self.config.get("dpi_state"):
            parts.append("上次退出未还原，可点「立即恢复」")
        self.dpi_status_var.set("　|　".join(parts) if parts else "当前缩放：未检测到（仅供参考，不影响使用）")

    def _startup_dpi(self):
        st = self.settings
        leftover = self.config.get("dpi_state")
        if leftover:
            # 上次异常退出（或强制结束进程）遗留，先还原到那时候的原始设置
            if leftover.get("restorable", True):
                try:
                    DpiScaler.restore(leftover)
                    self._log("[缩放] 已还原上次异常退出遗留的屏幕缩放设置。")
                except Exception as e:
                    self._log(f"[缩放] 还原遗留设置失败：{e}")
            self.config.pop("dpi_state", None)
            self.save_config()
        self._refresh_dpi_status()
        if st.get("dpi_enabled"):
            self._apply_dpi(st.get("dpi_target"), auto=True)

    def _apply_dpi(self, target, auto=False):
        try:
            state = DpiScaler.apply(target, self.settings.get("dpi_primary_key", ""))
        except Exception as e:
            self._log(f"[缩放] 调整失败：{e}")
            self._refresh_dpi_status()
            return False
        state["restorable"] = bool(self.settings.get("dpi_restore_on_exit", True))
        self.config["dpi_state"] = state
        self._dpi_applied = True
        if state.get("primary_key"):
            self.settings["dpi_primary_key"] = state["primary_key"]
        self.save_config()
        self._log(f"[缩放] 已{'自动' if auto else ''}调整为 {state.get('applied_percent')}%（主显示器）")
        self._refresh_dpi_status()
        return True

    def _restore_dpi(self):
        state = self.config.get("dpi_state")
        if not state:
            return False
        try:
            DpiScaler.restore(state)
        except Exception as e:
            self._log(f"[缩放] 还原失败：{e}")
            return False
        self.config.pop("dpi_state", None)
        self._dpi_applied = False
        if state.get("primary_key"):
            self.settings["dpi_primary_key"] = state["primary_key"]
        self.save_config()
        self._log("[缩放] 已还原为启动前的缩放设置。")
        self._refresh_dpi_status()
        return True

    def _apply_dpi_manual(self):
        self._on_settings_change()
        self._apply_dpi(self.settings.get("dpi_target"))

    def _restore_dpi_manual(self):
        if not self._restore_dpi():
            messagebox.showinfo("提示", "没有需要还原的缩放设置。")

    # ---------------- 方案 ----------------
    def _refresh_profile_combo(self):
        names = list(self.config["profiles"].keys())
        if not names:
            self.config["profiles"]["默认方案"] = []
            names = ["默认方案"]
        if self.current_profile() not in names:
            self.config["current"] = names[0]
        self.profile_combo["values"] = names
        self.profile_var.set(self.current_profile())

    def _on_switch_profile(self):
        self.config["current"] = self.profile_var.get()
        self.current_task_id = None
        self._refresh_task_list()
        self.save_config()

    def _new_profile(self):
        name = simpledialog.askstring("新建方案", "方案名称：")
        if name and name not in self.config["profiles"]:
            self.config["profiles"][name] = []
            self.config["current"] = name
            self._refresh_profile_combo()
            self._refresh_task_list()
            self.save_config()
            self._log(f"已新建方案：{name}")

    def _copy_profile(self):
        src = self.current_profile()
        name = simpledialog.askstring("复制方案", "新方案名称：", initialvalue=src + " 副本")
        if name and name not in self.config["profiles"]:
            import copy
            self.config["profiles"][name] = copy.deepcopy(self.current_tasks())
            # 任务 id 需要唯一
            for t in self.config["profiles"][name]:
                t["id"] = new_id()
            self.config["current"] = name
            self._refresh_profile_combo()
            self._refresh_task_list()
            self.save_config()

    def _rename_profile(self):
        old = self.current_profile()
        name = simpledialog.askstring("重命名方案", "新名称：", initialvalue=old)
        if name and name != old:
            self.config["profiles"][name] = self.config["profiles"].pop(old)
            self.config["current"] = name
            self._refresh_profile_combo()
            self.save_config()

    def _delete_profile(self):
        if len(self.config["profiles"]) <= 1:
            messagebox.showinfo("提示", "至少保留一个方案。")
            return
        old = self.current_profile()
        if messagebox.askyesno("确认", f"确定删除方案「{old}」？"):
            self.config["profiles"].pop(old, None)
            self.config["current"] = list(self.config["profiles"].keys())[0]
            self._refresh_profile_combo()
            self._refresh_task_list()
            self.save_config()

    def _on_delay_change(self):
        try:
            self.config["delay"] = float(self.delay_var.get())
            self.save_config()
        except ValueError:
            pass

    # ---------------- 任务列表 ----------------
    def _refresh_task_list(self):
        self.task_tree.delete(*self.task_tree.get_children())
        for t in self.current_tasks():
            self.task_tree.insert("", "end", iid=t["id"], values=(
                t.get("name", ""),
                t.get("browser", ""),
                MODES.get(t.get("mode", "tabs"), t.get("mode", "")),
                len(t.get("urls", [])),
            ))
        self._refresh_browser_combo()

    def _selected_task(self):
        tid = self.current_task_id
        for t in self.current_tasks():
            if t["id"] == tid:
                return t
        return None

    def _on_select_task(self):
        sel = self.task_tree.selection()
        if not sel:
            return
        self.current_task_id = sel[0]
        t = self._selected_task()
        if not t:
            return
        self.task_name.set(t.get("name", ""))
        self.browser_var.set(t.get("browser", ""))
        self.mode_var.set(t.get("mode", "tabs"))
        self.isolate_var.set(bool(t.get("isolate", True)))
        self.task_layout_var.set(LAYOUTS.get(t.get("layout", ""), LAYOUTS[""]))
        self.task_mute_var.set(MUTE_OPTIONS.get(t.get("mute", ""), MUTE_OPTIONS[""]))
        self.url_list.delete(0, "end")
        for u in t.get("urls", []):
            self.url_list.insert("end", u)
        self._sync_mode_display()

    def _sync_mode_display(self):
        val = self.mode_var.get()
        # 组合框显示 "key | 中文"，内部只存 key
        if " | " in val:
            self.mode_var.set(val.split(" | ")[0])

    def _task_layout_key(self):
        label = self.task_layout_var.get()
        return next((k for k in LAYOUT_ORDER if LAYOUTS[k] == label), "")

    def _task_mute_key(self):
        label = self.task_mute_var.get()
        return next((k for k in MUTE_ORDER if MUTE_OPTIONS[k] == label), "")

    def _new_task(self):
        browsers = list(self.config["browsers"].keys())
        if not browsers:
            messagebox.showinfo("提示", "请先在「浏览器管理」中添加浏览器。")
            return
        t = {
            "id": new_id(),
            "name": f"任务 {len(self.current_tasks()) + 1}",
            "browser": browsers[0],
            "mode": "tabs",
            "isolate": True,
            "layout": "",
            "mute": "",
            "urls": [],
        }
        self.current_tasks().append(t)
        self._refresh_task_list()
        self.task_tree.selection_set(t["id"])
        self._on_select_task()
        self.save_config()

    def _copy_task(self):
        t = self._selected_task()
        if not t:
            return
        import copy
        nt = copy.deepcopy(t)
        nt["id"] = new_id()
        nt["name"] = t["name"] + " 副本"
        self.current_tasks().append(nt)
        self._refresh_task_list()
        self.save_config()

    def _delete_task(self):
        t = self._selected_task()
        if not t:
            return
        if messagebox.askyesno("确认", f"删除任务「{t['name']}」？"):
            self.current_tasks().remove(t)
            self.current_task_id = None
            self._refresh_task_list()
            self._clear_editor()
            self.save_config()

    def _move_task(self, delta):
        tasks = self.current_tasks()
        for i, t in enumerate(tasks):
            if t["id"] == self.current_task_id:
                j = i + delta
                if 0 <= j < len(tasks):
                    tasks[i], tasks[j] = tasks[j], tasks[i]
                    self._refresh_task_list()
                    self.task_tree.selection_set(t["id"])
                    self.save_config()
                return

    def _save_task(self):
        t = self._selected_task()
        if not t:
            messagebox.showinfo("提示", "请先选中一个任务。")
            return
        t["name"] = self.task_name.get().strip() or "未命名任务"
        t["browser"] = self.browser_var.get()
        t["mode"] = self.mode_var.get()
        t["isolate"] = self.isolate_var.get()
        t["layout"] = self._task_layout_key()
        t["mute"] = self._task_mute_key()
        t["urls"] = [self.url_list.get(i) for i in range(self.url_list.size())]
        self._refresh_task_list()
        self.task_tree.selection_set(t["id"])
        self.save_config()
        self._log(f"已保存任务：{t['name']}")

    def _clear_editor(self):
        self.task_name.set("")
        self.browser_var.set("")
        self.mode_var.set("tabs")
        self.isolate_var.set(True)
        self.task_layout_var.set(LAYOUTS[""])
        self.task_mute_var.set(MUTE_OPTIONS[""])
        self.url_list.delete(0, "end")

    def _pick_browser_exe(self):
        path = filedialog.askopenfilename(title="选择浏览器可执行文件",
                                          filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.config["browsers"][name] = {"path": path, "kind": detect_kind(path)}
        self.browser_var.set(name)
        self._refresh_browser_list()
        self._refresh_browser_combo()
        self.save_config()
        self._log(f"已添加浏览器：{name} -> {path}")

    # ---------------- 网址编辑 ----------------
    def _add_url(self):
        u = simpledialog.askstring("添加网址", "网址（可省略 https://）：")
        if u:
            self.url_list.insert("end", normalize_url(u))

    def _edit_url(self):
        sel = self.url_list.curselection()
        if not sel:
            return
        i = sel[0]
        u = simpledialog.askstring("编辑网址", "网址：", initialvalue=self.url_list.get(i))
        if u:
            self.url_list.delete(i)
            self.url_list.insert(i, normalize_url(u))
            self.url_list.selection_set(i)

    def _remove_url(self):
        sel = self.url_list.curselection()
        if sel:
            self.url_list.delete(sel[0])

    def _move_url(self, delta):
        sel = self.url_list.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if 0 <= j < self.url_list.size():
            val = self.url_list.get(i)
            self.url_list.delete(i)
            self.url_list.insert(j, val)
            self.url_list.selection_set(j)

    def _import_urls(self):
        win = tk.Toplevel(self.root)
        win.title("批量导入网址")
        win.geometry("520x380")
        ttk.Label(win, text="每行一个网址（可省略 https://）：", padding=6).pack(anchor="w")
        txt = scrolledtext.ScrolledText(win, font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=8)

        def do_import():
            raw = txt.get("1.0", "end")
            added = 0
            for line in raw.splitlines():
                u = normalize_url(line)
                if u:
                    self.url_list.insert("end", u)
                    added += 1
            self._log(f"导入 {added} 个网址。")
            win.destroy()

        ttk.Button(win, text="导入", command=do_import).pack(pady=6)
        txt.focus_set()

    def _clear_urls(self):
        if messagebox.askyesno("确认", "清空该任务的网址列表？"):
            self.url_list.delete(0, "end")

    # ---------------- 浏览器管理 ----------------
    def _refresh_browser_list(self):
        self.br_tree.delete(*self.br_tree.get_children())
        for name, info in self.config["browsers"].items():
            self.br_tree.insert("", "end", iid=name, values=(
                name, info.get("kind", ""), info.get("path", "")))

    def _refresh_browser_combo(self):
        names = list(self.config["browsers"].keys())
        self.browser_combo["values"] = names
        if self.browser_var.get() not in names and names:
            self.browser_var.set(names[0])

    def _auto_detect(self):
        found = detect_browsers()
        if not found:
            messagebox.showinfo("提示", "未检测到常见浏览器，请手动添加 exe 路径。")
            return
        added = 0
        for name, info in found.items():
            if name not in self.config["browsers"]:
                self.config["browsers"][name] = info
                added += 1
            elif not os.path.exists(self.config["browsers"][name].get("path", "")):
                self.config["browsers"][name] = info
                added += 1
        self._refresh_browser_list()
        self._refresh_browser_combo()
        self.save_config()
        self._log(f"检测完成，新增/修正 {added} 个浏览器。")

    def _add_browser(self):
        self._pick_browser_exe()

    def _edit_browser(self):
        sel = self.br_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个浏览器。")
            return
        name = sel[0]
        path = filedialog.askopenfilename(title=f"选择「{name}」的可执行文件",
                                          filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if path:
            self.config["browsers"][name] = {"path": path, "kind": detect_kind(path)}
            self._refresh_browser_list()
            self.save_config()
            self._log(f"已更新「{name}」路径：{path}")

    def _rename_browser(self):
        sel = self.br_tree.selection()
        if not sel:
            return
        old = sel[0]
        new = simpledialog.askstring("重命名", "新名称：", initialvalue=old)
        if new and new != old and new not in self.config["browsers"]:
            self.config["browsers"][new] = self.config["browsers"].pop(old)
            for tasks in self.config["profiles"].values():
                for t in tasks:
                    if t.get("browser") == old:
                        t["browser"] = new
            self._refresh_browser_list()
            self._refresh_browser_combo()
            self._refresh_task_list()
            self.save_config()

    def _remove_browser(self):
        sel = self.br_tree.selection()
        if not sel:
            return
        name = sel[0]
        if messagebox.askyesno("确认", f"删除浏览器「{name}」？"):
            self.config["browsers"].pop(name, None)
            self._refresh_browser_list()
            self._refresh_browser_combo()
            self.save_config()

    def _test_browser(self):
        sel = self.br_tree.selection()
        if not sel:
            return
        info = self.config["browsers"][sel[0]]
        if not os.path.exists(info.get("path", "")):
            messagebox.showerror("错误", f"路径不存在：{info.get('path')}")
            return
        try:
            subprocess.Popen([info["path"], "about:blank"])
            self._log(f"测试启动：{sel[0]}")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ---------------- 启动 ----------------
    def _start_selected(self):
        t = self._selected_task()
        if not t:
            messagebox.showinfo("提示", "请先选中一个任务。")
            return
        self._schedule_launch([t], 0)

    def _start_all(self):
        tasks = self.current_tasks()
        if not tasks:
            messagebox.showinfo("提示", "当前方案没有任何任务。")
            return
        self._log(f"开始启动方案「{self.current_profile()}」，共 {len(tasks)} 个任务…")
        self._schedule_launch(tasks, 0)

    def _schedule_launch(self, tasks, idx):
        if idx >= len(tasks):
            self.status_var.set(f"启动完成，已启动 {len(self.processes)} 个进程")
            self._log("全部任务启动完成。")
            return
        self._launch_task(tasks[idx])
        try:
            delay = float(self.delay_var.get())
        except ValueError:
            delay = 0.5
        self.root.after(int(delay * 1000), lambda: self._schedule_launch(tasks, idx + 1))

    def _launch_task(self, task):
        name = task.get("name", "未命名")
        bname = task.get("browser", "")
        info = self.config["browsers"].get(bname)
        if not info:
            self._log(f"[跳过] 任务「{name}」未指定有效浏览器。")
            return
        path = info.get("path", "")
        if not os.path.exists(path):
            self._log(f"[错误] 任务「{name}」的浏览器路径不存在：{path}")
            return
        urls = [normalize_url(u) for u in task.get("urls", [])]
        urls = [u for u in urls if u]
        if not urls:
            self._log(f"[跳过] 任务「{name}」没有网址。")
            return

        kind = info.get("kind") or detect_kind(path)
        mute = self._resolve_mute(task)
        cmds = build_commands(path, kind, task.get("mode", "tabs"),
                              urls, task.get("isolate", True), task.get("id", ""), mute)
        for args in cmds:
            self._log(f"  > {subprocess.list2cmdline([path] + args)}")
        layout = self._resolve_layout(task)
        if layout not in ("", "none"):
            self._log(f"  窗口位置：{LAYOUTS.get(layout, layout)}（窗口打开后自动摆放）")
        if mute:
            how = "启动参数静音" if kind == "chromium" else "系统音频接口静音"
            self._log(f"  声音：静音（{how}）")
        self._spawn_cmds(path, name, bname,
                         MODES.get(task.get("mode", "tabs"), task.get("mode", "")),
                         cmds, 0, len(urls), layout, mute)

    def _resolve_layout(self, task):
        """任务自己的设置优先，否则用全局设置；全局开关关闭时一律不调整"""
        if not self.settings.get("layout_enabled", True):
            return "none"
        layout = task.get("layout") or ""
        if layout:
            return layout
        return self.settings.get("layout_default") or "none"

    def _resolve_mute(self, task):
        """任务自己的静音设置优先，其次全局开关"""
        choice = task.get("mute") or ""
        if choice == "on":
            return True
        if choice == "off":
            return False
        return bool(self.settings.get("mute_enabled"))

    def _spawn_cmds(self, path, name, bname, mode_txt, cmds, idx, url_count,
                    layout="none", mute=False):
        """逐个启动，多个进程之间留出间隔（同时瞬时启动会导致后面的网址被丢弃）"""
        if idx >= len(cmds):
            self._log(f"已启动「{name}」：{bname} × {url_count} 个网址（{mode_txt}）")
            return
        want_layout = layout not in ("", "none")
        before = list_top_windows() if want_layout else None
        try:
            proc = subprocess.Popen([path] + cmds[idx])
            self.processes.append((proc.pid, name))
        except Exception as e:
            self._log(f"[错误] 启动失败：{e}")
            self._log(f"已启动「{name}」：{bname} × {url_count} 个网址（{mode_txt}）")
            return
        if want_layout:
            self._schedule_place(proc.pid, name, layout, before)
        if mute:
            self._schedule_mute(proc.pid, name, True)
        gap = 800 if len(cmds) > 1 else 0
        self.root.after(gap, lambda: self._spawn_cmds(path, name, bname, mode_txt,
                                                      cmds, idx + 1, url_count, layout, mute))

    # ---------------- 窗口摆放 ----------------
    def _schedule_place(self, pid, name, layout, before):
        try:
            delay = max(200, int(self.settings.get("layout_delay", 900)))
        except (TypeError, ValueError):
            delay = 900
        token = f"{name}#{pid}"
        for off in (0, 800, 1900, 3500):
            self.root.after(delay + off,
                            lambda: self._place_windows(pid, name, layout, before, token))

    def _place_windows(self, pid, name, layout, before, token):
        if layout in ("", "none"):
            return
        targets = []
        try:
            targets = find_windows_by_pids(descendant_pids(pid))
        except Exception:
            targets = []
        if not targets and before is not None:
            # 兜底：浏览器自举出了新进程时，用「新出现的窗口」判断
            targets = [h for h in find_new_browser_windows(before)
                       if h not in self._claimed_hwnds]
        placed = 0
        for hwnd in targets:
            if place_window(hwnd, layout):
                self._claimed_hwnds.add(hwnd)
                placed += 1
        if placed and token not in self._placed_logged:
            self._placed_logged.add(token)
            self._log(f"  已把「{name}」的 {placed} 个窗口摆放到{LAYOUTS.get(layout, layout)}")

    # ---------------- 静音 ----------------
    def _schedule_mute(self, pid, name, mute=True):
        """网页往往要过一会儿才出声，分几次尝试，保证音频会话出现后立刻静音"""
        try:
            delay = max(500, int(self.settings.get("mute_delay", 3000)))
        except (TypeError, ValueError):
            delay = 3000
        for off in (0, 5000, 12000):
            self.root.after(delay + off, lambda: self._mute_pid(pid, name, mute))

    def _mute_pid(self, pid, name, mute=True):
        try:
            n = AudioMuter.set_mute_pids(descendant_pids(pid), mute)
        except Exception:
            n = 0
        token = f"{name}#{pid}#{mute}"
        if n and token not in self._mute_logged:
            self._mute_logged.add(token)
            self._log(f"  已{'静音' if mute else '取消静音'}「{name}」的 {n} 路声音")

    def _mute_all(self, mute=True):
        if not self.processes:
            messagebox.showinfo("提示", "没有已启动的浏览器进程。")
            return
        total = 0
        for pid, name in list(self.processes):
            try:
                total += AudioMuter.set_mute_pids(descendant_pids(pid), mute)
            except Exception:
                pass
        if total:
            self._log(f"已{'静音' if mute else '取消静音'} {total} 路浏览器声音。")
        else:
            self._log(f"未找到可{'静音' if mute else '取消静音'}的浏览器声音"
                      "（页面还没有播放过声音时会话不存在）。")
            messagebox.showinfo("提示", "没有找到浏览器正在播放的声音。\n"
                                        "网页开始播放声音后再试一次即可。")

    def _close_all(self):
        if not self.processes:
            messagebox.showinfo("提示", "没有已启动的浏览器进程。")
            return
        closed = 0
        for pid, name in list(self.processes):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True)
                closed += 1
            except Exception as e:
                self._log(f"[错误] 关闭进程 {pid} 失败：{e}")
        self.processes.clear()
        self._log(f"已关闭 {closed} 个进程。")
        self.status_var.set("已关闭全部")

    # ---------------- 日志 ----------------
    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _on_close(self):
        self._on_settings_change()   # 手动输入（如延迟）也要落盘
        st = self.settings
        if st.get("dpi_enabled") and st.get("dpi_restore_on_exit", True):
            self._restore_dpi()
        elif self.config.get("dpi_state"):
            # 明确要求保留调整后的缩放：丢掉状态，避免下次启动被"还原"
            self.config.pop("dpi_state", None)
        self.save_config()
        self.root.destroy()


def main():
    root = tk.Tk()
    # 让 ttk 使用较新的主题
    try:
        from tkinter import ttk
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
