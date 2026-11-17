import asyncio
import base64
import json
import os
import threading
from openai import OpenAI


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

    def __init__(self, resolver, config=None, client=None):
        self.resolver = resolver
        config = config if config is not None else os.environ
        self.url = config.get('OPENAI_BASE_URL') or config.get('INTERACTION_MODEL_URL', '').removesuffix('/chat/completions')
        self.key = config.get('OPENAI_API_KEY') or config.get('INTERACTION_API_KEY', '')
        self.name = config.get('OPENAI_MODEL') or config.get('INTERACTION_MODEL', '')
        self.activeRequests, self.lock = 0, threading.Lock()
        if not self.url.startswith('https://') or not self.key or not self.name:
            raise ValueError('请在本地 .env 配置模型服务地址、密钥和模型名')
        self.client = client or OpenAI(api_key=self.key, base_url=self.url,
            timeout=float(config.get('MODEL_TIMEOUT_SECONDS', 60)), max_retries=0)
        self.mode = '真实 AI 对话'

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
            completion = self.client.chat.completions.create(model=self.name, messages=messages,
                response_format={'type': 'json_object'}, max_tokens=1024)
            result = json.loads(completion.choices[0].message.content)
            if not isinstance(result, dict) or not isinstance(result.get('text', ''), str):
                raise ValueError('invalid model response')
            return result
        finally:
            with self.lock:
                self.activeRequests -= 1
