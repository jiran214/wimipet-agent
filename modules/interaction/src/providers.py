import asyncio
import base64
import json
import os
import threading
import urllib.request


class FixedModel:
    mode = '模拟 · 固定响应，非真实理解'
    activeRequests = 0

    def __init__(self):
        self.behavior = {}

    async def generate(self, role, context, session):
        behavior = self.behavior.get(role['instanceId'], 'normal')
        await asyncio.sleep(15 if behavior == 'timeout' else .6)
        if behavior == 'fail':
            raise RuntimeError('injected failure')
        if behavior == 'silent':
            return {}
        last = next((m for m in reversed(context) if m['kind'] != 'action-feedback'), None)
        topic = (last['text'] or '你分享的这张图片')[:80] if last else '安静的桌面'
        tones = ['我愿意陪你慢慢想。', '我们给接下来留一点惊喜吧！', '我先留意眼前的线索，再听听大家怎么想。']
        tone = tones[sum(map(ord, role['instanceId'])) % len(tones)]
        # 只能使用当前加载能力，缺少动作时允许只说话。
        actions = role['supportedActions']
        action = next((a['actionId'] for a in actions if not a['loop']), None)
        return dict(text=f"{role['name']}：关于「{topic}」，{tone}", actionId=action)


class OpenAICompatibleModel:
    mode = '真实模型 · 未做效果验收；桌面仍为模拟样例'

    def __init__(self, resolver):
        self.resolver = resolver
        self.url = os.environ.get('INTERACTION_MODEL_URL', '').rstrip('/')
        self.key, self.name = os.environ.get('INTERACTION_API_KEY', ''), os.environ.get('INTERACTION_MODEL', '')
        self.activeRequests, self.lock = 0, threading.Lock()
        if not self.url.startswith('https://') or not self.key or not self.name:
            raise ValueError('真实模式需要 HTTPS 完整 chat/completions URL、API key 和模型名')

    async def generate(self, role, context, session):
        prompt = ('你是一个陪伴角色。仅依据可见经历，不编造历史或动作执行成功。允许保持安静。'
                  '以 JSON 对象回复：text（字符串，可空）、actionId（当前 supportedActions 之一或 null）。'
                  + json.dumps(role, ensure_ascii=False))
        messages = [dict(role='system', content=prompt)]
        for m in context:
            text = json.dumps(m, ensure_ascii=False)
            content = [dict(type='text', text=text)]
            for item in m['attachments']:
                raw = await self.resolver.resolve(item['contentRef'], session)
                encoded = base64.b64encode(raw).decode()
                content.append(dict(type='image_url', image_url=dict(url=f"data:{item['mime']};base64,{encoded}")))
            messages.append(dict(role='user', content=content))
        return await asyncio.to_thread(self._request, messages)

    def _request(self, messages):
        with self.lock:
            self.activeRequests += 1
        try:
            payload = json.dumps(dict(model=self.name, messages=messages, response_format={'type':'json_object'})).encode()
            request = urllib.request.Request(self.url, data=payload, headers={
                'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'})
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
                result = json.loads(response.read(1_000_000))
            result = json.loads(result['choices'][0]['message']['content'])
            if not isinstance(result, dict) or not isinstance(result.get('text', ''), str):
                raise ValueError('invalid model response')
            return result
        finally:
            with self.lock:
                self.activeRequests -= 1
