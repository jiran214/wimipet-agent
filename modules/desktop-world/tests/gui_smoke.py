"""显式启用的 GUI/原生集成检查；不截图、不注入系统输入、不操作其他应用。

python modules/desktop-world/tests/gui_smoke.py
python modules/desktop-world/tests/gui_smoke.py --win32
"""
import argparse
import base64
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_world.app import Playground
from desktop_world.model import Package
from desktop_world.windows import WindowsDesktop


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--win32", action="store_true")
    args = parser.parse_args()
    WindowsDesktop.enable_dpi()
    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *exc: errors.append(str(exc))
    app = Playground(root)
    try:
        assert len(app.world.pets) == 3
        assert sum(len(a.frames) for p in app.world.pets.values() for a in p.package.actions.values()) == 63
        pet_id = next(iter(app.world.pets))
        app.pet_choice.set(pet_id)
        app.action_choice.set("wave")
        app.action()
        pet = app.world.pets[pet_id]
        assert pet.action == "wave"
        x, y = pet.x, pet.y - 20
        app._canvas_down(SimpleNamespace(x=x, y=y))
        assert pet.state == "dragging"
        app._canvas_move(SimpleNamespace(x=x + 200, y=y - 160))
        app._canvas_up(None)
        assert any(e.get("status") == "interrupted" for e in app.results)
        app.world.tick(2000)
        app.attach("hanging")
        before = pet.x
        app.change_window("move")
        assert pet.x != before
        app.change_window("minimize")
        assert pet.attachment is None
        app.pause.set(True)
        app.capture_policy()
        app.capture()
        assert not app.preview_windows
        assert "暂停" in app.status.get()
        app.pause.set(False)
        app.capture_policy()
        app.capture()
        deadline = time.monotonic() + 5
        while not app.preview_windows and time.monotonic() < deadline:
            app.api.poll()
            root.update_idletasks()
        assert len(app.preview_windows) == 1
        app.reset()
        assert len(app.world.pets) == 3
        assert not app.preview_windows
        app.start_replay()
        for _ in range(90):
            app.world.tick(100)
            while app.replay and app.world.time_ms >= app.replay[0][0]:
                _, operation = app.replay.pop(0)
                operation()
            app._render()
            root.update_idletasks()
        assert not app.replay
        pet = next(iter(app.world.pets.values()))
        assert pet.state == "standing" and pet.attachment is None
        assert "sample-window" not in app.world.surfaces
        app.world.command(pet.id, "wave", "render-failure")
        del app.photos[pet.frame.path]  # 固定注入宿主资产引用损坏。
        app._render()
        assert len(app.world.pets) == 2
        assert any(e.get("requestId") == "render-failure" and e.get("status") == "failed" for e in app.results)
        app.reset()
        app.load_common()
        one = app.pet_choice.get()
        app.load_common()
        two = app.pet_choice.get()
        assert one != two
        assert app.world.pets[one].package.id == app.world.pets[two].package.id == "contract-sample-moss"
        app.action_choice.set("wave")
        app.action()
        app.api.tick(700)
        assert any(e["type"] == "ActionResult" and e["payload"]["instanceId"] == two and e["payload"]["status"] == "completed" for e in app.public_events)
        app.attach("hanging")
        assert app.world.pets[two].state == "standing"
        assert "unsupported-capability" in app.status.get()
        app._render()
        app.reset()
        assert not errors, errors
        print("PASS GUI: unified samples, PNG decoding, drag interruption, attach/move/minimize, async capture pause/preview, reset, fixed replay; common v1 Tk decode, two instances, wave completion, missing-posture rejection")
        if args.win32:
            desktop = WindowsDesktop()
            assert desktop.bounds().width > 0
            assert desktop.work_area().height > 0
            surfaces = desktop.surfaces()
            assert all(s.source == "windows" and s.id.startswith("native-window-") for s in surfaces)
            # 隐藏的原生窗口验证真实 HRGN 像素边界，不向桌面发送输入。
            window = tk.Toplevel(root)
            window.withdraw()
            window.overrideredirect(True)
            frame = next(iter(app.world.pets.values())).frame
            photo = app.photos[frame.path]
            window.geometry("64x64")
            root.update_idletasks()
            desktop.configure_overlay(window)
            desktop.shape(window, photo)
            hwnd = desktop.user.GetAncestor(window.winfo_id(), 2)
            assert desktop.get_style(hwnd, -20) & 0x08000000
            info = desktop.display_info(20, 20)
            assert info["scaleFactor"] > 0 and info["displayId"]
            excluded = desktop.register_host("hidden-test", window)
            assert desktop.target_ready("hidden-test") == excluded
            print(f"WIN32 host exclusion property acknowledged: {excluded}; pixel-level exclusion remains unverified")
            desktop.unregister_host("hidden-test")
            assert not desktop.target_ready("hidden-test")
            desktop.user.GetWindowRgn.argtypes = [W.HWND, W.HANDLE]
            desktop.gdi.PtInRegion.argtypes = [W.HANDLE, C.c_int, C.c_int]
            region = desktop.gdi.CreateRectRgn(0, 0, 0, 0)
            try:
                assert desktop.user.GetWindowRgn(hwnd, region) > 0
                assert not desktop.gdi.PtInRegion(region, 0, 0)
                assert desktop.gdi.PtInRegion(region, 32, 35)
            finally:
                desktop.gdi.DeleteObject(region)
                window.destroy()
            desktop.dispose()
            print("PASS WIN32: virtual desktop/work area, real window enumeration, hidden HWND region excludes transparent pixel and includes body pixel")
            # 截图 GDI 管线读取固定白色内存位图，绝不从屏幕读像素。
            screen = desktop.user.GetDC(None)
            memory = desktop.gdi.CreateCompatibleDC(screen)
            bitmap = desktop.gdi.CreateCompatibleBitmap(screen, 16, 16)
            old = desktop.gdi.SelectObject(memory, bitmap)
            try:
                desktop.gdi.PatBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.DWORD]
                assert desktop.gdi.PatBlt(memory, 0, 0, 16, 16, 0x00FF0062)
                from desktop_world.model import Rect
                data = desktop._capture_dc(memory, Rect(0, 0, 16, 16))
                generated = tk.PhotoImage(data=base64.b64encode(data), master=root)
                assert generated.get(8, 8) == (255, 255, 255)
            finally:
                desktop.gdi.SelectObject(memory, old)
                desktop.gdi.DeleteObject(bitmap)
                desktop.gdi.DeleteDC(memory)
                desktop.user.ReleaseDC(None, screen)
            app.native = desktop
            app.api._target_ready = desktop.target_ready
            for pet in app.world.pets.values():
                pet.visible = False
            app._render_overlays()
            assert len(app.overlays) == 3
            assert all(window.state() == "withdrawn" for window, _, _ in app.overlays.values())
            app.reset()
            assert not app.overlays and app.native is None
            print("PASS WIN32: synthetic-memory GDI -> PNG -> Tk pixel; native host creates and cleans three hidden overlays without showing or activating them")
            print("NOT CHECKED: real desktop screenshot, visible overlay drag/layering, click-through to another app, multi-monitor / mixed DPI")
    finally:
        app.close()
    assert app.world.disposed and app.api._closed
    assert not app.photos and not app.overlays
    print("PASS cleanup: timers, world, facade, capture requests, image cache, Tk windows released")


if __name__ == "__main__":
    main()
