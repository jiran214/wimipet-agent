"""不依赖 GUI、操作系统或其他业务模块的唯一身体状态机。"""
from __future__ import annotations

import copy
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    def contains(self, other):
        return (self.x <= other.x and self.y <= other.y and
                self.right >= other.right and self.bottom >= other.bottom)

    def intersects(self, other):
        return (self.x < other.right and self.right > other.x and
                self.y < other.bottom and self.bottom > other.y)


@dataclass(frozen=True)
class Frame:
    path: Path
    duration: int
    contact: tuple[float, float]
    width: int
    height: int
    data: bytes = b""


@dataclass(frozen=True)
class Action:
    frames: tuple[Frame, ...]
    loop: bool
    states: tuple[str, ...]
    priority: int
    contact_kind: str
    meaning: str = ""
    name: str = ""


@dataclass(frozen=True)
class Package:
    id: str
    version: str
    name: str
    actions: dict[str, Action]
    revision: int = 1
    character: dict = field(default_factory=dict)

    @classmethod
    def load(cls, manifest: Path, *, allow_legacy=False):
        """公开路径默认读取统一包；旧磁盘资产格式仅作显式历史适配。"""
        try:
            if manifest.stat().st_size > 32 * 1048576:
                raise ValueError("包超过 32MiB")
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if raw.get("protocol") == "wimipet/1" or not allow_legacy:
                from .package_v1 import load_package
                return load_package(raw)
            if raw["schema"] != "A-display/1":
                raise ValueError("不支持的角色包协议")
            if not all(isinstance(raw[k], str) and raw[k] for k in ("id", "version", "name")):
                raise ValueError("角色标识、版本、名称不能为空")
            actions = {}
            root = manifest.parent.resolve()
            for name, spec in raw["actions"].items():
                frames = []
                if not isinstance(spec["loop"], bool):
                    raise ValueError("loop 必须为布尔值")
                for item in spec["frames"]:
                    path = (root / item["file"]).resolve()
                    if not path.is_relative_to(root):
                        raise ValueError("资产路径越出角色包")
                    data = path.read_bytes()
                    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                        raise ValueError(f"仅支持 PNG 序列帧: {path.name}")
                    w, h = struct.unpack(">II", data[16:24])
                    duration = item["durationMs"]
                    contact = item["contact"]
                    if not (0 < w <= 512 and 0 < h <= 512 and
                            isinstance(duration, int) and 10 <= duration <= 10000 and
                            len(contact) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in contact)
                            and 0 <= contact[0] <= w and 0 <= contact[1] <= h):
                        raise ValueError("帧尺寸、时长或接触点无效")
                    frames.append(Frame(path, duration, tuple(contact), w, h))
                states = tuple(spec["states"])
                allowed = {"standing", "lying", "clinging", "hanging", "falling", "dragging"}
                if not frames or not states or not set(states) <= allowed or not isinstance(spec["priority"], int):
                    raise ValueError("动作帧、允许状态或优先级无效")
                contact_kind = spec["contactKind"]
                if contact_kind not in ("feet", "body", "top-grip", "side-grip"):
                    raise ValueError("未知接触点类型")
                actions[name] = Action(tuple(frames), spec["loop"], states, spec["priority"], contact_kind)
            if "idle" not in actions or not {"standing", "falling", "dragging"} <= set(actions["idle"].states) or not actions["idle"].loop:
                raise ValueError("必须提供覆盖 standing/falling/dragging 的循环 idle")
            return cls(raw["id"], raw["version"], raw["name"], actions)
        except (KeyError, TypeError, ValueError, OSError, struct.error) as exc:
            raise PackageError(f"角色包 {manifest}: {exc}") from exc


@dataclass
class Surface:
    id: str
    rect: Rect
    minimized: bool = False
    occluded: bool = False
    title: str = ""
    source: str = "simulation"
    active: bool = False


@dataclass
class Pet:
    id: str
    package: Package
    x: float
    y: float
    state: str = "standing"
    visible: bool = True
    attachment: dict | None = None
    action: str = "idle"
    frame_index: int = 0
    elapsed: float = 0
    velocity: float = 0
    request_id: str | None = None
    queue: list = field(default_factory=list)

    @property
    def frame(self):
        return self.package.actions[self.action].frames[self.frame_index]

    @property
    def origin(self):
        return self.x - self.frame.contact[0], self.y - self.frame.contact[1]


class World:
    def __init__(self, bounds=Rect(0, 0, 1000, 620), source="simulation"):
        self.bounds = bounds
        self.source = source
        self.pets: dict[str, Pet] = {}
        self.surfaces: dict[str, Surface] = {}
        self.time_ms = 0.0
        self.events = []
        self.listeners: list[Callable] = []
        self.disposed = False
        self.sequence = 0

    def subscribe(self, callback):
        self._live()
        self.listeners.append(callback)
        def cancel():
            if callback in self.listeners:
                self.listeners.remove(callback)
        return cancel

    def _live(self):
        if self.disposed:
            raise RuntimeError("世界已释放")

    def emit(self, kind, **payload):
        self.sequence += 1
        event = dict(protocol="A-world/1", sequence=self.sequence, timeMs=self.time_ms,
                     source=self.source, type=kind, **payload)
        self.events.append(copy.deepcopy(event))
        del self.events[:-500]
        for callback in tuple(self.listeners):
            try:
                callback(copy.deepcopy(event))
            except Exception:
                # 消费者异常不能阻断身体反馈，错误事件不递归调用消费者。
                self.events.append(dict(type="subscriber-error", timeMs=self.time_ms))

    def add(self, pet_id, package, x, y):
        self._live()
        if pet_id in self.pets:
            raise ValueError("角色实例标识重复")
        pet = Pet(pet_id, package, x, y)
        self.pets[pet_id] = pet
        self._clamp(pet)
        if pet.y < self.bounds.bottom:
            pet.state = "falling"
        self.emit("added", petId=pet_id, packageId=package.id, version=package.version)
        return pet

    def remove(self, pet_id):
        self._live()
        pet = self.pets[pet_id]
        self._interrupt(pet, "角色移除")
        del self.pets[pet_id]
        self.emit("removed", petId=pet_id)

    def _result(self, pet, request, status, reason=""):
        self.emit("action-result", petId=pet.id, requestId=request, status=status, reason=reason)

    def _idle(self, pet):
        fallback = self.posture_action(pet.package, pet.state) or "idle"
        pet.action, pet.frame_index, pet.elapsed, pet.request_id = fallback, 0, 0, None

    @staticmethod
    def posture_action(package, posture):
        kind = {"lying": "body", "clinging": "side-grip", "hanging": "top-grip"}.get(posture)
        if kind is None:
            return None
        return next((name for name, action in package.actions.items()
                     if action.loop and posture in action.states and action.contact_kind == kind), None)

    def _interrupt(self, pet, reason):
        if pet.request_id:
            self._result(pet, pet.request_id, "interrupted", reason)
        for request, _ in pet.queue:
            self._result(pet, request, "interrupted", reason)
        pet.queue.clear()
        self._idle(pet)

    def command(self, pet_id, action, request_id, enqueue=False):
        self._live()
        pet = self.pets[pet_id]
        spec = pet.package.actions.get(action)
        if not spec:
            self._result(pet, request_id, "rejected", "未知动作")
            return False
        if pet.state == "dragging" or pet.state not in spec.states:
            self._result(pet, request_id, "rejected", f"身体状态 {pet.state} 不允许 {action}")
            return False
        required_contact = {"lying": "body", "hanging": "top-grip", "clinging": "side-grip"}.get(pet.state, "feet")
        if spec.contact_kind != required_contact:
            self._result(pet, request_id, "rejected", "动作缺少当前依附所需的接触点类型")
            return False
        if pet.request_id:
            if enqueue:
                if len(pet.queue) >= 8:
                    self._result(pet, request_id, "rejected", "队列已满")
                    return False
                pet.queue.append((request_id, action))
                self._result(pet, request_id, "queued")
                return True
            if spec.priority < pet.package.actions[pet.action].priority:
                self._result(pet, request_id, "rejected", "优先级低于当前动作")
                return False
            self._interrupt(pet, "被新动作打断")
        pet.action, pet.frame_index, pet.elapsed, pet.request_id = action, 0, 0, request_id
        self._result(pet, request_id, "accepted")
        return True

    def drag_start(self, pet_id):
        self._live()
        pet = self.pets[pet_id]
        self._interrupt(pet, "用户拖拽")
        pet.state, pet.attachment, pet.velocity, pet.visible = "dragging", None, 0, True
        self._idle(pet)
        self.emit("drag-start", petId=pet_id)

    def fail_action(self, pet_id, request_id, reason):
        """宿主报告当前请求的真实播放故障；过期请求不得影响后续动作。"""
        self._live()
        pet = self.pets[pet_id]
        if not request_id or pet.request_id != request_id:
            return False
        self._result(pet, request_id, "failed", reason)
        pet.request_id = None
        self._interrupt(pet, "前序动作播放失败")
        return True

    def cancel_request(self, request_id, reason="请求已取消"):
        self._live()
        for pet in self.pets.values():
            if pet.request_id == request_id:
                self._result(pet, request_id, "interrupted", reason)
                self._idle(pet)
                self._next_queued(pet)
                return True
            for index, (queued_id, _) in enumerate(pet.queue):
                if queued_id == request_id:
                    pet.queue.pop(index)
                    self._result(pet, request_id, "interrupted", reason)
                    return True
        return False

    def _next_queued(self, pet):
        while pet.queue and not pet.request_id:
            request, action = pet.queue.pop(0)
            self.command(pet.id, action, request)

    def drag_move(self, pet_id, x, y):
        self._live()
        pet = self.pets[pet_id]
        if pet.state != "dragging":
            raise ValueError("角色未处于拖拽状态")
        pet.x, pet.y = x, y
        self._clamp(pet)
        self.emit("position", **self.snapshot(pet_id))

    def drag_end(self, pet_id):
        self._live()
        pet = self.pets[pet_id]
        if pet.state != "dragging":
            return
        pet.state = "falling"
        self.emit("drag-end", petId=pet_id, x=pet.x, y=pet.y)

    def _clamp(self, pet):
        f = pet.frame
        pet.x = max(self.bounds.x + f.contact[0], min(pet.x, self.bounds.right - f.width + f.contact[0]))
        pet.y = max(self.bounds.y + f.contact[1], min(pet.y, self.bounds.bottom))

    def dock(self, pet_id):
        self._live()
        pet = self.pets[pet_id]
        self._interrupt(pet, "任务栏停靠")
        pet.attachment, pet.state, pet.y, pet.velocity, pet.visible = None, "standing", self.bounds.bottom, 0, True
        self._idle(pet)
        self._clamp(pet)
        self.emit("docked", **self.snapshot(pet_id))

    def attach(self, pet_id, surface_id, posture="lying"):
        self._live()
        pet = self.pets[pet_id]
        surface = self.surfaces.get(surface_id)
        posture_action = self.posture_action(pet.package, posture)
        if not surface or surface.minimized or not posture_action:
            self.emit("attachment-rejected", petId=pet_id, reason="目标不可依附或角色缺少真实姿态资产")
            return False
        self._interrupt(pet, "改变接触关系")
        offset = max(32, min(pet.x - surface.rect.x, max(32, surface.rect.width - 32)))
        pet.attachment = dict(surfaceId=surface_id, offset=offset, posture=posture)
        pet.state, pet.velocity = posture, 0
        self._contact(pet, surface)
        # 姿态演示资产也受状态规则验证；这里不伪造动作完成。
        self.command(pet_id, posture_action, f"posture-{self.sequence + 1}")
        self.emit("attached", **self.snapshot(pet_id))
        return True

    def _contact(self, pet, surface):
        r = surface.rect
        a = pet.attachment
        a["offset"] = min(a["offset"], max(0, r.width))
        pet.x, pet.y = r.x + a["offset"], r.y
        if a["posture"] == "clinging":
            pet.x, pet.y = r.right, r.y + min(64, r.height / 2)
        # 不夹紧有效接触点：窗口移出工作区时由渲染裁切，保持接触关系。
        pet.visible = not surface.occluded and self.bounds.intersects(Rect(*pet.origin, pet.frame.width, pet.frame.height))

    def set_surface(self, surface):
        self._live()
        self.surfaces[surface.id] = copy.deepcopy(surface)
        self.emit("surface", surfaceId=surface.id, rect=vars(surface.rect), minimized=surface.minimized,
                  occluded=surface.occluded, active=surface.active, observedAtMs=self.time_ms, objectSource=surface.source)
        for pet in self.pets.values():
            if pet.attachment and pet.attachment["surfaceId"] == surface.id:
                if surface.minimized:
                    self.release(pet.id, "窗口最小化")
                else:
                    self._contact(pet, surface)

    def set_bounds(self, bounds):
        self._live()
        if bounds.width < 64 or bounds.height < 64:
            raise ValueError("工作区过小")
        self.bounds = bounds
        for pet in self.pets.values():
            if not pet.attachment:
                if pet.state == "standing":
                    pet.y = bounds.bottom
                self._clamp(pet)
        self.emit("workspace", rect=vars(bounds))

    def remove_surface(self, surface_id):
        self._live()
        self.surfaces.pop(surface_id, None)
        for pet in list(self.pets.values()):
            if pet.attachment and pet.attachment["surfaceId"] == surface_id:
                self.release(pet.id, "窗口关闭")
        self.emit("surface-removed", surfaceId=surface_id)

    def release(self, pet_id, reason="主动释放"):
        self._live()
        pet = self.pets[pet_id]
        self._interrupt(pet, reason)
        pet.attachment, pet.state, pet.velocity, pet.visible = None, "falling", 0, True
        self._idle(pet)
        self._clamp(pet)
        self.emit("released", petId=pet_id, reason=reason)

    def tick(self, delta_ms):
        self._live()
        if not math.isfinite(delta_ms) or delta_ms < 0:
            raise ValueError("时间增量无效")
        self.time_ms += delta_ms
        for pet in self.pets.values():
            before = self.snapshot(pet.id)
            remaining = delta_ms / 1000
            # 固定子步长使重放结果不依赖 GUI 刷新率。
            while remaining > 0 and pet.state == "falling":
                dt = min(remaining, 0.016)
                pet.y += pet.velocity * dt + 0.5 * 1000 * dt * dt
                pet.velocity += 1000 * dt
                remaining -= dt
                if pet.y >= self.bounds.bottom:
                    self.dock(pet.id)
            pet.elapsed += delta_ms
            while pet.elapsed >= pet.frame.duration:
                pet.elapsed -= pet.frame.duration
                spec = pet.package.actions[pet.action]
                pet.frame_index += 1
                if pet.frame_index == len(spec.frames):
                    if spec.loop:
                        pet.frame_index = 0
                    else:
                        request = pet.request_id
                        self._idle(pet)
                        if request:
                            self._result(pet, request, "completed")
                        self._next_queued(pet)
                        break
            if pet.attachment:
                self._contact(pet, self.surfaces[pet.attachment["surfaceId"]])
            if self.snapshot(pet.id) != before:
                self.emit("position", **self.snapshot(pet.id))

    def snapshot(self, pet_id):
        pet = self.pets[pet_id]
        return dict(petId=pet.id, x=pet.x, y=pet.y, state=pet.state, visible=pet.visible,
                    attachment=copy.deepcopy(pet.attachment), action=pet.action, frame=pet.frame_index)

    def dispose(self):
        if self.disposed:
            return
        for pet_id in list(self.pets):
            self.remove(pet_id)
        self.surfaces.clear()
        self.listeners.clear()
        self.disposed = True
