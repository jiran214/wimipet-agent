"""仅绑定回环地址的单用户调试工具；公开接口详见 docs/interaction。"""
import argparse
import asyncio
import base64
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from engine import Interaction
from providers import FixedModel, FixtureCapture, OpenAICompatibleModel


def main():
    parser = argparse.ArgumentParser(description='C 模块互动实验室（本地单用户调试）')
    parser.add_argument('--port', type=int, default=8763)
    parser.add_argument('--real', action='store_true')
    args = parser.parse_args()
    roles = json.loads((ROOT / 'fixtures/roles.json').read_text(encoding='utf-8'))
    engine = Interaction(roles, OpenAICompatibleModel() if args.real else FixedModel(), FixtureCapture())
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def route(path, data):
        if path == '/api/state':
            return engine.snapshot(debug=True)
        if path == '/api/view':
            return engine.viewer(data['role'])
        if path == '/api/send':
            image = data.get('image')
            if image and image not in ('share.png', 'desktop.png'):
                try:
                    prefix, encoded = image.split(',', 1)
                    raw = base64.b64decode(encoded, validate=True)
                    valid = (prefix == 'data:image/png;base64' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (prefix == 'data:image/jpeg;base64' and raw.startswith(b'\xff\xd8\xff'))
                    if not valid or len(raw) > 2*1024*1024:
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise ValueError('图片需要为不超过 2MB 的 PNG 或 JPEG')
            engine.submit(data['text'], data.get('targets'), data.get('kind', 'text'), image)
        elif path == '/api/auto':
            engine.start_auto(int(data.get('rounds', 3)))
        elif path == '/api/stop':
            engine.cancel()
        elif path == '/api/reset':
            await engine.reset()
            engine.roles = {r['id']: r.copy() for r in roles}
            if isinstance(engine.model, FixedModel):
                engine.model.behavior.clear()
        elif path == '/api/observe':
            engine.desktop(force=True)
        elif path == '/api/window':
            engine.desktop()
        elif path == '/api/observation':
            engine.observation(bool(data['paused']), data['scope'], float(data['cooldown']))
        elif path == '/api/feedback':
            engine.feedback(data['id'], data['status'])
        elif path == '/api/role':
            role = engine.roles.get(data['id'])
            if not role or len(data.get('persona', '')) > 2000:
                raise ValueError('无效角色或人设过长')
            engine.cancel()
            role['persona'] = data['persona']
            if isinstance(engine.model, FixedModel):
                behavior = data.get('behavior', 'normal')
                if behavior not in ('normal', 'fail', 'timeout', 'silent'):
                    raise ValueError('未知模拟行为')
                engine.model.behavior[role['id']] = behavior
        elif path == '/api/remove':
            engine.remove(data['id'])
        else:
            raise ValueError('未知接口')
        engine.steps.append(dict(command=path, input=data, time=engine.clock()))
        return engine.snapshot(debug=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # 不将消息输入写入访问日志

        def reply(self, status, body, mime='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/state':
                value = asyncio.run_coroutine_threadsafe(route(self.path, {}), loop).result(3)
                return self.reply(200, json.dumps(value, ensure_ascii=False).encode())
            files = {'/': (ROOT/'demo/index.html', 'text/html; charset=utf-8'),
                     '/app.js': (ROOT/'demo/app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': (ROOT/'demo/style.css', 'text/css; charset=utf-8'),
                     '/share.png': (ROOT/'fixtures/share.png', 'image/png'),
                     '/desktop.png': (ROOT/'fixtures/desktop.png', 'image/png')}
            item = files.get(self.path)
            if not item:
                return self.reply(404, b'{}')
            self.reply(200, item[0].read_bytes(), item[1])

        def do_POST(self):
            # 浏览器跨源不能写入本地会话，且不接收表单式请求。
            if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}'):
                return self.reply(403, b'{}')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 3*1024*1024 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('请求必须为不超过 3MB 的 JSON')
                data = json.loads(self.rfile.read(size))
                result = asyncio.run_coroutine_threadsafe(route(self.path, data), loop).result(3)
                self.reply(200, json.dumps(result, ensure_ascii=False).encode())
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())
            except Exception:
                self.reply(500, json.dumps({'error': '处理失败，请重试或恢复样例'}, ensure_ascii=False).encode())

    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    try:
        server = LocalServer(('127.0.0.1', args.port), Handler)
    except OSError:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(3)
        loop.close()
        raise SystemExit(f'无法监听端口 {args.port}，请停止旧服务或使用 --port 指定其他端口。')
    print(f'互动实验室 http://127.0.0.1:{args.port} | {engine.model.mode}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        asyncio.run_coroutine_threadsafe(engine.close(), loop).result(3)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(3)
        loop.close()


if __name__ == '__main__':
    main()
