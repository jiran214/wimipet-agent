"""回环单用户实验室；消息来自正式 getView，诊断单独读取。"""
import argparse
import asyncio
import base64
import copy
import json
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from public import create_interaction, PROTOCOL, IMPLEMENTATION, ProtocolError
from resources import MemoryResources
from providers import FixedModel, OpenAICompatibleModel
from dependencies import SimulatedBody, SimulatedCapture


class Lab:
    def __init__(self, real=False):
        self.resources, self.body = MemoryResources(), SimulatedBody()
        self.capture = SimulatedCapture(self.resources)
        self.model = OpenAICompatibleModel(self.resources) if real else FixedModel()
        self.core = create_interaction(model=self.model, resolver=self.resources, body=self.body, capture=self.capture)
        self.packages = {}

    async def command(self, kind, payload=None, rid=None):
        return await self.core.dispatch(dict(protocol=PROTOCOL, sessionId=self.core.sessionId,
            requestId=rid or uuid.uuid4().hex, expiresAtMs=int(time.time()*1000)+60000,
            type=kind, payload=payload or {}))

    async def defaults(self):
        package = json.loads((ROOT/'fixtures/character-package.v1.json').read_text(encoding='utf-8'))
        for instance in ('instance-one', 'instance-two', 'instance-three'):
            current = copy.deepcopy(package)
            if instance == 'instance-three':
                current['packageId'] = 'interaction-demo-orbit'
                current['character'].update(name='星轨', persona='沉静理性，关注细节，表达关心时简短克制。')
            self.packages[instance] = current
            result = await self.command('RegisterCharacter', dict(package=current, loaded=self.body.load(instance,current)))
            if not result['payload']['accepted']:
                raise RuntimeError('模拟角色配置未被 C 接受')
        return await self.command('SetObservationPolicy', dict(policyRevision=self.core.policyRevision+1,
            paused=False, scope='active-window', cooldownMs=10000, excludedRegions=[]))

    async def route(self, path, data):
        if path == '/api/state':
            result = self.core.diagnostics()
            result.pop('view')  # 管理员上下文与正式消息源分开
            result['capabilities'] = self.core.getCapabilities()
            result['modelBehaviors'] = copy.deepcopy(getattr(self.model,'behavior',{}))
            result['captureBehavior'] = self.capture.behavior
            return result
        if path == '/api/view':
            return self.core.getView(data or dict(type='user'))
        if path == '/api/dispatch':
            return await self.core.dispatch(data)
        if path == '/api/prepare':
            image = data['image']
            if image == 'share.png':
                raw, mime, name = (ROOT/'fixtures/share.png').read_bytes(), 'image/png', '固定分享样例.png'
            else:
                prefix, encoded = image.split(',',1)
                raw, mime, name = base64.b64decode(encoded,validate=True), prefix.removeprefix('data:').removesuffix(';base64'), '用户分享图片'
            attachment = self.resources.put(raw,mime,name,self.core.sessionId)
            return dict(attachment=attachment,previewUrl='/resource/'+attachment['contentRef'],sessionId=self.core.sessionId)
        if path == '/api/release':
            self.resources.release(data['contentRef'],self.core.sessionId)
            return dict(released=True)
        if path == '/api/send':
            attachments = []
            image = data.get('image')
            if image:
                if image in ('share.png','desktop.png'):
                    raw, mime, name = (ROOT/'fixtures'/image).read_bytes(), 'image/png', image
                else:
                    prefix, encoded = image.split(',',1)
                    raw, mime, name = base64.b64decode(encoded,validate=True), prefix.removeprefix('data:').removesuffix(';base64'), '用户分享图片'
                attachments.append(self.resources.put(raw,mime,name,self.core.sessionId))
            receipt = await self.command('UserInput', dict(kind=data.get('kind','message'), recipient=data.get('recipient','all'),
                text=data.get('text',''), attachments=attachments))
            if not receipt['payload']['accepted']:
                for item in attachments:
                    self.resources.release(item['contentRef'],self.core.sessionId)
            return receipt
        if path == '/api/auto':
            receipt = await self.command('StartAutoConversation',dict(rounds=data.get('rounds',3)))
        elif path == '/api/stop':
            receipt = await self.command('StopConversation')
        elif path == '/api/cancel':
            receipt = await self.command('CancelRequest',dict(targetRequestId=data['requestId']))
        elif path == '/api/reset':
            receipt = await self.command('ResetSession')
            self.body.commands.clear()
            self.capture.behavior = 'normal'
            if isinstance(self.model,FixedModel):
                self.model.behavior.clear()
            await self.defaults()
        elif path in ('/api/observe','/api/window'):
            receipt = await self.command('ObserveDesktop',dict(force=path=='/api/observe',source='window'))
        elif path == '/api/observation':
            self.capture.behavior = data.get('behavior','normal')
            receipt = await self.command('SetObservationPolicy',dict(policyRevision=self.core.policyRevision+1,
                paused=data['paused'],scope=data['scope'],cooldownMs=data['cooldownMs'],excludedRegions=[]))
        elif path == '/api/feedback':
            receipt = await self.core.dispatch(self.body.event(data['requestId'],data['status']))
        elif path == '/api/role':
            instance = data['instanceId']
            if instance not in self.core.roles:
                raise ProtocolError('not-found','角色不存在')
            package = copy.deepcopy(self.packages[instance])
            package['revision'] += 1
            package['character']['persona'] = data['persona']
            receipt = await self.command('RegisterCharacter',dict(package=package,loaded=self.body.load(instance,package)))
            if receipt['payload']['accepted']:
                self.packages[instance] = package
                if isinstance(self.model,FixedModel):
                    self.model.behavior[instance] = data.get('behavior','normal')
        elif path == '/api/remove':
            receipt = await self.command('RemoveCharacter',dict(instanceId=data['instanceId']))
        else:
            raise ProtocolError('not-found','未知调试接口')
        self.core.steps.append(dict(command=path,input=copy.deepcopy(data),atMs=int(time.time()*1000),
                                    accepted=receipt['payload']['accepted']))
        return receipt


def main():
    parser = argparse.ArgumentParser(description='C 互动实验室 / wimipet/1')
    parser.add_argument('--port',type=int,default=8763)
    parser.add_argument('--real',action='store_true')
    args = parser.parse_args()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever,daemon=True)
    thread.start()
    async def setup():
        lab = Lab(args.real)
        await lab.defaults()
        return lab
    lab = asyncio.run_coroutine_threadsafe(setup(),loop).result(12)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def reply(self,status,body,mime='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ('/api/state','/api/view'):
                result = asyncio.run_coroutine_threadsafe(lab.route(self.path,{}),loop).result(3)
                return self.reply(200,json.dumps(result,ensure_ascii=False).encode())
            if self.path.startswith('/resource/'):
                ref = self.path.removeprefix('/resource/')
                async def read():
                    raw = await lab.resources.resolve(ref,lab.core.sessionId)
                    meta = await lab.resources.describe(ref,lab.core.sessionId)
                    return raw,meta['mime']
                try:
                    raw,mime = asyncio.run_coroutine_threadsafe(read(),loop).result(3)
                    return self.reply(200,raw,mime)
                except Exception:
                    return self.reply(404,b'{}')
            files = {'/':(ROOT/'demo/index.html','text/html; charset=utf-8'),
                     '/app.js':(ROOT/'demo/app.js','text/javascript; charset=utf-8'),
                     '/style.css':(ROOT/'demo/style.css','text/css; charset=utf-8'),
                     '/desktop.png':(ROOT/'fixtures/desktop.png','image/png')}
            item = files.get(self.path)
            if not item:
                return self.reply(404,b'{}')
            self.reply(200,item[0].read_bytes(),item[1])

        def do_POST(self):
            port = self.server.server_address[1]
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
                return self.reply(403,b'{}')
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 3*1048576 or self.headers.get('Content-Type') != 'application/json':
                    raise ProtocolError('invalid-input','请求需要 3MiB 内 JSON')
                data = json.loads(self.rfile.read(size))
                result = asyncio.run_coroutine_threadsafe(lab.route(self.path,data),loop).result(12)
                self.reply(200,json.dumps(result,ensure_ascii=False).encode())
            except ProtocolError as exc:
                self.reply(400,json.dumps(dict(error=exc.error),ensure_ascii=False).encode())
            except Exception:
                self.reply(400,json.dumps(dict(error=dict(code='invalid-input',message='调试请求无效',retryable=False)),ensure_ascii=False).encode())

    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def server_bind(self):
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()

    try:
        server = LocalServer(('127.0.0.1',args.port),Handler)
    except OSError:
        asyncio.run_coroutine_threadsafe(lab.core.close(),loop).result(3)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(3)
        loop.close()
        raise SystemExit(f'端口 {args.port} 不可用，请停止旧服务或指定 --port。')
    port = server.server_address[1]
    print(f'{IMPLEMENTATION} {PROTOCOL} | http://127.0.0.1:{port} | instance={lab.core.sourceInstanceId} | {lab.model.mode}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        result = asyncio.run_coroutine_threadsafe(lab.core.close(),loop).result(4)
        print('关闭报告 '+json.dumps(result),flush=True)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(3)
        loop.close()


if __name__ == '__main__':
    main()
