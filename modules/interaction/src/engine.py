"""C 模块独立核心。无 UI、操作系统截图或其他业务模块依赖。"""
import asyncio
import copy
import time
import uuid

VERSION = "c-lab/0.1"


class Interaction:
    def __init__(self, roles, model, capture, clock=time.monotonic, timeout=8):
        self.roles = {r['id']: copy.deepcopy(r) for r in roles}
        self.model, self.capture, self.clock, self.timeout = model, capture, clock, timeout
        self.messages, self.actions, self.jobs, self.events = [], [], [], []
        self.tasks = set()
        self.contexts = {}
        self.steps = []
        self.epoch, self.scene = 0, 0
        self.paused, self.scope, self.cooldown, self.last_observe = False, 'active-window', 10, float('-inf')
        self.auto_task = None

    def context(self, role):
        return copy.deepcopy([m for m in self.messages if m['audience'] == 'all' or role in m['audience']])

    def add(self, sender, text, audience='all', kind='text', image=None):
        m = dict(id=uuid.uuid4().hex, seq=len(self.messages)+1, sender=sender, text=text,
                 audience=audience, kind=kind, time=self.clock())
        if image:
            m['image'] = image
        self.messages.append(m)
        return m

    def track(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()  # 统一读取异常，具体业务错误由调用方转为状态
        task.add_done_callback(finished)
        return task

    async def bounded(self, coro):
        task = self.track(coro)
        try:
            done, _ = await asyncio.wait({task}, timeout=self.timeout)
            if not done:
                raise asyncio.TimeoutError()
            return task.result()
        finally:
            if not task.done():
                task.cancel()

    def cancel(self):
        self.epoch += 1
        for task in list(self.tasks):
            task.cancel()
        if self.auto_task:
            self.auto_task.cancel()

    async def reset(self):
        self.cancel()
        await asyncio.sleep(0)
        self.messages.clear()
        self.actions.clear()
        self.jobs.clear()
        self.events.clear()
        self.contexts.clear()
        self.steps.clear()
        self.paused = False
        self.scope, self.cooldown = 'active-window', 10
        self.last_observe = float('-inf')
        self.scene += 1

    async def close(self):
        self.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def submit(self, text, targets=None, kind='text', image=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('请输入 1–4000 字的内容')
        if kind not in ('text', 'story', 'image'):
            raise ValueError('不支持的输入类型')
        if targets is not None and (not targets or any(r not in self.roles for r in targets)):
            raise ValueError('目标角色不存在')
        self.cancel()  # 用户插话优先，停止旧轮次与自动接话
        audience = list(dict.fromkeys(targets)) if targets else 'all'
        event = dict(text=text, targets=targets, kind=kind, image=image)
        self.events.append(copy.deepcopy(event))
        self.add('director' if kind == 'story' else 'user', text, audience, kind, image)
        for role in (targets or self.roles):
            self.track(self.respond(role, audience, self.epoch))

    async def respond(self, role_id, audience, epoch, scene=None):
        role = self.roles.get(role_id)
        if not role:
            return
        job = dict(id=uuid.uuid4().hex, role=role_id, status='running', source_seq=len(self.messages))
        self.jobs.append(job)
        # 本次输出的全部接收者必须都有权看到引用来源，防止私聊在公开接话中泄漏。
        context = [m for m in self.context(role_id) if m['audience'] == 'all' or
                   (audience != 'all' and set(audience).issubset(set(m['audience'])))]
        self.contexts[role_id] = copy.deepcopy(context)
        try:
            result = await self.bounded(self.model.generate(copy.deepcopy(role), context))
            if epoch != self.epoch or role_id not in self.roles or (scene is not None and (scene != self.scene or self.paused)):
                job['status'] = 'stale'
                return
            text, action = result.get('text', ''), result.get('action')
            if text:
                self.add(role_id, str(text)[:4000], audience)
            if action:
                if action not in role.get('actions', []):
                    job['action_error'] = 'unknown-action'
                else:
                    self.actions.append(dict(id=uuid.uuid4().hex, role=role_id, action=action,
                                             status='requested', job=job['id']))
            job['status'] = 'done' if text or action else 'silent'
        except asyncio.CancelledError:
            job['status'] = 'cancelled'
        except asyncio.TimeoutError:
            job['status'] = 'timeout'
        except Exception:
            job['status'] = 'failed'
            job['error'] = '模型调用失败；请检查服务配置（响应与凭据不写入诊断）'

    def start_auto(self, rounds=3):
        if not 1 <= rounds <= 12:
            raise ValueError('自动交流轮次应为 1–12')
        self.cancel()
        epoch = self.epoch
        async def run():
            try:
                for i in range(rounds):
                    ids = list(self.roles)
                    if not ids or epoch != self.epoch:
                        break
                    await self.track(self.respond(ids[i % len(ids)], 'all', epoch))
                    await asyncio.sleep(.15)
            except asyncio.CancelledError:
                pass
        self.auto_task = self.track(run())

    def desktop(self, source='window', force=False):
        if source in ('self', 'action-feedback', 'bubble'):
            return False
        self.scene += 1
        if self.paused or (not force and self.clock()-self.last_observe < self.cooldown):
            return False
        self.last_observe = self.clock()
        scene, epoch, scope = self.scene, self.epoch, self.scope
        async def observe():
            job = dict(id=uuid.uuid4().hex, role='capture', status='running', scene=scene, scope=scope)
            self.jobs.append(job)
            try:
                shot = await self.bounded(self.capture.capture(scope, scene))
                if self.paused or scene != self.scene or epoch != self.epoch:
                    job['status'] = 'stale'
                    return
                self.add('environment', shot['description'], kind='desktop', image=shot['image'])
                job['status'] = 'done'
                for role in self.roles:
                    self.track(self.respond(role, 'all', epoch, scene))
            except asyncio.CancelledError:
                job['status'] = 'cancelled'
            except asyncio.TimeoutError:
                job['status'] = 'timeout'
            except Exception:
                job['status'] = 'failed'
        self.track(observe())
        return True

    def observation(self, paused, scope, cooldown):
        if scope not in ('active-window', 'desktop') or not 0 <= cooldown <= 300:
            raise ValueError('观察配置无效')
        self.paused, self.scope, self.cooldown = bool(paused), scope, cooldown
        self.scene += 1  # 配置变化使在途画面及视觉推理失效

    def feedback(self, action_id, status):
        if status not in ('accepted', 'rejected', 'completed'):
            raise ValueError('未知动作反馈')
        action = next((a for a in self.actions if a['id'] == action_id), None)
        if not action or action['status'] in ('rejected', 'completed'):
            raise ValueError('动作不存在或已结束')
        if status == 'completed' and action['status'] != 'accepted':
            raise ValueError('动作需要先接受再完成')
        action['status'] = status
        # 只有所属角色能看到执行结果，不触发新模型调用
        self.add('body', f"动作 {action['action']}：{status}", [action['role']], 'action-feedback')

    def remove(self, role):
        self.roles.pop(role, None)
        self.contexts.pop(role, None)

    def snapshot(self, debug=False):
        data = dict(version=VERSION, mode=self.model.mode, roles=copy.deepcopy(list(self.roles.values())),
                    messages=copy.deepcopy(self.messages), actions=copy.deepcopy(self.actions),
                    jobs=copy.deepcopy(self.jobs), observation=dict(paused=self.paused, scope=self.scope, cooldown=self.cooldown),
                    auto=bool(self.auto_task and not self.auto_task.done()))
        if debug:
            data.update(contexts=copy.deepcopy(self.contexts), inputs=copy.deepcopy(self.events), steps=copy.deepcopy(self.steps))
        return data

    def viewer(self, role):
        if role not in self.roles:
            raise ValueError('角色不存在')
        return dict(version=VERSION, messages=self.context(role))
