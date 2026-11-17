"""Windows 原生适配器。句柄仅存本文件内部，不进入公开事件或反馈。"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
from .model import Rect, Surface
from .capture import png


class WindowsDesktop:
    capabilities = dict(windows=True, taskbar="work-area", icons=False,
                        screenshot=True, monitors="virtual-desktop", occlusion="full-window-cover")

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("原生模式仅支持 Windows；可继续使用模拟体验场")
        self.user = C.WinDLL("user32", use_last_error=True)
        self.gdi = C.WinDLL("gdi32", use_last_error=True)
        self.dwm = C.WinDLL("dwmapi")
        self._ids = {}
        self._counter = 0
        self._declare()

    def _declare(self):
        u, g = self.user, self.gdi
        self.callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        u.EnumWindows.argtypes = [self.callback_type, W.LPARAM]
        u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        u.IsWindowVisible.argtypes = [W.HWND]
        u.IsIconic.argtypes = [W.HWND]
        u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        u.GetAncestor.argtypes = [W.HWND, W.UINT]
        u.GetAncestor.restype = W.HWND
        u.GetForegroundWindow.restype = W.HWND
        self.get_style = u.GetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else u.GetWindowLongW
        self.set_style = u.SetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else u.SetWindowLongW
        self.get_style.argtypes = [W.HWND, C.c_int]
        self.get_style.restype = C.c_ssize_t
        self.set_style.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
        self.set_style.restype = C.c_ssize_t
        u.GetDC.argtypes = [W.HWND]
        u.GetDC.restype = W.HDC
        u.ReleaseDC.argtypes = [W.HWND, W.HDC]
        u.SetWindowRgn.argtypes = [W.HWND, W.HANDLE, W.BOOL]
        u.SetWindowDisplayAffinity.argtypes = [W.HWND, W.DWORD]
        u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        g.CreateCompatibleDC.argtypes = [W.HDC]
        g.CreateCompatibleDC.restype = W.HDC
        g.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
        g.CreateCompatibleBitmap.restype = W.HBITMAP
        g.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
        g.SelectObject.restype = W.HGDIOBJ
        g.DeleteObject.argtypes = [W.HGDIOBJ]
        g.DeleteDC.argtypes = [W.HDC]
        g.BitBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD]
        g.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
        g.CreateRectRgn.argtypes = [C.c_int, C.c_int, C.c_int, C.c_int]
        g.CreateRectRgn.restype = W.HANDLE
        g.CombineRgn.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE, C.c_int]

    @staticmethod
    def enable_dpi():
        if os.name == "nt":
            try:
                # 必须在创建 Tk 窗口前设置；坐标全部为虚拟桌面物理像素。
                C.windll.user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))
            except (AttributeError, OSError):
                C.windll.user32.SetProcessDPIAware()

    def bounds(self):
        u = self.user
        return Rect(*(u.GetSystemMetrics(i) for i in (76, 77, 78, 79)))

    def work_area(self):
        rect = W.RECT()
        if not self.user.SystemParametersInfoW(48, 0, C.byref(rect), 0):
            raise C.WinError(C.get_last_error())
        return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def surfaces(self, registered=None):
        registered = registered or {}
        result = []
        covering = []
        live = set()
        foreground = self.user.GetForegroundWindow()
        @self.callback_type
        def visit(hwnd, _):
            try:
                if not self.user.IsWindowVisible(hwnd):
                    return True
                pid = W.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
                if pid.value == os.getpid() and hwnd not in registered:
                    return True
                title = C.create_unicode_buffer(256)
                self.user.GetWindowTextW(hwnd, title, 256)
                if not title.value or title.value == "Program Manager":
                    return True
                cloaked = W.DWORD()
                self.dwm.DwmGetWindowAttribute(W.HWND(hwnd), 14, C.byref(cloaked), C.sizeof(cloaked))
                if cloaked.value:
                    return True
                r = W.RECT()
                if not self.user.GetWindowRect(hwnd, C.byref(r)):
                    return True
                rect = Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
                if rect.width <= 0 or rect.height <= 0:
                    return True
                live.add(hwnd)
                if hwnd in registered:
                    self._ids[hwnd] = registered[hwnd]
                elif hwnd not in self._ids:
                    self._counter += 1
                    self._ids[hwnd] = f"native-window-{self._counter}"
                minimized = bool(self.user.IsIconic(hwnd))
                result.append(Surface(self._ids[hwnd], rect, minimized,
                                      any(c.contains(rect) for c in covering), title.value, "windows", hwnd == foreground))
                if not minimized:
                    covering.append(rect)
            except Exception:
                # 窗口可能在枚举期间消失；下一轮将回收旧对象。
                pass
            return True
        if not self.user.EnumWindows(visit, 0):
            raise C.WinError(C.get_last_error())
        self._ids = {handle: key for handle, key in self._ids.items() if handle in live}
        return result

    def configure_overlay(self, widget):
        hwnd = self.user.GetAncestor(widget.winfo_id(), 2)
        # 工具窗口不占任务栏，不在显示/动画刷新时抢走当前软件的输入焦点。
        style = self.get_style(hwnd, -20)
        self.set_style(hwnd, -20, (style | 0x08000000 | 0x80) & ~0x40000)

    def shape(self, widget, photo):
        """按 PNG 不透明像素构造窗口区域；透明区域根本不在窗口命中区域中。"""
        hwnd = self.user.GetAncestor(widget.winfo_id(), 2)
        region = self.gdi.CreateRectRgn(0, 0, 0, 0)
        try:
            for y in range(photo.height()):
                start = None
                for x in range(photo.width() + 1):
                    opaque = x < photo.width() and not photo.transparency_get(x, y)
                    if opaque and start is None:
                        start = x
                    if not opaque and start is not None:
                        row = self.gdi.CreateRectRgn(start, y, x, y + 1)
                        self.gdi.CombineRgn(region, region, row, 2)
                        self.gdi.DeleteObject(row)
                        start = None
            if not self.user.SetWindowRgn(hwnd, region, True):
                raise C.WinError(C.get_last_error())
            region = None  # 系统接管 region 的所有权。
            self.user.SetWindowDisplayAffinity(hwnd, 0x11)
        finally:
            if region:
                self.gdi.DeleteObject(region)

    def screenshot(self, rect):
        """调用方先隐藏自身窗口并 DwmFlush；不记录或保存画面。"""
        screen = self.user.GetDC(None)
        if not screen:
            raise C.WinError(C.get_last_error())
        try:
            return self._capture_dc(screen, rect)
        finally:
            self.user.ReleaseDC(None, screen)

    def _capture_dc(self, screen, rect):
        """共享 GDI 编码路径；原生测试注入合成内存 DC，避免采集个人桌面。"""
        class BitmapInfoHeader(C.Structure):
            _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                        ("planes", W.WORD), ("bitcount", W.WORD), ("compression", W.DWORD),
                        ("sizeimage", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                        ("used", W.DWORD), ("important", W.DWORD)]
        memory = bitmap = old = None
        try:
            memory = self.gdi.CreateCompatibleDC(screen)
            bitmap = self.gdi.CreateCompatibleBitmap(screen, rect.width, rect.height)
            if not screen or not memory or not bitmap:
                raise C.WinError(C.get_last_error())
            old = self.gdi.SelectObject(memory, bitmap)
            if not self.gdi.BitBlt(memory, 0, 0, rect.width, rect.height, screen, rect.x, rect.y, 0x00CC0020):
                raise C.WinError(C.get_last_error())
            # GetDIBits 要求目标位图没有被选入任何 DC。
            self.gdi.SelectObject(memory, old)
            old = None
            info = BitmapInfoHeader(C.sizeof(BitmapInfoHeader), rect.width, -rect.height, 1, 32, 0, 0, 0, 0, 0, 0)
            data = C.create_string_buffer(rect.width * rect.height * 4)
            if self.gdi.GetDIBits(memory, bitmap, 0, rect.height, data, C.byref(info), 0) != rect.height:
                raise C.WinError(C.get_last_error())
            bgra = data.raw
            rgb = bytearray(rect.width * rect.height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
            return png(rect.width, rect.height, rgb)
        finally:
            if old:
                self.gdi.SelectObject(memory, old)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)

    def dispose(self):
        self._ids.clear()
