"""Single-owner assembly: real A body, C conversation, B package, D view."""
import asyncio
import base64
import copy
import json
from pathlib import Path
import sys
import time
import uuid
from config import ROOT, load_config

sys.path.insert(0, str(ROOT/'modules/desktop-world/src'))
sys.path.insert(0, str(ROOT/'modules/interaction/src'))
from desktop_world.api import DesktopWorldAPI
from desktop_world.model import World, Rect
from public import create_interaction
from providers import FixedModel, OpenAICompatibleModel
from resources import MemoryResources

class Runtime:
    def __init__(self, offline=False):
        self.config = load_config()
        self.resources = MemoryResources()
        self.model = FixedModel() if offline else OpenAICompatibleModel(self.resources, self.config)
        self.core = create_interaction(model=self.model, resolver=self.resources, body=self,
                                       capture=self, timeout=float(self.config.get('MODEL_TIMEOUT_SECONDS',60)))
        self.a = DesktopWorldAPI(World(Rect(0,0,900,450)), session_id=self.core.sessionId)
        self.packages = {}
        self.retained_refs = set()
        self.running = True
        self.ticker = None

    def envelope(self, kind, payload):
        return dict(protocol='wimipet/1',sessionId=self.core.sessionId,requestId=uuid.uuid4().hex,
                    expiresAtMs=time.time_ns()//1000000+60000,type=kind,payload=payload)

    async def load(self, package, instance=None):
        instance = instance or 'pet-'+uuid.uuid4().hex[:8]
        result = self.a.request(self.envelope('LoadCharacter',dict(instanceId=instance,package=package)))
        if result.get('error'): raise ValueError(result['error']['message'])
        receipt = await self.core.dispatch(self.envelope('RegisterCharacter',dict(package=package,loaded=result['payload'])))
        if not receipt['payload']['accepted']:
            self.a.request(self.envelope('RemoveCharacter',dict(instanceId=instance)))
            raise ValueError('角色配置未被接受')
        self.packages[instance]=package
        self.a.placeCharacter(instance,100+((len(self.packages)-1)%6)*140,400)
        return instance

    async def start(self):
        a_caps=self.a.handshake();c_caps=self.core.getCapabilities()
        if a_caps['protocol']!='wimipet/1' or c_caps['protocol']!='wimipet/1':
            raise RuntimeError('模块协议不兼容')
        if not all(a_caps['capabilities'].get(k) for k in ('characterPackage','actualPngDecode','actionLifecycle')):
            raise RuntimeError('身体模块缺少必要能力')
        if not {'user-input','message-view','action-lifecycle'}.issubset(c_caps['capabilities']):
            raise RuntimeError('互动模块缺少必要能力')
        package=json.loads((ROOT/'docs/contracts/fixtures/v1/character-package.json').read_text(encoding='utf-8'))
        for name,persona in [('苔苔','温和细腻，喜欢植物，愿意倾听。'),('火花','活泼好奇，爱开轻松玩笑，不强迫别人聊天。'),('星轨','沉静理性，简短表达关心。')]:
            p=copy.deepcopy(package);p['packageId']='starter-'+str(len(self.packages));p['character'].update(name=name,persona=persona)
            await self.load(p)
        await self.core.dispatch(self.envelope('SetObservationPolicy',dict(policyRevision=1,paused=True,scope='desktop',cooldownMs=10000,excludedRegions=[])))
        self.ticker=asyncio.create_task(self.tick())

    async def tick(self):
        last=time.monotonic()
        while self.running:
            now=time.monotonic()
            for event in self.a.tick(min(250,(now-last)*1000)):
                if event['type']=='ActionResult':
                    event['expiresAtMs']=time.time_ns()//1000000+60000
                    await self.core.dispatch(event)
            last=now
            await asyncio.sleep(.03)

    async def send(self, request):
        result=self.a.request(request)
        if result.get('error'): raise ValueError(result['error']['message'])

    async def applyObservationPolicy(self, policy):
        result=self.a.request(self.envelope('ApplyObservationPolicy',policy))
        if result.get('error'): raise ValueError(result['error']['message'])
        return dict(policyRevision=policy['policyRevision'])

    async def capture(self, request):
        # Stage has no native capture backend; never pretend this is a screenshot.
        raise RuntimeError('当前舞台不采集桌面，请直接分享图片')

    def state(self):
        roles=[]
        for instance,p in self.packages.items():
            pos=self.a.position(instance)
            roles.append(dict(instanceId=instance,packageId=p['packageId'],revision=p['revision'],
                name=p['character']['name'],interactive=True,avatarRef='frame-'+instance,
                position=dict(x=pos['x']+pos['width']/2,y=pos['y'],visible=pos['visible'],coordinateSpace='stage-css-px')))
        view=self.core.getView(dict(type='user'))
        return dict(view=view,scene=dict(sessionId=self.core.sessionId,
            roles=roles,mode=self.model.mode+' · 陪伴舞台',observationPolicy=view['processing']['observation']['confirmed']),
            model=self.config.get('OPENAI_MODEL'),studioPort=int(self.config.get('STUDIO_PORT',4312)))

    async def dispatch(self, request):
        request=copy.deepcopy(request)
        if request['type']=='ObservationPolicy' and not request['payload'].get('paused',True):
            return dict(protocol='wimipet/1',sessionId=self.core.sessionId,requestId=request['requestId'],
                payload=dict(accepted=False,error=dict(code='unsupported-capability',message='舞台未接入桌面采集',retryable=False)))
        if request['type']=='ObservationPolicy': request['type']='SetObservationPolicy'
        if request['type']=='CancelRequest' and 'requestId' in request['payload']:
            request['payload']['targetRequestId']=request['payload'].pop('requestId')
        if request['type']=='UserInput':
            # Keep resources through uncertain receipts, cancellation and model execution.
            self.retained_refs.update(item['contentRef'] for item in request['payload'].get('attachments',[]))
        return await self.core.dispatch(request)

    def release_draft(self, ref):
        if ref not in self.retained_refs:
            self.resources.release(ref,self.core.sessionId)

    async def close(self):
        self.running=False
        if self.ticker: await self.ticker
        report=await self.core.close()
        self.a.close()
        if hasattr(self.model,'client'): self.model.client.close()
        return report
