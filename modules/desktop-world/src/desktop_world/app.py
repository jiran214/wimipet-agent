"""桌面体验场；模拟和 Windows 原生宿主共享 World。"""
from __future__ import annotations

import argparse
import base64
import json
import platform
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from . import __version__
from .model import World, Rect, Surface, Package
from .capture import CaptureGate, png
from .windows import WindowsDesktop

ROOT = Path(__file__).resolve().parents[2]


class Playground:
    def __init__(self, root, native=False):
        self.root = root
        self.root.title("A · 桌面体验场 0.1.0")
        self.root.geometry("1260x850")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.native = None
        self.world = None
        self.gate = None
        self.photos = {}
        self.overlays = {}
        self.preview_windows = []
        self.test_window = None
        self.next_id = 0
        self.after_id = None
        self.steps = []
        self.results = []
        self.replay = []
        self.last_poll = 0
        self.last_time = time.monotonic()
        self.closed = False
        self.drag = None
        self.mode = tk.StringVar(value="native" if native else "simulation")
        self.pet_choice = tk.StringVar()
        self.surface_choice = tk.StringVar()
        self.action_choice = tk.StringVar(value="wave")
        self.queue = tk.BooleanVar(value=False)
        self.pause = tk.BooleanVar(value=False)
        self.exclude = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪")
        self.capture_vars = [tk.StringVar(value=str(v)) for v in (0, 0, 420, 260)]
        self._build()
        self.reset()
        self._loop()

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(7, 5))
        header = ttk.Frame(self.root, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="桌面体验场", font=("Microsoft YaHei UI", 19, "bold")).pack(side="left")
        ttk.Label(header, text="  A / 身体与环境 · 无 AI · 本地固定样例").pack(side="left")
        for value, title in (("simulation", "模拟桌面"), ("native", "Windows 原生")):
            ttk.Radiobutton(header, text=title, variable=self.mode, value=value, command=self.reset).pack(side="right")
        body = ttk.Frame(self.root, padding=(12, 0))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.banner = ttk.Label(left, text="", padding=6, wraplength=850)
        self.banner.pack(fill="x")
        self.canvas = tk.Canvas(left, width=900, height=620, bg="#e6edf0", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._canvas_down)
        self.canvas.bind("<B1-Motion>", self._canvas_move)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_up)
        self.state_label = ttk.Label(left, text="", padding=7, wraplength=850)
        self.state_label.pack(fill="x")
        right = ttk.Frame(body, padding=(12, 0, 0, 0), width=300)
        right.pack(side="right", fill="y")
        self._label(right, "角色 / 身体")
        self.pet_box = ttk.Combobox(right, textvariable=self.pet_choice, state="readonly", width=30)
        self.pet_box.pack(fill="x", pady=3)
        self._row(right, (("添加样例", self.add_sample), ("载入角色包", self.load_package), ("移除", self.remove_pet)))
        ttk.Label(right, text="动作（wave 为有限动作，其余循环）").pack(anchor="w", pady=(6, 0))
        ttk.Combobox(right, textvariable=self.action_choice, values=("wave", "idle", "lying", "hanging", "clinging", "unknown"),
                     state="readonly").pack(fill="x")
        ttk.Checkbutton(right, text="当前动作后排队（最多 8 条）", variable=self.queue).pack(anchor="w")
        self._row(right, (("执行动作", self.action), ("释放 / 下落", self.release), ("任务栏停靠", self.dock)))
        self._label(right, "依附窗口")
        self.surface_box = ttk.Combobox(right, textvariable=self.surface_choice, state="readonly", width=30)
        self.surface_box.pack(fill="x", pady=3)
        self._row(right, (("趴在顶部", lambda: self.attach("lying")), ("挂在顶部", lambda: self.attach("hanging")),
                          ("贴右侧", lambda: self.attach("clinging"))))
        self._row(right, (("窗口右移", lambda: self.change_window("move")), ("缩放", lambda: self.change_window("resize")),
                          ("遮挡", lambda: self.change_window("cover"))))
        self._row(right, (("最小化 / 恢复", lambda: self.change_window("minimize")),
                          ("关闭样例窗口", lambda: self.change_window("close"))))
        ttk.Button(right, text="新建 / 恢复样例窗口", command=self.restore_window).pack(fill="x", pady=3)
        self._label(right, "按范围采集（默认内存预览）")
        coords = ttk.Frame(right)
        coords.pack(fill="x")
        for name, var in zip(("X", "Y", "宽", "高"), self.capture_vars):
            cell = ttk.Frame(coords)
            cell.pack(side="left", expand=True, fill="x")
            ttk.Label(cell, text=name).pack()
            ttk.Entry(cell, textvariable=var, width=6).pack()
        self._row(right, (("授权该范围", self.authorize), ("采集预览", self.capture)))
        ttk.Checkbutton(right, text="暂停采集", variable=self.pause, command=self.capture_policy).pack(anchor="w")
        ttk.Checkbutton(right, text="排除授权区左上 80×80（相交即拒绝）", variable=self.exclude,
                        command=self.capture_policy).pack(anchor="w")
        self._label(right, "回放 / 反馈")
        self._row(right, (("恢复初始场景", self.reset), ("固定事件回放", self.start_replay)))
        ttk.Label(right, text="预期 / 实际 / 异常（手工填写）").pack(anchor="w")
        self.notes = tk.Text(right, height=3, width=34)
        self.notes.pack(fill="x")
        ttk.Button(right, text="导出复现记录（不含截图 / 窗口标题）", command=self.export).pack(fill="x", pady=4)
        self.log = tk.Text(right, width=37, height=8, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        footer = ttk.Frame(self.root, padding=8)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status, wraplength=1200).pack(anchor="w")
        ttk.Label(footer, text="拖动角色可打断动作。原生模式可直接拖动桌面角色；模拟场仅在画布内。关闭此面板退出并释放所有角色窗口。",
                  foreground="#526574").pack(anchor="w")

    @staticmethod
    def _label(parent, text):
        ttk.Label(parent, text=text, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(10, 3))

    @staticmethod
    def _row(parent, buttons):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        for title, callback in buttons:
            ttk.Button(row, text=title, command=callback).pack(side="left", expand=True, fill="x", padx=1)

    def record(self, operation, **data):
        self.steps.append(dict(atMs=round(self.world.time_ms if self.world else 0), operation=operation, **data))
        del self.steps[:-1000]

    def tell(self, text):
        self.status.set(text)
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 300:
            self.log.delete("1.0", f"{lines - 300}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def event(self, event):
        if event["type"] in ("action-result", "attachment-rejected", "released"):
            self.results.append(event)
            del self.results[:-300]
            self.tell(f'{event.get("petId", "")} {event.get("requestId", "")} {event.get("status", event["type"])} {event.get("reason", "")}')

    def reset(self):
        self.replay.clear()
        self._destroy_overlays()
        for preview in self.preview_windows:
            if preview.winfo_exists():
                preview.destroy()
        self.preview_windows.clear()
        self._close_test()
        if self.world:
            self.world.dispose()
        if self.gate:
            self.gate.dispose()
        if self.native:
            self.native.dispose()
        self.native = None
        try:
            if self.mode.get() == "native":
                self.native = WindowsDesktop()
                bounds = self.native.work_area()
            else:
                bounds = Rect(0, 0, 900, 600)
        except Exception as exc:
            self.mode.set("simulation")
            self.tell(f"原生模式启动失败：{exc}；已恢复模拟模式")
            bounds = Rect(0, 0, 900, 600)
        self.world = World(bounds, "windows" if self.native else "simulation")
        self.world.subscribe(self.event)
        self.gate = CaptureGate(self.native.bounds() if self.native else bounds)
        self.pause.set(False)
        self.exclude.set(False)
        for var, value in zip(self.capture_vars, (bounds.x, bounds.y, 420, 260)):
            var.set(str(value))
        self.banner.configure(text=("真实 Windows 模式 · 角色在桌面独立显示 · 窗口结构每 100ms 轮询 · 图标读取未接通；主屏工作区停靠，多屏 / 混合 DPI 待验收。"
                                    if self.native else "模拟模式 · 固定桌面与本地 PNG 序列帧 · 截图为合成样例，未读取真实桌面。"))
        self.photos.clear()
        self.next_id = 0
        for _ in range(3):
            self.add_sample(record=False)
        self.restore_window()
        self.record("reset", mode=self.mode.get(), scenario="three-pets-v1")
        self.last_time = time.monotonic()
        self.tell("初始场景已恢复：3 只样例角色；没有删除或改写用户角色包")

    def _cache(self, package):
        # Tk 实际解码全部帧；损坏 PNG 在载入阶段隔离，而非播放时崩溃。
        loaded = {}
        for action in package.actions.values():
            for frame in action.frames:
                if frame.path not in self.photos:
                    loaded[frame.path] = tk.PhotoImage(file=str(frame.path), master=self.root)
        self.photos.update(loaded)

    def add_sample(self, record=True):
        path = ROOT / "fixtures" / ("moss", "sky", "peach")[self.next_id % 3] / "character.json"
        self._add_package(path)
        if record:
            self.record("add-sample")

    def _add_package(self, path):
        try:
            package = Package.load(path)
            self._cache(package)
            self.next_id += 1
            pet_id = f"pet-{self.next_id}"
            b = self.world.bounds
            self.world.add(pet_id, package, b.x + 100 + (len(self.world.pets) * 140) % max(140, int(b.width - 180)), b.bottom)
            self.pet_choice.set(pet_id)
            self._refresh_choices()
            return pet_id
        except Exception as exc:
            self.tell(f"载入失败（其他角色继续运行）：{exc}")
            return None

    def load_package(self):
        path = filedialog.askopenfilename(title="选择 A-display/1 character.json", filetypes=[("角色包清单", "*.json")])
        if path:
            pet_id = self._add_package(Path(path))
            self.record("load-package", success=pet_id is not None)  # 不导出用户路径。

    def _pet(self):
        pet = self.pet_choice.get()
        if pet not in self.world.pets:
            self.tell("请先选择或添加角色")
            return None
        return pet

    def remove_pet(self):
        if pet := self._pet():
            self.world.remove(pet)
            self.record("remove", petId=pet)
            self._refresh_choices()
            self._prune_cache()

    def _prune_cache(self):
        used = {frame.path for pet in self.world.pets.values() for action in pet.package.actions.values() for frame in action.frames}
        self.photos = {path: photo for path, photo in self.photos.items() if path in used}

    def action(self):
        if pet := self._pet():
            request = f"ui-{self.world.sequence + 1}"
            self.world.command(pet, self.action_choice.get(), request, self.queue.get())
            self.record("action", petId=pet, action=self.action_choice.get(), requestId=request, enqueue=self.queue.get())

    def attach(self, posture):
        if pet := self._pet():
            surface_id = self.surface_choice.get().split(" | ")[0]
            self.world.attach(pet, surface_id, posture)
            self.record("attach", petId=pet, surfaceId=surface_id, posture=posture)

    def release(self):
        if pet := self._pet():
            self.world.release(pet)
            self.record("release", petId=pet)

    def dock(self):
        if pet := self._pet():
            self.world.dock(pet)
            self.record("dock", petId=pet)

    def restore_window(self):
        if self.native:
            if self.test_window and self.test_window.winfo_exists():
                self.test_window.deiconify()
            else:
                self.test_window = tk.Toplevel(self.root)
                self.test_window.title("A 模块 · 原生测试窗口")
                self.test_window.geometry("460x230+420+320")
                ttk.Label(self.test_window, text="这是本模块自己的真实 Windows 窗口。\n可以拖动标题栏、拉伸边缘、最小化或关闭。\n角色依附只更改角色，不会操作您的其他软件。", padding=24).pack(fill="both", expand=True)
                self.test_window.protocol("WM_DELETE_WINDOW", self._close_test)
                self.root.update_idletasks()
            self._poll()
        else:
            self.world.set_surface(Surface("sample-window", Rect(260, 260, 420, 210), title="固定样例窗口 v1"))
        self.surface_choice.set("sample-window | 固定样例窗口 v1")
        self._refresh_choices()
        self.record("restore-window")

    def _close_test(self):
        if self.test_window:
            self.test_window.destroy()
            self.test_window = None
        if self.world and not self.world.disposed:
            self.world.remove_surface("sample-window")

    def change_window(self, kind):
        # 控制按钮只操作体验场自己的窗口，不会代用户移动其他软件。
        surface = self.world.surfaces.get("sample-window")
        if not surface:
            self.tell("样例窗口已关闭，请先恢复样例窗口")
            return
        if self.native:
            window = self.test_window
            if kind == "move":
                window.geometry(f"+{window.winfo_x() + 45}+{window.winfo_y() + 15}")
            elif kind == "resize":
                width = 360 if window.winfo_width() > 400 else 500
                window.geometry(f"{width}x250")
            elif kind == "minimize":
                window.deiconify() if window.state() == "iconic" else window.iconify()
            elif kind == "close":
                self._close_test()
            elif kind == "cover":
                self.tell("原生遮挡测试：请手工用其他窗口完全覆盖目标；部分遮挡尚未精确计算")
            self.root.update_idletasks()
            self._poll()
        else:
            r = surface.rect
            if kind == "move":
                surface.rect = Rect((r.x + 65) % 460 + 40, r.y, r.width, r.height)
            elif kind == "resize":
                surface.rect = Rect(r.x, r.y, 280 if r.width > 300 else 420, 180 if r.height > 190 else 210)
            elif kind == "cover":
                surface.occluded = not surface.occluded
            elif kind == "minimize":
                surface.minimized = not surface.minimized
            elif kind == "close":
                self.world.remove_surface(surface.id)
                self._refresh_choices()
                self.record("window", change=kind)
                return
            self.world.set_surface(surface)
        self.record("window", change=kind)

    def _refresh_choices(self):
        self.pet_box["values"] = tuple(self.world.pets)
        if self.pet_choice.get() not in self.world.pets:
            self.pet_choice.set(next(iter(self.world.pets), ""))
        values = [f"{s.id} | {s.title[:26]}" for s in self.world.surfaces.values()]
        self.surface_box["values"] = values
        selected = self.surface_choice.get().split(" | ")[0]
        self.surface_choice.set(next((v for v in values if v.split(" | ")[0] == selected), next(iter(values), "")))

    def _poll(self):
        if not self.native:
            return
        bounds = self.native.work_area()
        if bounds != self.world.bounds:
            self.world.set_bounds(bounds)
        registered = {}
        if self.test_window and self.test_window.winfo_exists():
            hwnd = self.native.user.GetAncestor(self.test_window.winfo_id(), 2)
            registered[hwnd] = "sample-window"
        surfaces = self.native.surfaces(registered)
        for key in set(self.world.surfaces) - {s.id for s in surfaces}:
            self.world.remove_surface(key)
        for surface in surfaces:
            if self.world.surfaces.get(surface.id) != surface:
                self.world.set_surface(surface)
        self._refresh_choices()

    def _canvas_down(self, event):
        if self.native:
            return
        for pet in reversed(list(self.world.pets.values())):
            x, y = pet.origin
            px, py = int(event.x - x), int(event.y - y)
            photo = self.photos[pet.frame.path]
            if pet.visible and 0 <= px < photo.width() and 0 <= py < photo.height() and not photo.transparency_get(px, py):
                self._drag_begin(pet.id, event.x, event.y)
                return

    def _drag_begin(self, pet_id, x, y):
        self.pet_choice.set(pet_id)
        pet = self.world.pets[pet_id]
        self.drag = (pet_id, pet.x - x, pet.y - y)
        self.world.emit("click", petId=pet_id, x=x, y=y)
        self.world.drag_start(pet_id)
        self.record("drag-start", petId=pet_id, x=x, y=y)

    def _drag_move(self, x, y):
        if self.drag:
            pet_id, dx, dy = self.drag
            self.world.drag_move(pet_id, x + dx, y + dy)

    def _drag_end(self):
        if self.drag:
            pet_id = self.drag[0]
            pet = self.world.pets[pet_id]
            self.record("drag-end", petId=pet_id, x=pet.x, y=pet.y)
            self.world.drag_end(pet_id)
            self.drag = None

    def _canvas_move(self, event):
        self._drag_move(event.x, event.y)

    def _canvas_up(self, event):
        self._drag_end()

    def _render(self):
        c = self.canvas
        c.delete("all")
        if self.native:
            c.create_text(35, 40, anchor="nw", font=("Microsoft YaHei UI", 16), text="角色正在真实桌面显示", fill="#203e47")
            c.create_text(35, 95, anchor="nw", width=740, font=("Microsoft YaHei UI", 11),
                          text="使用右侧控制面板，或直接拖动桌面上的角色。\n选择样例窗口或其他已枚举窗口进行依附。\n控制面板的移动 / 缩放 / 关闭按钮仅操作自己的样例窗口。\n\n原生能力：窗口枚举、工作区、PNG 动画、按像素命中区域、GDI 范围截图。\n待验证：多屏 / 混合 DPI、部分遮挡、图标结构、复杂系统窗口层级。\n无模型调用，无聊天或语义理解。", fill="#526574")
            self._render_overlays()
        else:
            c.create_text(24, 24, text="固定桌面 / THREE FRIENDS", anchor="nw", fill="#536b77", font=("Consolas", 12))
            c.create_rectangle(0, 600, max(900, c.winfo_width()), max(620, c.winfo_height()), fill="#c2d2d8", outline="")
            c.create_text(22, 610, text="模拟任务栏 · 角色脚底接触线 y=600", anchor="nw", fill="#344e5a")
            for surface in self.world.surfaces.values():
                if surface.minimized:
                    c.create_text(280, 580, text="样例窗口已最小化", fill="#536b77")
                    continue
                r = surface.rect
                c.create_rectangle(r.x, r.y, r.right, r.bottom, fill="#f8fafb", outline="#97aeb9", width=2)
                c.create_rectangle(r.x, r.y, r.right, r.y + 32, fill="#cbdde4", outline="")
                c.create_text(r.x + 12, r.y + 16, text="固定样例窗口 v1", anchor="w", fill="#203e47")
                c.create_text(r.x + 18, r.y + 65, text="趴 / 挂 / 贴 · 移动 / 缩放 / 最小化 / 关闭", anchor="w", fill="#536b77")
                if surface.occluded:
                    c.create_rectangle(r.x - 12, r.y - 16, r.right + 12, r.bottom + 12, fill="#8899aa", outline="")
                    c.create_text(r.x + 20, r.y + 40, text="模拟上层窗口 · 依附角色隐藏", anchor="w", fill="white")
            for pet in list(self.world.pets.values()):
                try:
                    if pet.visible:
                        c.create_image(*pet.origin, image=self.photos[pet.frame.path], anchor="nw")
                        c.create_text(pet.x, pet.origin[1] - 12, text=pet.id, fill="#536b77", font=("Consolas", 9))
                except Exception as exc:
                    self._render_failed(pet, exc)
        pet = self.world.pets.get(self.pet_choice.get())
        if pet:
            self.state_label.configure(text=f"{pet.id} · {pet.package.name} · {pet.state} · {pet.action}[{pet.frame_index}] · 接触点 ({pet.x:.0f}, {pet.y:.0f}) · 队列 {len(pet.queue)} · 可见 {pet.visible}")
        else:
            self.state_label.configure(text="当前没有角色")

    def _render_overlays(self):
        for key in set(self.overlays) - set(self.world.pets):
            self.overlays.pop(key)[0].destroy()
        for pet in list(self.world.pets.values()):
            try:
                self._render_overlay(pet)
            except Exception as exc:
                self._render_failed(pet, exc)

    def _render_overlay(self, pet):
        if pet.id not in self.overlays:
            window = tk.Toplevel(self.root)
            window.withdraw()
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.configure(bg="#ff00ff")
            window.attributes("-transparentcolor", "#ff00ff")
            label = tk.Label(window, bd=0, highlightthickness=0, bg="#ff00ff")
            label.pack()
            label.bind("<ButtonPress-1>", lambda e, key=pet.id: self._native_down(key, e))
            label.bind("<B1-Motion>", lambda e: self._drag_move(e.x_root, e.y_root))
            label.bind("<ButtonRelease-1>", lambda e: self._native_up(e))
            self.overlays[pet.id] = [window, label, None]
            window.update_idletasks()
            self.native.configure_overlay(window)
        overlay = self.overlays[pet.id]
        window, label, old_path = overlay
        photo = self.photos[pet.frame.path]
        x, y = pet.origin
        # Tk 的 -x 几何偏移代表相对右缘，使用 Win32 SetWindowPos 支持负坐标。
        window.geometry(f"{photo.width()}x{photo.height()}")
        if old_path != pet.frame.path:
            label.configure(image=photo)
            window.update_idletasks()
            self.native.shape(window, photo)
            overlay[2] = pet.frame.path
        if pet.visible:
            if window.state() == "withdrawn":
                window.deiconify()
            hwnd = self.native.user.GetAncestor(window.winfo_id(), 2)
            if not self.native.user.SetWindowPos(W_HWND(hwnd), W_HWND(-1), round(x), round(y), photo.width(), photo.height(), 0x0010):
                raise OSError("原生窗口定位失败")
        else:
            window.withdraw()

    def _render_failed(self, pet, exc):
        self.world.fail_action(pet.id, pet.request_id, "宿主渲染失败")
        self.world.remove(pet.id)
        if pet.id in self.overlays:
            self.overlays.pop(pet.id)[0].destroy()
        if self.drag and self.drag[0] == pet.id:
            self.drag = None
        self.record("render-error", petId=pet.id, errorType=type(exc).__name__)
        self.tell(f"角色 {pet.id} 显示失败，已隔离该实例：{type(exc).__name__}；可重新载入或重置")
        self._refresh_choices()
        self._prune_cache()

    def _native_down(self, pet_id, event):
        self._drag_begin(pet_id, event.x_root, event.y_root)
        event.widget.grab_set()

    def _native_up(self, event):
        event.widget.grab_release()
        self._drag_end()

    def _destroy_overlays(self):
        self.drag = None
        for window, _, _ in self.overlays.values():
            window.destroy()
        self.overlays.clear()

    def _rect(self):
        return Rect(*(int(var.get()) for var in self.capture_vars))

    def authorize(self):
        try:
            rect = self._rect()
            bounds = self.native.bounds() if self.native else self.world.bounds
            if rect.width <= 0 or rect.height <= 0 or not bounds.contains(rect):
                raise ValueError("授权范围必须在桌面内且宽高为正")
            self.gate.configure(allowed=rect)
            self.capture_policy()
            self.record("authorize", rect=vars(rect))
            self.tell(f"已授权 {vars(rect)}；之后修改输入不会自动扩大授权")
        except Exception as exc:
            self.tell(f"授权失败：{exc}")

    def capture_policy(self):
        r = self.gate.allowed
        self.gate.configure(paused=self.pause.get(), excluded=(Rect(r.x, r.y, min(80, r.width), min(80, r.height)),) if self.exclude.get() else ())
        self.record("capture-policy", paused=self.pause.get(), excluded=self.exclude.get())
        self.tell(f"采集策略 epoch={self.gate.epoch} · 暂停={self.pause.get()} · 左上排除={self.exclude.get()}")

    def _sample_capture(self, rect):
        # 明确的模拟画面：固定桌面和当前模拟窗口，不包含角色和面板。
        rgb = bytearray(bytes((230, 237, 240)) * (rect.width * rect.height))
        for surface in self.world.surfaces.values():
            if surface.minimized:
                continue
            r = surface.rect
            for y in range(max(rect.y, int(r.y)), min(rect.bottom, int(r.bottom))):
                left, right = max(rect.x, int(r.x)), min(rect.right, int(r.right))
                if right > left:
                    color = (136, 153, 170) if surface.occluded else ((203, 221, 228) if y < r.y + 32 else (248, 250, 251))
                    start = ((y - rect.y) * rect.width + left - rect.x) * 3
                    rgb[start:start + (right - left) * 3] = bytes(color) * (right - left)
        return png(rect.width, rect.height, rgb)

    def _native_capture(self, rect):
        if not self.native.bounds().contains(rect):
            raise ValueError("屏幕范围已经改变，请重新授权有效范围")
        visible = [self.root] + [value[0] for value in self.overlays.values() if value[0].state() != "withdrawn"]
        visible += [w for w in self.preview_windows if w.winfo_exists() and w.state() != "withdrawn"]
        if self.test_window and self.test_window.state() != "iconic":
            visible.append(self.test_window)
        try:
            for window in visible:
                window.withdraw()
            self.root.update_idletasks()
            self.native.dwm.DwmFlush()
            return self.native.screenshot(rect)
        finally:
            for window in visible:
                if window.winfo_exists():
                    window.deiconify()

    def capture(self):
        try:
            rect = self._rect()
            request = f"capture-{len(self.steps) + 1}"
            result = self.gate.capture(request, rect, self._native_capture if self.native else self._sample_capture)
            self.record("capture", rect=vars(rect), status=result["status"], epoch=result["epoch"])
            if result["stale"]:
                self.tell("在途采集结果已过期，画面已丢弃")
                return
            photo = tk.PhotoImage(data=base64.b64encode(result["image"]), master=self.root)
            window = tk.Toplevel(self.root)
            window.title("真实桌面截图 · 仅内存" if self.native else "合成样例截图 · 未采集真实桌面")
            ttk.Label(window, text=f"范围 {vars(rect)} · 未保存文件 · 关闭即释放预览引用", padding=8).pack()
            label = ttk.Label(window, image=photo)
            label.image = photo
            label.pack()
            self.preview_windows.append(window)
            def close_preview():
                self.preview_windows.remove(window)
                window.destroy()
            window.protocol("WM_DELETE_WINDOW", close_preview)
            self.tell("采集完成；画面仅存在内存，反馈记录不含画面")
        except Exception as exc:
            self.record("capture-error", reason=str(exc))
            self.tell(f"采集拒绝 / 失败：{exc}")

    def start_replay(self):
        # 只在模拟模式重放固定输入；不代替用户操作真实第三方窗口。
        self.mode.set("simulation")
        self.reset()
        pet = next(iter(self.world.pets))
        self.pet_choice.set(pet)
        start = self.world.time_ms
        self.replay = [(start + delay, operation) for delay, operation in (
            (300, lambda: self.attach("hanging")), (1100, lambda: self.change_window("move")),
            (1900, lambda: self.change_window("resize")), (2700, lambda: self.change_window("cover")),
            (3400, lambda: self.change_window("cover")), (4100, lambda: self.change_window("minimize")),
            (5200, self.restore_window), (5800, lambda: self.attach("lying")),
            (6800, lambda: self.change_window("close")), (8000, lambda: self.tell("固定回放完成：依附→移动→缩放→遮挡→最小化→恢复→关闭→下落停靠")))]
        self.record("replay", fixture="window-events-v1")

    def export(self):
        path = filedialog.asksaveasfilename(title="保存复现记录（不包含画面）", defaultextension=".json", initialfile="desktop-world-feedback.json")
        if not path:
            return
        data = dict(module="desktop-world", version=__version__, protocol="A-world/1", scenario="three-pets-v1",
                    environment=dict(system=platform.system(), release=platform.release(), python=platform.python_version(),
                                     tk=self.root.tk.call("info", "patchlevel"), mode=self.mode.get(),
                                     bounds=vars(self.world.bounds), capabilities=self.native.capabilities if self.native else {"simulated": True}),
                    steps=self.steps, results=self.results, expected="见操作说明；请在 notes 中记录实际观察", notes=self.notes.get("1.0", "end").strip(),
                    states=[self.world.snapshot(key) for key in self.world.pets], events=self.world.events)
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.tell("复现记录已导出；未自动收集截图、密钥或窗口标题。手填文字由您控制。")
        except OSError as exc:
            self.tell(f"导出失败：{exc}")

    def _loop(self):
        if self.closed:
            return
        now = time.monotonic()
        delta = min((now - self.last_time) * 1000, 250)
        self.last_time = now
        try:
            self.world.tick(delta)
            if self.native and now - self.last_poll >= 0.1:
                self._poll()
                self.last_poll = now
            while self.replay and self.world.time_ms >= self.replay[0][0]:
                _, operation = self.replay.pop(0)
                operation()
            self._render()
        except Exception as exc:
            self.tell(f"宿主错误：{exc}")
            self.replay.clear()
        self.after_id = self.root.after(16, self._loop)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.replay.clear()
        self._destroy_overlays()
        self._close_test()
        self.gate.dispose()
        self.world.dispose()
        if self.native:
            self.native.dispose()
        self.photos.clear()
        self.preview_windows.clear()
        self.root.destroy()


# 显式指针类型，避免 64 位 Windows 的 HWND 被 ctypes 默认 int 截断。
from ctypes.wintypes import HWND as W_HWND


def main():
    parser = argparse.ArgumentParser(description="A 模块桌面体验场；默认模拟，无密钥、无网络")
    parser.add_argument("--native", action="store_true", help="直接进入 Windows 原生模式")
    args = parser.parse_args()
    WindowsDesktop.enable_dpi()
    root = tk.Tk()
    Playground(root, native=args.native)
    root.mainloop()


if __name__ == "__main__":
    main()
