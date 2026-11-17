"""宿主直接调用正式入口的最小可执行例子，不启动 HTTP 或访问其他模块。"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from public import create_interaction, PROTOCOL
from providers import FixedModel
from resources import MemoryResources
from dependencies import SimulatedBody, SimulatedCapture


async def main():
    bridge,body=MemoryResources(),SimulatedBody()
    core=create_interaction(model=FixedModel(),resolver=bridge,body=body,capture=SimulatedCapture(bridge))
    def req(kind,payload):
        return dict(protocol=PROTOCOL,sessionId=core.getView(dict(type='user'))['sessionId'],
                    requestId=uuid.uuid4().hex,expiresAtMs=int(time.time()*1000)+60000,type=kind,payload=payload)
    package=json.loads((ROOT/'fixtures/character-package.v1.json').read_text(encoding='utf-8'))
    finished=asyncio.Event()
    unsubscribe=None
    try:
        print(json.dumps(core.getCapabilities(),ensure_ascii=False))
        for instance in ('instance-one','instance-two'):
            receipt=await core.dispatch(req('RegisterCharacter',dict(package=package,loaded=body.load(instance,package))))
            assert receipt['payload']['accepted'],receipt
        def listener(view):
            if sum(m['speaker']['type']=='character' for m in view['messages'])>=2:
                finished.set()
        unsubscribe=core.subscribe(dict(type='user'),listener)
        initial=core.getView(dict(type='user'))  # 先订阅，再取原子快照；使用 revision 去重
        item=bridge.put((ROOT/'fixtures/share.png').read_bytes(),'image/png','固定图片.png',initial['sessionId'])
        command=req('UserInput',dict(kind='message',text='',recipient='all',attachments=[item]))
        receipt=await core.dispatch(command)
        assert receipt['payload']['accepted'],receipt
        assert await core.dispatch(command)==receipt  # 相同请求只执行一次
        await asyncio.wait_for(finished.wait(),5)
        result=core.getView(dict(type='user'))
        assert result['messages'][0]['text']==''
        assert [m['seq'] for m in result['messages']]==[1,2,3]
        print(json.dumps(dict(mode='模拟宿主 / 非跨模块联调',sessionId=result['sessionId'],revision=result['revision'],
                              messages=len(result['messages']),imageOnly=True,dedup=True),ensure_ascii=False))
    finally:
        if unsubscribe:
            unsubscribe()
        print(json.dumps(await core.close(),ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
