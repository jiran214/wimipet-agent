import asyncio
import base64
import json
import os
import urllib.request
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'


class FixedModel:
    mode = '模拟 · 固定响应，非真实理解'

    def __init__(self):
        self.behavior = {}

    async def generate(self, role, context):
        behavior = self.behavior.get(role['id'], 'normal')
        await asyncio.sleep(15 if behavior == 'timeout' else .6)
        if behavior == 'fail':
            raise RuntimeError('injected failure')
        if behavior == 'silent':
            return {}
        last = next((m for m in reversed(context) if m['kind'] != 'action-feedback'), None)
        topic = last['text'][:80] if last else '安静的桌面'
        templates = {
            'moss': f'我想把这件小事记在今天的心里。你说「{topic}」，我愿意陪你慢慢想。',
            'spark': f'「{topic}」让我有了一个小主意！我们给接下来留一点惊喜吧。',
            'orbit': f'关于「{topic}」，我先留意眼前的线索，再听听大家怎么想。',
        }
        return dict(text=templates.get(role['id'], f"{role['name']}：我听到了「{topic}」。"),
                    action=next(iter(role.get('actions', [])), None))


class FixtureCapture:
    async def capture(self, scope, scene):
        await asyncio.sleep(.25)
        return dict(description=f'固定模拟画面：雨天窗边的一盆绿植。范围 {scope}，场景 {scene}；没有采集真实桌面。',
                    image='desktop.png')


class OpenAICompatibleModel:
    """显式启用的 Chat Completions 兼容适配器；配置只从服务端环境读取。"""
    mode = '真实模型 · 尚需人工效果评审；桌面仍是固定样例'

    def __init__(self):
        self.url = os.environ.get('INTERACTION_MODEL_URL', '').rstrip('/')
        self.key = os.environ.get('INTERACTION_API_KEY', '')
        self.name = os.environ.get('INTERACTION_MODEL', '')
        if not self.url.startswith('https://') or not self.key or not self.name:
            raise ValueError('真实模式需要 HTTPS 完整 chat/completions URL、API key 和模型名')

    async def generate(self, role, context):
        return await asyncio.to_thread(self._request, role, context)

    def _request(self, role, context):
        prompt = ('你是一个陪伴角色。仅依据提供的可见经历，不编造历史或动作执行成功。'
                  '可选择安静。以 JSON 对象回复，字段 text（字符串，可空）、action（可用动作之一或 null）。'
                  + json.dumps(role, ensure_ascii=False))
        messages = [dict(role='system', content=prompt)]
        for m in context:
            content = [dict(type='text', text=json.dumps({k: v for k, v in m.items() if k != 'image'}, ensure_ascii=False))]
            if m.get('image') in ('share.png', 'desktop.png'):
                encoded = base64.b64encode((FIXTURES / m['image']).read_bytes()).decode()
                content.append(dict(type='image_url', image_url=dict(url=f'data:image/png;base64,{encoded}')))
            elif m.get('image', '').startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
                content.append(dict(type='image_url', image_url=dict(url=m['image'])))
            messages.append(dict(role='user', content=content))
        payload = json.dumps(dict(model=self.name, messages=messages, response_format={'type': 'json_object'})).encode()
        req = urllib.request.Request(self.url, data=payload, headers={
            'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'})
        # 不跟随重定向，防止凭据被意外转送；传输超时也限制被取消的底层线程寿命。
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
            result = json.loads(response.read(1_000_000))
        result = json.loads(result['choices'][0]['message']['content'])
        if not isinstance(result, dict) or not isinstance(result.get('text', ''), str):
            raise ValueError('invalid model response')
        return result
