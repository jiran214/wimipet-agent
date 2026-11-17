"""受控采集：先做范围授权，再调用注入的采集器；画面默认只存在内存。"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from .model import Rect


def png(width, height, rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\0" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@dataclass(frozen=True)
class Ticket:
    request_id: str
    epoch: int
    rect: Rect


class CaptureGate:
    def __init__(self, allowed: Rect):
        self.allowed = allowed
        self.excluded: tuple[Rect, ...] = ()
        self.paused = False
        self.epoch = 0
        self.disposed = False
        self.pending: dict[str, Ticket] = {}

    def configure(self, *, allowed=None, excluded=None, paused=None):
        if self.disposed:
            raise RuntimeError("采集器已释放")
        if allowed is not None:
            self.allowed = allowed
        if excluded is not None:
            self.excluded = tuple(excluded)
        if paused is not None:
            self.paused = bool(paused)
        self.epoch += 1

    def begin(self, request_id, rect):
        if self.disposed or self.paused:
            raise PermissionError("观察已暂停或采集器已释放")
        if (not all(isinstance(v, int) for v in vars(rect).values()) or rect.width <= 0 or rect.height <= 0
                or rect.width * rect.height > 3840 * 2160 or not self.allowed.contains(rect)):
            raise PermissionError("请求越出授权范围、像素上限或尺寸无效")
        if any(rect.intersects(excluded) for excluded in self.excluded):
            raise PermissionError("请求与排除区域相交；拒绝整次采集")
        if request_id in self.pending:
            raise ValueError("采集请求标识重复")
        ticket = Ticket(request_id, self.epoch, rect)
        self.pending[request_id] = ticket
        return ticket

    def finish(self, ticket, data):
        if self.pending.pop(ticket.request_id, None) != ticket:
            raise ValueError("未知或已经完成的采集请求")
        stale = self.disposed or ticket.epoch != self.epoch or self.paused
        return dict(requestId=ticket.request_id, epoch=ticket.epoch, stale=stale,
                    status="stale" if stale else "completed", image=None if stale else data)

    def capture(self, request_id, rect, backend):
        ticket = self.begin(request_id, rect)
        try:
            return self.finish(ticket, backend(rect))
        except Exception:
            self.pending.pop(request_id, None)
            raise

    def dispose(self):
        self.disposed, self.paused = True, True
        self.epoch += 1
        self.pending.clear()
