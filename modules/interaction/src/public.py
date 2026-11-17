"""宿主公开入口：在宿主选定的 asyncio 循环中创建并调用。"""
from engine import Interaction
from protocol import PROTOCOL, IMPLEMENTATION, ProtocolError


def create_interaction(*, model, resolver=None, body=None, capture=None, **options):
    return Interaction(model, resolver=resolver, body=body, capture=capture, **options)


__all__ = ['create_interaction', 'Interaction', 'ProtocolError', 'PROTOCOL', 'IMPLEMENTATION']
