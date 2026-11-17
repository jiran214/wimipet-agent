"""A 的唯一跨模块边界：wimipet/1 JSON facade。

所有方法在创建线程调用；采集工作线程只返回字节，不触碰身体/Tk/订阅。
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
import json
import queue
import threading
import time
import uuid

from . import __version__
from .model import World, Rect
from .package_v1 import LIMITS, ProtocolError, identity, integer, load_package, decode_png


@dataclass
class Record:
    fingerprint: str
    deadline: float
    expires_at: int
    response: dict | None = None
    terminal: bool = False


class DesktopWorldAPI:
    def __init__(self, world=None, *, decoder=None, capture_backend=None, resolve_scope=None,
                 display_info=None, target_ready=None, source="simulation", epoch_ms=None, monotonic=None,
                 max_requests=1024, session_id=None):
        self.world = world or World()
        self.session_id = session_id or str(uuid.uuid4())
        self.source_instance_id = str(uuid.uuid4())
        self.source_seq = 0
        self._owner = threading.get_ident()
        self._epoch = epoch_ms or (lambda: time.time_ns() // 1000000)
        self._mono = monotonic or time.monotonic
        self._decoder = decoder
        self._backend = capture_backend
        self._resolve_scope = resolve_scope
        self._display = display_info or (lambda x, y: dict(originX=0, originY=0, displayId="simulation-primary", scaleFactor=1))
        self._target_ready = target_ready
        self.source = source
        self._max_requests = max_requests
        self._records = {}
        self._contexts = {}
        self._policy = dict(policyRevision=0, paused=True, scope="desktop", cooldownMs=0, excludedRegions=[])
        self._allowed = self.world.bounds
        self._last_capture = float("-inf")
        self._generation = 0
        self._targets = set()
        self._jobs = {}
        self._workers = []
        self._completions = queue.SimpleQueue()
        self._resources = {}
        self._resource_bytes = 0
        self._events = []
        self._position_cache = {}  # 可丢弃的派生去重缓存，不拥有身体位置。
        self._subscribers = []
        self._closed = False
        self._cancel_world = self.world.subscribe(self._world_event)

    def _check(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("A facade 必须由其宿主线程调用")
        if self._closed:
            raise ProtocolError("unavailable", "A 已关闭")

    def handshake(self):
        return dict(protocol="wimipet/1", module="A", implementationVersion=__version__,
                    capabilities=dict(characterPackage=True, actualPngDecode=True, multiInstance=True,
                                      actionLifecycle=True, positions="desktop-physical-px", observationPolicy=True,
                                      asynchronousCapture=self._backend is not None, hostExclusionRegistration=True,
                                      attachmentPostures=["lying", "clinging", "hanging"],
                                      requestTypes=["LoadCharacter", "RemoveCharacter", "ActionCommand", "AttachCharacter",
                                                    "ApplyObservationPolicy", "CaptureRequest", "RegisterHostTarget",
                                                    "UnregisterHostTarget", "CancelRequest", "ReleaseResource"],
                                      source=self.source, icons=False, preciseOcclusion=False, nativeHost= self.source == "native"),
                    limits={**LIMITS, "requestRecords": self._max_requests, "capturePixels": 3840 * 2160,
                            "resourceBytes": 32 * 1048576, "captureWorkers": 2},
                    sessionId=self.session_id, sourceInstanceId=self.source_instance_id)

    def subscribe(self, listener):
        self._check()
        self._subscribers.append(listener)
        def unsubscribe():
            if listener in self._subscribers:
                self._subscribers.remove(listener)
        return unsubscribe  # 不在返回之前同步回调。

    def _emit(self, kind, payload, request_id=None):
        if kind == "Position":
            instance = payload["instanceId"]
            if self._position_cache.get(instance) == payload:
                return None
            self._position_cache[instance] = copy.deepcopy(payload)
        self.source_seq += 1
        event_id = f"{self.source_instance_id}:{self.source_seq}"
        event = dict(protocol="wimipet/1", sessionId=self.session_id, source="A",
                     sourceInstanceId=self.source_instance_id, sourceSeq=self.source_seq,
                     atMs=self._epoch(), eventId=event_id, requestId=request_id or event_id,
                     expiresAtMs=self._records[request_id].expires_at if request_id in self._records else self._epoch() + 60000,
                     type=kind, payload=copy.deepcopy(payload))
        self._events.append(event)
        return event

    def _response(self, request_id, kind, payload=None, error=None):
        result = dict(protocol="wimipet/1", sessionId=self.session_id, requestId=request_id,
                      expiresAtMs=self._records[request_id].expires_at if isinstance(request_id, str) and request_id in self._records else self._epoch() + 60000,
                      type=kind, payload=payload or {})
        if error:
            result["error"] = error.json() if isinstance(error, ProtocolError) else error
        return result

    def _world_event(self, event):
        if event["type"] == "action-result":
            request = event["requestId"]
            if request not in self._contexts:
                return  # 本地旧控制回放结果不冒充跨模块请求。
            status = event["status"]
            reason = event.get("reason", "")
            payload = dict(instanceId=event["petId"], status=status)
            if reason:
                payload.update(reasonCode="timeout" if "截止时间" in reason else ("unsupported-capability" if status == "rejected" else
                               ("internal" if status == "failed" else "cancelled")), message=reason)
            self._emit("ActionResult", payload, request)
            if status in ("rejected", "completed", "interrupted", "failed"):
                if request in self._records:
                    self._records[request].terminal = True
            if request in self._records:
                self._records[request].response = self._response(request, "ActionResult", payload)
        elif event["type"] in ("position", "docked", "attached", "released", "added"):
            instance = event.get("petId")
            if instance in self.world.pets:
                self._publish_position(instance)
        elif event["type"] in ("surface", "surface-removed", "workspace"):
            payload = {key: value for key, value in event.items() if key not in ("protocol", "sequence", "timeMs", "source")}
            if "observedAtMs" in payload:
                payload["observedAtMs"] = self._epoch()
            self._emit("DesktopEvent", payload)
        elif event["type"] in ("click", "drag-start", "drag-end", "removed"):
            if event["type"] == "removed":
                self._position_cache.pop(event["petId"], None)
            self._emit("Interaction", dict(instanceId=event["petId"], kind=event["type"]))

    def position(self, instance_id):
        pet = self.world.pets[instance_id]
        x, y = pet.origin
        display = self._display(x + pet.frame.width / 2, y + pet.frame.height / 2)
        return dict(instanceId=instance_id, x=x, y=y, width=pet.frame.width, height=pet.frame.height,
                    anchor=dict(x=pet.frame.contact[0], y=pet.frame.contact[1]),
                    coordinateSpace="desktop-physical-px", visible=pet.visible, **display)

    def _publish_position(self, instance):
        try:
            self._emit("Position", self.position(instance))
        except Exception:
            self._emit("PositionError", dict(instanceId=instance,
                                             error=ProtocolError("unavailable", "显示器坐标或缩放暂不可用", True).json()))

    def getPositions(self):
        self._check()
        return dict(protocol="wimipet/1", sessionId=self.session_id,
                    positions=[self.position(key) for key in self.world.pets])

    def authorize(self, rect):
        """装配注入的用户授权，策略不能扩大此范围。"""
        self._check()
        self._rect(vars(rect))
        self._allowed = rect
        self._generation += 1
        self._invalidate_captures("stale", "授权范围变化")

    @staticmethod
    def _rect(raw):
        if not isinstance(raw, dict) or not all(type(raw.get(k)) in (int, float) for k in ("x", "y", "width", "height")):
            raise ProtocolError("invalid-input", "矩形字段无效")
        import math
        rect = Rect(**{k: raw[k] for k in ("x", "y", "width", "height")})
        if not all(math.isfinite(v) for v in vars(rect).values()) or rect.width <= 0 or rect.height <= 0:
            raise ProtocolError("invalid-input", "矩形尺寸无效")
        return rect

    def request(self, envelope):
        request_id = envelope.get("requestId", "invalid-request") if isinstance(envelope, dict) else "invalid-request"
        try:
            self._check()
            if not isinstance(envelope, dict) or envelope.get("protocol") != "wimipet/1":
                raise ProtocolError("unsupported-version", "只接受 wimipet/1")
            if not identity(request_id) or not isinstance(envelope.get("payload"), dict) or not identity(envelope.get("type")):
                raise ProtocolError("invalid-input", "请求信封无效")
            required = envelope.get("requiredCapabilities", [])
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                raise ProtocolError("invalid-input", "必需能力声明无效")
            if any(not self.handshake()["capabilities"].get(item) for item in required):
                raise ProtocolError("unsupported-capability", "请求声明了未知或未实现的必需能力")
            if envelope.get("sessionId") != self.session_id:
                raise ProtocolError("stale", "会话已失效")
            expires = envelope.get("expiresAtMs")
            if not integer(expires, 0, 2**53 - 1):
                raise ProtocolError("invalid-input", "必须提供 epoch 毫秒截止时间")
            fingerprint = hashlib.sha256(json.dumps([envelope["type"], envelope["payload"], required], sort_keys=True,
                                                   ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
            old = self._records.get(request_id)
            if old:
                if old.deadline <= self._mono():
                    raise ProtocolError("stale", "原请求已过期")
                if old.fingerprint != fingerprint:
                    raise ProtocolError("conflict", "同一请求标识携带不同内容")
                return copy.deepcopy(old.response)
            remaining = expires - self._epoch()
            if remaining <= 0:
                raise ProtocolError("stale", "请求已过期")
            self._records = {key: value for key, value in self._records.items() if value.deadline > self._mono() or not value.terminal}
            self._contexts = {key: value for key, value in self._contexts.items() if key in self._records}
            if len(self._records) >= self._max_requests:
                raise ProtocolError("unavailable", "去重容量已满，请等待有效记录过期", True)
            record = Record(fingerprint, self._mono() + remaining / 1000, expires)
            self._records[request_id] = record
            self._contexts[request_id] = envelope["type"]
            try:
                result = self._dispatch(envelope["type"], envelope["payload"], request_id)
            except ProtocolError as exc:
                result = self._response(request_id, "Error", error=exc)
                record.terminal = True
            record.response = copy.deepcopy(result)
            if envelope["type"] not in ("ActionCommand", "CaptureRequest") or "error" in result:
                record.terminal = True
            return result
        except ProtocolError as exc:
            return self._response(request_id, "Error", error=exc)
        except (TypeError, ValueError, KeyError, OverflowError):
            result = self._response(request_id, "Error", error=ProtocolError("invalid-input", "输入字段无效"))
            if request_id in self._records:
                self._records[request_id].response = result
                self._records[request_id].terminal = True
            return result
        except Exception:
            result = self._response(request_id, "Error", error=ProtocolError("internal", "A 宿主处理失败"))
            if isinstance(request_id, str) and request_id in self._records:
                self._records[request_id].response = result
                self._records[request_id].terminal = True
            return result

    def _dispatch(self, kind, p, request):
        if kind == "LoadCharacter":
            instance = p.get("instanceId")
            if not identity(instance, reserved=True):
                raise ProtocolError("invalid-input", "instanceId 无效或保留值 all")
            package = load_package(p.get("package"), **({"decoder": self._decoder} if self._decoder else {}))
            # 所有校验和解码结束才改变旧实例，失败保留原版本。
            x, y = self.world.bounds.x + 120, self.world.bounds.bottom
            if instance in self.world.pets:
                old = self.world.pets[instance]
                x, y = old.x, old.y
                self.world.remove(instance)
            self.world.add(instance, package, x, y)
            payload = self._loaded(instance)
            return self._response(request, "CharacterLoaded", payload)
        if kind == "RemoveCharacter":
            self._instance(p)
            self.world.remove(p["instanceId"])
            return self._response(request, "CharacterRemoved", dict(instanceId=p["instanceId"]))
        if kind == "ActionCommand":
            self._instance(p)
            if not identity(p.get("actionId")) or type(p.get("enqueue")) is not bool:
                raise ProtocolError("invalid-input", "actionId/enqueue 无效")
            self.world.command(p["instanceId"], p["actionId"], request, p["enqueue"])
            event = next(e for e in reversed(self._events) if e["requestId"] == request and e["type"] == "ActionResult")
            return self._response(request, "ActionResult", event["payload"])
        if kind == "AttachCharacter":
            self._instance(p)
            posture = p.get("posture")
            if not self.world.posture_action(self.world.pets[p["instanceId"]].package, posture):
                raise ProtocolError("unsupported-capability", "该角色未加载可维持此姿态的真实资产")
            if p.get("surfaceId") not in self.world.surfaces:
                raise ProtocolError("not-found", "依附窗口不存在")
            if not self.world.attach(p["instanceId"], p["surfaceId"], posture):
                raise ProtocolError("unavailable", "窗口当前不可依附")
            return self._response(request, "CharacterAttached", dict(instanceId=p["instanceId"], posture=posture))
        if kind == "ApplyObservationPolicy":
            rev = p.get("policyRevision")
            if (not integer(rev, 1, 2**53 - 1) or type(p.get("paused")) is not bool or p.get("scope") not in ("desktop", "active-window")
                    or not integer(p.get("cooldownMs"), 0, 300000) or not isinstance(p.get("excludedRegions"), list)):
                raise ProtocolError("invalid-input", "观察策略字段无效")
            for rect in p["excludedRegions"]:
                self._rect(rect)
            if rev <= self._policy["policyRevision"]:
                raise ProtocolError("stale", "策略版本必须递增")
            self._policy = {key: copy.deepcopy(p[key]) for key in ("policyRevision", "paused", "scope", "cooldownMs", "excludedRegions")}
            self._generation += 1
            self._invalidate_captures("stale", "观察策略已变化")
            self._clear_resources()
            return self._response(request, "ObservationPolicyApplied", copy.deepcopy(self._policy))
        if kind == "CaptureRequest":
            return self._start_capture(p, request)
        if kind == "RegisterHostTarget":
            target = p.get("targetId")
            if not identity(target):
                raise ProtocolError("invalid-input", "targetId 无效")
            self._targets.add(target)
            self._generation += 1
            self._invalidate_captures("stale", "宿主登记变化")
            if not self._is_target_ready(target):
                raise ProtocolError("unavailable", "目标尚不能保证排除；保留登记并阻止采集", True)
            return self._response(request, "HostTargetRegistered", dict(targetId=target, excluded=True))
        if kind == "UnregisterHostTarget":
            self._targets.discard(p.get("targetId"))
            self._generation += 1
            self._invalidate_captures("stale", "宿主已注销")
            return self._response(request, "HostTargetUnregistered")
        if kind == "CancelRequest":
            target = p.get("requestId")
            if target not in self._records:
                raise ProtocolError("not-found", "待取消请求不存在")
            if target in self._jobs:
                self._finish_job_error(target, "cancelled", "采集已取消")
            else:
                self.world.cancel_request(target)
            return self._response(request, "RequestCancelled", dict(requestId=target))
        if kind == "ReleaseResource":
            self.releaseResource(p.get("contentRef"))
            return self._response(request, "ResourceReleased")
        raise ProtocolError("unsupported-capability", "A 不支持此请求类型")

    def _instance(self, payload):
        if payload.get("instanceId") not in self.world.pets:
            raise ProtocolError("not-found", "运行实例不存在")

    def _is_target_ready(self, target):
        try:
            return self._target_ready is not None and self._target_ready(target) is True
        except Exception:
            return False

    def _loaded(self, instance):
        package = self.world.pets[instance].package
        contact_states = {"feet": {"standing", "falling", "dragging"}, "body": {"lying"},
                          "top-grip": {"hanging"}, "side-grip": {"clinging"}}
        return dict(instanceId=instance, packageId=package.id, revision=package.revision,
                    supportedActions=[dict(actionId=key, name=a.name, meaning=a.meaning,
                                           allowedStates=[s for s in a.states if s in contact_states[a.contact_kind]],
                                           loop=a.loop, contactKind=a.contact_kind, priority=a.priority) for key, a in package.actions.items()
                                      if set(a.states) & contact_states[a.contact_kind]],
                    supportedPostures=[state for state in ("lying", "clinging", "hanging") if self.world.posture_action(package, state)])

    def loadCharacter(self, envelope):
        return self.request(envelope)

    def applyObservationPolicy(self, envelope):
        return self.request(envelope)

    def _start_capture(self, p, request):
        if not self._backend or not self._resolve_scope:
            raise ProtocolError("unavailable", "未注入采集宿主")
        if self._policy["paused"]:
            raise ProtocolError("unavailable", "观察已暂停")
        if p.get("policyRevision") != self._policy["policyRevision"] or p.get("scope") != self._policy["scope"]:
            raise ProtocolError("stale", "策略版本或范围已变化")
        if not identity(p.get("sceneId")):
            raise ProtocolError("invalid-input", "sceneId 无效")
        if self.source == "native" and not self._targets:
            raise ProtocolError("unavailable", "尚未登记任何自身宿主，禁止原生采集")
        if any(not self._is_target_ready(target) for target in self._targets):
            raise ProtocolError("unavailable", "有宿主无法保证排除，拒绝采集")
        rect = self._resolve_scope(p["scope"])
        if not isinstance(rect, Rect) or not self._allowed.contains(rect):
            raise ProtocolError("unavailable", "实际采集范围超出授权区域")
        if not all(type(v) is int for v in vars(rect).values()) or rect.width <= 0 or rect.height <= 0 or rect.width * rect.height > 3840 * 2160:
            raise ProtocolError("invalid-input", "实际范围必须为有效整数像素且不超上限")
        if any(rect.intersects(self._rect(r)) for r in self._policy["excludedRegions"]):
            raise ProtocolError("unavailable", "实际采集范围与排除区域相交，整次拒绝")
        if self._mono() - self._last_capture < self._policy["cooldownMs"] / 1000:
            raise ProtocolError("unavailable", "采集冷却中", True)
        self._workers = [job for job in self._workers if job["thread"].is_alive()]
        if len(self._workers) >= 2 or self._resource_bytes >= 32 * 1048576:
            raise ProtocolError("unavailable", "采集或资源容量已满", True)
        cancel = threading.Event()
        job = dict(cancel=cancel, generation=self._generation, session=self.session_id, policy=self._policy["policyRevision"],
                   rect=rect, scene=p["sceneId"], request=request)
        self._jobs[request] = job
        backend = self._backend  # 重置后不改用新宿主。
        completion_queue = self._completions
        epoch = self._epoch
        def worker():
            try:
                data = backend(rect, cancel)
                captured_at = epoch()
                if cancel.is_set():
                    return
                if not isinstance(data, bytes):
                    raise ValueError("backend 未返回 PNG 字节")
                if decode_png(data, max_dimension=3840, max_pixels=3840 * 2160) != (rect.width, rect.height):
                    raise ValueError("截图实际尺寸与范围不符")
                if not cancel.is_set():
                    completion_queue.put((job, data, captured_at, None))
            except Exception:
                if not cancel.is_set():
                    completion_queue.put((job, None, epoch(), ProtocolError("unavailable", "采集器失败", True)))
        thread = threading.Thread(target=worker, name=f"A-capture-{request[:24]}", daemon=True)
        job["thread"] = thread
        self._workers.append(job)
        self._last_capture = self._mono()
        try:
            thread.start()
        except Exception as exc:
            self._jobs.pop(request, None)
            self._workers.remove(job)
            raise ProtocolError("unavailable", "采集线程无法启动", True) from exc
        return self._response(request, "CaptureReceipt", dict(accepted=True, policyRevision=job["policy"], sceneId=job["scene"]))

    def _finish_job_error(self, request, code, message):
        job = self._jobs.pop(request, None)
        if job:
            job["cancel"].set()
            payload = dict(policyRevision=job["policy"], sceneId=job["scene"], error=ProtocolError(code, message).json())
            self._emit("CaptureError", payload, request)
            self._records[request].terminal = True
            self._records[request].response = self._response(request, "CaptureError", payload)

    def _invalidate_captures(self, code, message):
        for request in list(self._jobs):
            self._finish_job_error(request, code, message)

    def poll(self):
        self._check()
        for request, record in list(self._records.items()):
            if not record.terminal and record.deadline <= self._mono():
                if request in self._jobs:
                    self._finish_job_error(request, "timeout", "采集截止时间已到")
                else:
                    for pet in list(self.world.pets.values()):
                        if pet.request_id == request:
                            self.world.fail_action(pet.id, request, "动作截止时间已到")
                        else:
                            self.world.cancel_request(request, "排队请求截止时间已到")
                record.terminal = True
        while not self._completions.empty():
            job, data, captured_at, error = self._completions.get_nowait()
            request = job["request"]
            if self._jobs.get(request) is not job:
                continue
            if job["session"] != self.session_id or job["generation"] != self._generation:
                self._finish_job_error(request, "stale", "迟到画面已丢弃")
                continue
            if error:
                self._finish_job_error(request, error.code, error.message)
                continue
            if any(not self._is_target_ready(target) for target in self._targets):
                self._finish_job_error(request, "unavailable", "采集期间宿主排除失效，丢弃结果")
                continue
            if self._resource_bytes + len(data) > 32 * 1048576:
                self._finish_job_error(request, "unavailable", "画面资源容量不足")
                continue
            ref = f"A-image:{uuid.uuid4()}"
            self._resources[ref] = data
            self._resource_bytes += len(data)
            self._jobs.pop(request)
            self._records[request].terminal = True
            self._emit("CaptureResult", dict(policyRevision=job["policy"], sceneId=job["scene"], capturedAtMs=captured_at,
                                             source=self.source, rect=vars(job["rect"]), contentRef=ref), request)
            self._records[request].response = self._response(request, "CaptureResult", self._events[-1]["payload"])
        self._workers = [job for job in self._workers if job["thread"].is_alive()]
        # 即便身体未动、单帧 idle，也向消费者发布显示器缩放变化。
        for instance in self.world.pets:
            self._publish_position(instance)
        events, self._events = self._events, []
        for event in events:
            for listener in tuple(self._subscribers):
                try:
                    listener(copy.deepcopy(event))
                except Exception:
                    pass  # 外部订阅失败不停止身体主循环。
        return copy.deepcopy(events)

    def tick(self, delta_ms):
        self._check()
        before = self.poll()  # 先终结超时，避免已到期动作被计为完成。
        self.world.tick(delta_ms)
        return before + self.poll()

    def readResource(self, content_ref):
        self._check()
        if content_ref not in self._resources:
            raise ProtocolError("not-found", "画面引用已失效")
        return self._resources[content_ref]

    def releaseResource(self, content_ref):
        self._resource_bytes -= len(self._resources.pop(content_ref, b""))

    def _clear_resources(self):
        for record in self._records.values():
            if record.response and record.response["type"] == "CaptureResult":
                ref = record.response["payload"].get("contentRef")
                if ref in self._resources:
                    record.response = self._response(record.response["requestId"], "Error", error=ProtocolError("stale", "画面策略或会话已变化"))
        self._resources.clear()
        self._resource_bytes = 0

    def reset(self, world=None):
        self._check()
        self._invalidate_captures("stale", "会话重置")
        for instance in list(self.world.pets):
            self.world.remove(instance)
        self.poll()  # 先向旧会话订阅者送达终结事实。
        self._cancel_world()
        self.world.dispose()
        self.world = world or World()
        self._cancel_world = self.world.subscribe(self._world_event)
        self.session_id = str(uuid.uuid4())
        self._records.clear()
        self._position_cache.clear()
        self._contexts.clear()
        self._targets.clear()
        self._clear_resources()
        self._generation += 1
        self._policy = dict(policyRevision=0, paused=True, scope="desktop", cooldownMs=0, excludedRegions=[])
        self._allowed = self.world.bounds
        self._last_capture = float("-inf")
        return self.session_id

    def close(self):
        if self._closed:
            return
        self._check()
        self._invalidate_captures("cancelled", "A 退出")
        self.world.dispose()
        self.poll()
        self._cancel_world()
        self._subscribers.clear()
        self._position_cache.clear()
        self._clear_resources()
        self._closed = True
