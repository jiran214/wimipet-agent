import asyncio
import copy
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from public import create_interaction, PROTOCOL
from resources import MemoryResources

PACKAGE = json.loads((ROOT/'fixtures/character-package.v1.json').read_text(encoding='utf-8'))


class Clock:
    mono = 10.0
    epoch = 1800000000000
    def advance(self, seconds):
        self.mono += seconds
        self.epoch += int(seconds*1000)


class Model:
    mode = 'test-fixed'
    def __init__(self):
        self.calls, self.gates, self.fail = [], {}, set()
        self.ignore_cancel = False
        self.result = None
    async def generate(self, role, context, session):
        self.calls.append((copy.deepcopy(role),copy.deepcopy(context),session))
        gate = self.gates.get(role['instanceId'])
        if gate:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancel:
                    raise
                await gate.wait()
        if role['instanceId'] in self.fail:
            raise RuntimeError('private response secret-key')
        return self.result if self.result is not None else dict(text=role['instanceId']+':'+(context[-1]['text'] if context else '安静'),actionId='wave')


class Body:
    def __init__(self,clock):
        self.commands=[]
        self.clock=clock
        self.source=uuid.uuid4().hex
        self.seq=0
        self.fail=False
    async def send(self,request):
        self.commands.append(copy.deepcopy(request))
        if self.fail:
            raise RuntimeError('transport failure')
    def loaded(self,instance,package=PACKAGE):
        return dict(instanceId=instance,packageId=package['packageId'],revision=package['revision'],
                    supportedActions=[{k:a[k] for k in ('actionId','meaning','allowedStates')} for a in package['actions']],
                    supportedPostures=['standing','falling','dragging'])
    def event(self,command,status):
        self.seq+=1
        return dict(protocol=PROTOCOL,sessionId=command['sessionId'],requestId=command['requestId'],
                    expiresAtMs=self.clock.epoch+60000,type='ActionResult',eventId=uuid.uuid4().hex,
                    source='A',sourceInstanceId=self.source,sourceSeq=self.seq,atMs=self.clock.epoch,
                    payload=dict(instanceId=command['payload']['instanceId'],status=status))


class Capture:
    def __init__(self,resources,clock):
        self.resources,self.clock=resources,clock
        self.policy,self.calls=None,[]
        self.policy_gate,self.capture_gate=None,None
        self.fail_policy=False
        self.wrong_revision=False
    async def applyObservationPolicy(self,value):
        if self.policy_gate:
            await self.policy_gate.wait()
        if self.fail_policy:
            raise RuntimeError('private upstream detail')
        self.policy=copy.deepcopy(value)
        return dict(policyRevision=value['policyRevision']+(1 if self.wrong_revision else 0))
    async def capture(self,request):
        self.calls.append(copy.deepcopy(request))
        if self.capture_gate:
            await self.capture_gate.wait()
        attachment=self.resources.put((ROOT/'fixtures/desktop.png').read_bytes(),'image/png','桌面.png',request['sessionId'])
        p=request['payload']
        return dict(protocol=PROTOCOL,sessionId=request['sessionId'],requestId=request['requestId'],
                    expiresAtMs=request['expiresAtMs'],type='CaptureResult',
                    payload=dict(policyRevision=p['policyRevision'],sceneId=p['sceneId'],capturedAtMs=self.clock.epoch,
                                 source='simulation',rect=dict(x=0,y=0,width=480,height=280),contentRef=attachment['contentRef']))


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(),3)


async def drain(engine):
    async def wait():
        while engine.tasks:
            await asyncio.gather(*list(engine.tasks),return_exceptions=True)
    await asyncio.wait_for(wait(),3)
