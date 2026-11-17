"""独立演示/宿主假依赖：内存资源桥，引用不携带路径或 URL。"""
import uuid
from protocol import MAX_IMAGE, ProtocolError, require


class MemoryResources:
    def __init__(self):
        self.entries = {}

    def put(self, raw, mime, name, session):
        require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_IMAGE, '图片必须为 2MiB 内的字节')
        require((mime == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or
                (mime == 'image/jpeg' and raw.startswith(b'\xff\xd8\xff')), '图片类型不符或不支持')
        ref = uuid.uuid4().hex
        item = dict(attachmentId=uuid.uuid4().hex, name=name, mime=mime, sizeBytes=len(raw), contentRef=ref)
        self.entries[ref] = (session, raw, item)
        return item.copy()

    async def resolve(self, ref, session):
        entry = self.entries.get(ref)
        if not entry or entry[0] != session:
            raise ProtocolError('not-found', '资源引用不存在或已释放')
        return entry[1]

    async def describe(self, ref, session):
        await self.resolve(ref, session)
        return self.entries[ref][2].copy()

    def release(self, ref, session):
        if ref in self.entries and self.entries[ref][0] == session:
            self.entries.pop(ref)

    async def releaseSession(self, session):
        self.entries = {k: v for k, v in self.entries.items() if v[0] != session}
