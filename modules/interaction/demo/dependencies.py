"""明确标记的本模块替身，不导入 A/B/D，不代表真实包加载或采集验证。"""
import asyncio
import copy
import time
import uuid
from pathlib import Path
from protocol import PROTOCOL, ProtocolError


class SimulatedBody:
    def __init__(self):
        self.loaded, self.commands = {}, {}
        self.sourceInstanceId, self.seq = uuid.uuid4().hex, 0

    def load(self, instance, package):
        loaded = dict(instanceId=instance, packageId=package['packageId'], revision=package['revision'],
                      supportedActions=[{k: a[k] for k in ('actionId', 'meaning', 'allowedStates')} for a in package['actions']],
                      supportedPostures=['standing', 'falling', 'dragging'])
        self.loaded[instance] = loaded
        return copy.deepcopy(loaded)

    async def send(self, request):
        p = request['payload']
        if not any(a['actionId'] == p['actionId'] for a in self.loaded.get(p['instanceId'], {}).get('supportedActions', [])):
            raise ProtocolError('unsupported-capability', '模拟身体未加载此动作')
        self.commands[request['requestId']] = copy.deepcopy(request)
        # 仅模拟送达。具体 queued/accepted 等由用户点击反馈，绝不自动伪造完成。

    def event(self, rid, status):
        command = self.commands.get(rid)
        if not command:
            raise ProtocolError('not-found', '模拟身体尚未收到动作')
        self.seq += 1
        return dict(protocol=PROTOCOL, sessionId=command['sessionId'], requestId=rid,
                    expiresAtMs=int(time.time()*1000)+60000, type='ActionResult', source='A',
                    sourceInstanceId=self.sourceInstanceId, sourceSeq=self.seq, eventId=uuid.uuid4().hex,
                    atMs=int(time.time()*1000), payload=dict(instanceId=command['payload']['instanceId'],
                                                           status=status, message='本地模拟执行反馈'))


class SimulatedCapture:
    def __init__(self, resources):
        self.resources, self.policy, self.behavior = resources, None, 'normal'

    async def applyObservationPolicy(self, policy):
        await asyncio.sleep(2 if self.behavior == 'slow' else .15)
        if self.behavior == 'fail':
            raise ProtocolError('unavailable', '模拟上游策略拒绝', True)
        if self.policy and self.policy['policyRevision'] >= policy['policyRevision']:
            raise ProtocolError('stale', '模拟采集端拒绝旧策略')
        self.policy = copy.deepcopy(policy)
        return dict(policyRevision=policy['policyRevision'])

    async def capture(self, request):
        await asyncio.sleep(.25)
        p = request['payload']
        if not self.policy or self.policy['paused'] or self.policy['policyRevision'] != p['policyRevision']:
            raise ProtocolError('unavailable', '模拟采集策略不允许本次请求')
        raw = (Path(__file__).resolve().parents[1]/'fixtures/desktop.png').read_bytes()
        attachment = self.resources.put(raw, 'image/png', '模拟桌面.png', request['sessionId'])
        return dict(protocol=PROTOCOL, type='CaptureResult', requestId=request['requestId'], sessionId=request['sessionId'],
                    expiresAtMs=request['expiresAtMs'], payload=dict(policyRevision=p['policyRevision'], sceneId=p['sceneId'],
                    capturedAtMs=int(time.time()*1000), source='simulation', rect=dict(x=0,y=0,width=480,height=280),
                    contentRef=attachment['contentRef']))
