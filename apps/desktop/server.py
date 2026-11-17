import argparse
import asyncio
import base64
import json
import mimetypes
import socket
import subprocess
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from runtime import ROOT, Runtime

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--port',type=int)
    args=parser.parse_args()
    loop=asyncio.new_event_loop()
    thread=threading.Thread(target=loop.run_forever,daemon=True);thread.start()
    async def setup():
        r=Runtime(args.offline);await r.start();return r
    runtime=asyncio.run_coroutine_threadsafe(setup(),loop).result(30)
    port=args.port or int(runtime.config.get('APP_PORT',8760))
    async def route(path,data):
        if path=='/api/state': return runtime.state()
        if path=='/api/dispatch': return await runtime.dispatch(data)
        if path=='/api/package': return dict(instanceId=await runtime.load(data))
        if path=='/api/release':
            runtime.release_draft(data['contentRef'])
            return dict(released=True)
        if path=='/api/prepare':
            if data.get('sessionId')!=runtime.core.sessionId: raise ValueError('会话已变化')
            if sum(len(entry[1]) for entry in runtime.resources.entries.values())>=64*1048576: raise ValueError('本次会话图片容量已满')
            raw=base64.b64decode(data['dataBase64'],validate=True)
            if not 0<len(raw)<=2*1048576: raise ValueError('图片须在 2MiB 内')
            # Actual decode, not MIME-only acceptance.
            from desktop_world.package_v1 import decode_png
            if data['mime']=='image/png':
                await asyncio.to_thread(decode_png,raw,max_dimension=8192,max_pixels=16*1048576)
            item=runtime.resources.put(raw,data['mime'],data['name'],runtime.core.sessionId)
            return item
        if path=='/api/auto': return await runtime.core.dispatch(runtime.envelope('StartAutoConversation',dict(rounds=3)))
        if path=='/api/stop': return await runtime.core.dispatch(runtime.envelope('StopConversation',{}))
        raise ValueError('未知操作')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,status,body,mime='application/json; charset=utf-8'):
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'): return self.reply(403,b'{}')
            path=urllib.parse.urlparse(self.path).path
            try:
                if path=='/api/state':
                    value=asyncio.run_coroutine_threadsafe(route(path,{}),loop).result(5)
                    return self.reply(200,json.dumps(value,ensure_ascii=False).encode())
                if path.startswith('/resource/'):
                    ref=path.split('/')[-1]
                    async def resource():
                        if ref.startswith('frame-'):
                            return runtime.a.renderFrame(ref[6:]),'image/png'
                        item=await runtime.resources.describe(ref,runtime.core.sessionId)
                        return await runtime.resources.resolve(ref,runtime.core.sessionId),item['mime']
                    raw,mime=asyncio.run_coroutine_threadsafe(resource(),loop).result(5)
                    return self.reply(200,raw,mime)
                if path=='/': file=ROOT/'apps/desktop/web/index.html'
                elif path.startswith('/web/'): file=ROOT/'apps/desktop/web'/path[5:]
                elif path.startswith('/ui/'): file=ROOT/'modules/companion-ui/src'/path[4:]
                else: return self.reply(404,b'{}')
                allowed=[(ROOT/'apps/desktop/web').resolve(),(ROOT/'modules/companion-ui/src').resolve()]
                if not any(file.resolve().is_relative_to(r) for r in allowed): return self.reply(403,b'{}')
                return self.reply(200,file.read_bytes(),{'.js':'text/javascript','.css':'text/css','.html':'text/html; charset=utf-8'}.get(file.suffix,'application/octet-stream'))
            except Exception: self.reply(404,b'{}')
        def do_POST(self):
            try:
                if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'): return self.reply(403,b'{}')
                if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{port}',f'http://localhost:{port}'): return self.reply(403,b'{}')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32*1048576 or self.headers.get('Content-Type')!='application/json': raise ValueError()
                data=json.loads(self.rfile.read(size))
                if self.path=='/api/exit':
                    self.reply(200,b'{"closed":true}')
                    threading.Thread(target=server.shutdown,daemon=True).start()
                    return
                value=asyncio.run_coroutine_threadsafe(route(self.path,data),loop).result(15)
                self.reply(200,json.dumps(value,ensure_ascii=False).encode())
            except Exception: self.reply(400,json.dumps(dict(error='操作未完成，请检查输入或稍后重试'),ensure_ascii=False).encode())
    class Server(ThreadingHTTPServer):
        allow_reuse_address=False
        def server_bind(self):
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'): self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()
    studio=None
    try:
        server=Server(('127.0.0.1',port),Handler)
        import os
        studio=subprocess.Popen(['node',str(ROOT/'modules/character-studio/server.mjs')],cwd=ROOT,
            env={**os.environ,'PORT':str(runtime.config.get('STUDIO_PORT',4312))},stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        # The child prints its own identity only after a successful bind; never silently use an old server.
        ready=studio.stdout.readline()
        if not ready or studio.poll() is not None:
            raise RuntimeError('角色制作台启动失败，请检查 STUDIO_PORT 是否被占用')
        print(f'Wimipet http://127.0.0.1:{port} | Ctrl+C 退出',flush=True)
        if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{port}')
        server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        if 'server' in locals(): server.server_close()
        if studio:
            studio.terminate();studio.wait(timeout=5)
        asyncio.run_coroutine_threadsafe(runtime.close(),loop).result(70)
        asyncio.run_coroutine_threadsafe(loop.shutdown_default_executor(),loop).result(70)
        loop.call_soon_threadsafe(loop.stop);thread.join(5);loop.close()

if __name__=='__main__': main()
