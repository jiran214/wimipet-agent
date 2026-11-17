"""C 唯一业务状态。公开调用须在创建实例的 asyncio 线程运行。"""
import asyncio
import copy
import inspect
import time
import uuid

from protocol import (PROTOCOL, IMPLEMENTATION, MAX_IMAGE, ACTION_TRANSITIONS,
                      ProtocolError, require, identifier, integer, fingerprint,
                      envelope, character_snapshot, policy, upstream_error, rectangle)


class Interaction:
    def __init__(self, model, resolver=None, body=None, capture=None,
                 clock=time.monotonic, epoch_ms=lambda: int(time.time()*1000),
                 timeout=8, dedup_capacity=2048):
        self.loop = asyncio.get_running_loop()
        self.model, self.resolver, self.body, self.capture = model, resolver, body, capture
        self.clock, self.epoch_ms, self.timeout = clock, epoch_ms, timeout
        self.sessionId, self.sourceInstanceId = uuid.uuid4().hex, uuid.uuid4().hex
        self.roles, self.messages, self.actions, self.jobs = {}, [], [], []
        self.contexts, self.steps, self.tasks, self.listeners = {}, [], {}, {}
        self.requests, self.result_events = {}, {}
        self.dedup_capacity = dedup_capacity
        self.revision, self.sourceSeq, self.generation, self.scene = 0, 0, 0, 0
        self.cancelled, self.closed, self.auto_task = set(), False, None
        self.confirmedPolicy, self.requestedPolicy, self.policyError = None, None, None
        self.policyRevision, self.analysisPaused = 0, True
        self.last_observe = float('-inf')
        self.listenerErrors = 0
        self.resourceCleanupPending = set()

    def _thread(self):
        require(asyncio.get_running_loop() is self.loop, '调用须由宿主转发到 C 的 asyncio 循环', 'conflict')

    def getCapabilities(self):
        return dict(protocol=PROTOCOL, module='C', implementationVersion=IMPLEMENTATION,
                    capabilities=['user-input', 'image-only', 'message-view', 'message-subscription',
                                  'private-context', 'action-lifecycle', 'request-dedup', 'request-cancel',
                                  'observation-policy-sync', 'session-reset'],
                    limits=dict(textCodePoints=4000, personaCodePoints=20000, attachments=1,
                                attachmentBytes=MAX_IMAGE, attachmentMimes=['image/png', 'image/jpeg'],
                                requestRecords=self.dedup_capacity),
                    dependencies=dict(resources=self.resolver is not None, body=self.body is not None,
                                      capture=self.capture is not None),
                    mode=getattr(self.model, 'mode', 'injected'))

    def _viewer(self, viewer):
        require(isinstance(viewer, dict) and viewer.get('type') in ('user', 'character'), 'viewer 必须明确用户或角色')
        if viewer['type'] == 'character':
            require(viewer.get('instanceId') in self.roles, '运行角色不存在', 'not-found')

    @staticmethod
    def _visible(audience, viewer):
        return viewer['type'] == 'user' or audience == 'all' or viewer['instanceId'] in audience

    def getView(self, viewer):
        self._thread()
        self._viewer(viewer)
        role = viewer.get('instanceId')
        return copy.deepcopy(dict(sessionId=self.sessionId, revision=self.revision,
            messages=[m for m in self.messages if self._visible(m['audience'], viewer)],
            processing=dict(tasks=[j for j in self.jobs if self._visible(j['audience'], viewer)],
                actions=[a for a in self.actions if viewer['type'] == 'user' or a['instanceId'] == role],
                auto=bool(self.auto_task and not self.auto_task.done()),
                observation=dict(confirmed=self.confirmedPolicy, requested=self.requestedPolicy,
                                 analysisPaused=self.analysisPaused, error=self.policyError))))

    def subscribe(self, viewer, listener):
        self._thread()
        self._viewer(viewer)
        require(callable(listener), 'listener 必须可调用')
        token = uuid.uuid4().hex
        self.listeners[token] = (copy.deepcopy(viewer), listener)
        def unsubscribe():
            self._thread()
            self.listeners.pop(token, None)
        return unsubscribe

    def _changed(self):
        self.revision += 1
        for token in list(self.listeners):
            def deliver(key=token):
                pair = self.listeners.get(key)
                if pair and not self.closed:
                    try:
                        pair[1](self.getView(pair[0]))
                    except Exception:
                        self.listenerErrors += 1
            self.loop.call_soon(deliver)

    def _event(self, kind, request_id, payload):
        self.sourceSeq += 1
        return dict(protocol=PROTOCOL, sessionId=self.sessionId, requestId=request_id,
                    eventId=uuid.uuid4().hex, source='C', sourceInstanceId=self.sourceInstanceId,
                    sourceSeq=self.sourceSeq, atMs=self.epoch_ms(), type=kind, payload=copy.deepcopy(payload))

    def _reply(self, request, accepted, error=None, **data):
        payload = dict(accepted=accepted, **data)
        if error:
            payload['error'] = error
        result = self._event('InputReceipt' if request.get('type') == 'UserInput' else 'Receipt', request.get('requestId', ''), payload)
        result['sessionId'] = request.get('sessionId', self.sessionId)
        result['expiresAtMs'] = request.get('expiresAtMs', self.epoch_ms())
        return result

    async def dispatch(self, request):
        """JSON 信封 -> 结构化响应。ActionResult 按 eventId 去重。"""
        self._thread()
        row = None
        try:
            envelope(request)
            require(not self.closed, '会话已关闭', 'unavailable')
            required = request.get('requiredCapabilities', [])
            require(isinstance(required,list) and all(c in self.getCapabilities()['capabilities'] for c in required), '未知必需能力', 'unsupported-capability')
            require(request['sessionId'] == self.sessionId, '会话已重置', 'stale')
            remaining = (request['expiresAtMs'] - self.epoch_ms()) / 1000
            require(remaining > 0, '请求已过期', 'stale')
            if request['type'] == 'ActionResult':
                return self._action_result(request)
            key, signature = (request['sessionId'], request['requestId']), fingerprint(request)
            previous = self.requests.get(key)
            if previous:
                require(previous['signature'] == signature, '同一 requestId 的内容冲突', 'conflict')
                require(self.clock() < previous['deadline'], '请求截止时间已到', 'stale')
                return copy.deepcopy(await asyncio.shield(previous['future']))
            self.requests = {k: v for k, v in self.requests.items() if v['deadline'] > self.clock() or not v['future'].done()}
            require(len(self.requests) < self.dedup_capacity, '有效去重记录已满，请稍后重试', 'unavailable')
            row = dict(signature=signature, deadline=self.clock()+remaining, future=self.loop.create_future())
            self.requests[key] = row
            result = await self._execute(request, row['deadline'])
        except ProtocolError as exc:
            result = self._reply(request if isinstance(request, dict) else {}, False, exc.error)
        except asyncio.CancelledError:
            result = self._reply(request, False, dict(code='cancelled', message='请求已取消', retryable=True))
        except Exception:
            result = self._reply(request if isinstance(request, dict) else {}, False,
                                 dict(code='internal', message='处理失败，未记录私有响应', retryable=True))
        if row and not row['future'].done():
            if request['sessionId'] == self.sessionId or request['type'] == 'ResetSession':
                payload = copy.deepcopy(request['payload'])
                if request['type'] == 'RegisterCharacter':
                    loaded, package = payload.get('loaded'), payload.get('package')
                    loaded = loaded if isinstance(loaded,dict) else {}
                    package = package if isinstance(package,dict) else {}
                    payload = dict(instanceId=loaded.get('instanceId'),packageId=package.get('packageId'),revision=package.get('revision'))
                self.steps.append(dict(sessionId=request['sessionId'],requestId=request['requestId'],
                    type=request['type'],atMs=self.epoch_ms(),payload=payload,result=copy.deepcopy(result['payload'])))
            row['future'].set_result(copy.deepcopy(result))
        return result

    async def _execute(self, req, deadline):
        kind, p, rid = req['type'], req['payload'], req['requestId']
        required = p.get('requiredCapabilities', [])
        require(isinstance(required, list) and all(c in self.getCapabilities()['capabilities'] for c in required), '未知必需能力', 'unsupported-capability')
        if kind == 'RegisterCharacter':
            role = character_snapshot(p.get('package'), p.get('loaded'))
            self.roles[role['instanceId']] = role
            self.generation += 1
            self._changed()
        elif kind == 'UserInput':
            await self._input(req, deadline)
        elif kind == 'StartAutoConversation':
            rounds = p.get('rounds', 3)
            require(integer(rounds) and 1 <= rounds <= 12, '自动轮次需要 1–12 整数')
            require(bool(self.roles), '没有参与角色', 'not-found')
            self._cancel_dialogue()
            generation, session = self.generation, self.sessionId
            async def auto():
                try:
                    for index in range(rounds):
                        ids = list(self.roles)
                        if not ids or generation != self.generation or session != self.sessionId:
                            break
                        await self._respond(ids[index % len(ids)], 'all', rid, deadline, generation, session)
                finally:
                    if self.auto_task is asyncio.current_task():
                        self.auto_task = None
                    if session == self.sessionId:
                        self._changed()
            self.auto_task = self._track(auto(), rid, 'dialogue')
            self._changed()
        elif kind == 'CancelRequest':
            target = p.get('targetRequestId')
            require(identifier(target), '缺少取消目标')
            require((self.sessionId, target) in self.requests, '取消目标不存在', 'not-found')
            self.cancelled.add((self.sessionId, target))
            for task, (task_rid, _) in list(self.tasks.items()):
                if task_rid == target:
                    task.cancel()
            self._changed()
        elif kind == 'StopConversation':
            self._cancel_dialogue()
            self._changed()
        elif kind == 'SetObservationPolicy':
            await self._set_policy(req, deadline)
        elif kind == 'ObserveDesktop':
            self._observe(req, deadline)
        elif kind == 'RemoveCharacter':
            instance = p.get('instanceId')
            require(instance in self.roles, '角色不存在', 'not-found')
            self.roles.pop(instance)
            self.contexts.pop(instance, None)
            self.listeners = {k: v for k, v in self.listeners.items() if v[0].get('instanceId') != instance}
            self._changed()
        elif kind == 'ResetSession':
            cleanup_error = await self._reset(deadline)
            details = dict(newSessionId=self.sessionId)
            if cleanup_error:
                details['cleanupError'] = cleanup_error
            return self._reply(req, True, **details)
        else:
            raise ProtocolError('unsupported-capability', '未实现该命令')
        return self._reply(req, True)

    def _track(self, coro, rid, category):
        task = self.loop.create_task(coro)
        self.tasks[task] = (rid, category)
        def done(completed):
            self.tasks.pop(completed, None)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)
        return task

    async def _bounded(self, coro, deadline, rid, category):
        if self.clock() >= deadline:
            if inspect.iscoroutine(coro):
                coro.close()
            raise ProtocolError('timeout', '截止时间已到，未启动下游请求', True)
        task = self._track(coro, rid, category)
        try:
            wait = max(0, min(self.timeout, deadline-self.clock()))
            done, _ = await asyncio.wait({task}, timeout=wait)
            if not done:
                raise ProtocolError('timeout', '请求超过截止时间', True)
            return task.result()
        finally:
            if not task.done():
                task.cancel()

    def _valid(self, session, rid, deadline, generation=None, visual=None):
        if session != self.sessionId or self.closed or self.clock() >= deadline:
            return False
        if (session, rid) in self.cancelled or (generation is not None and generation != self.generation):
            return False
        return visual is None or (not self.analysisPaused and self.confirmedPolicy and
                                  visual == (self.scene, self.confirmedPolicy['policyRevision']))

    def _cancel_dialogue(self):
        self.generation += 1
        for task, (_, category) in list(self.tasks.items()):
            if category == 'dialogue':
                task.cancel()

    def _add(self, speaker, text, audience='all', kind='message', attachments=None, rid=None):
        message = dict(messageId=uuid.uuid4().hex, seq=len(self.messages)+1, atMs=self.epoch_ms(),
                       speaker=copy.deepcopy(speaker), audience=copy.deepcopy(audience), kind=kind,
                       text=text, attachments=copy.deepcopy(attachments or []))
        if rid:
            message['requestId'] = rid
        self.messages.append(message)
        self._changed()
        return message

    async def _attachments(self, attachments, deadline, rid, session):
        require(isinstance(attachments, list) and len(attachments) <= 1, '最多一个附件')
        if not attachments:
            return []
        require(self.resolver is not None and callable(getattr(self.resolver,'resolve',None)), '缺少附件资源桥', 'unsupported-capability')
        item = attachments[0]
        require(isinstance(item, dict) and all(identifier(item.get(k)) for k in ('attachmentId', 'name', 'contentRef')), '附件字段无效')
        ref = item['contentRef']
        require(not any(c in ref for c in ('/', '\\')) and not ref.startswith(('data:', 'blob:', 'http:', 'https:', 'file:')), 'contentRef 必须为桥接不透明引用')
        require(item.get('mime') in ('image/png', 'image/jpeg'), '仅支持 PNG/JPEG', 'unsupported-capability')
        require(integer(item.get('sizeBytes')) and 0 < item['sizeBytes'] <= MAX_IMAGE, '附件超过 2MiB 或大小无效')
        raw = await self._bounded(self.resolver.resolve(ref, session), deadline, rid, 'resource')
        require(isinstance(raw, bytes) and len(raw) == item['sizeBytes'], '资源字节与声明不符')
        require((item['mime'] == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or
                (item['mime'] == 'image/jpeg' and raw.startswith(b'\xff\xd8\xff')), '资源类型与声明不符')
        return copy.deepcopy(attachments)

    async def _input(self, req, deadline):
        p, rid, session = req['payload'], req['requestId'], self.sessionId
        text, attachments, recipient = p.get('text', ''), p.get('attachments', []), p.get('recipient')
        require(isinstance(text, str) and len(text) <= 4000, '正文最多 4000 Unicode 码点')
        require(p.get('kind') in ('message', 'story-event'), '未知输入类型')
        require(recipient == 'all' or isinstance(recipient, str) and recipient in self.roles, '接收角色不存在', 'not-found')
        require(bool(self.roles), '没有参与角色', 'not-found')
        attachments = await self._attachments(attachments, deadline, rid, session)
        require(text.strip() or attachments, '需要非空正文或附件')
        require((session, rid) not in self.cancelled, '输入已取消', 'cancelled')
        require(self._valid(session, rid, deadline), '输入已取消或过期', 'stale')
        require(recipient == 'all' or recipient in self.roles, '接收角色已移除', 'not-found')
        self._cancel_dialogue()
        audience = 'all' if recipient == 'all' else [recipient]
        speaker_type = 'director' if p['kind'] == 'story-event' else 'user'
        self._add(dict(type=speaker_type, name='导演' if speaker_type == 'director' else '你'), text,
                  audience, p['kind'], attachments, rid)
        for role in (list(self.roles) if recipient == 'all' else [recipient]):
            self._track(self._respond(role, audience, rid, deadline, self.generation, session), rid, 'dialogue')

    def _context(self, role, audience):
        viewer = dict(type='character', instanceId=role)
        return copy.deepcopy([m for m in self.messages if self._visible(m['audience'], viewer) and
            (m['audience'] == 'all' or (audience != 'all' and set(audience).issubset(m['audience'])))])

    async def _respond(self, instance, audience, rid, deadline, generation, session, visual=None):
        role = copy.deepcopy(self.roles.get(instance))
        if not role or not self._valid(session, rid, deadline, generation, visual):
            return
        job = dict(taskId=uuid.uuid4().hex, requestId=rid, instanceId=instance, audience=audience, status='running')
        self.jobs.append(job)
        context = self._context(instance, audience)
        self.contexts[instance] = copy.deepcopy(context)
        self._changed()
        try:
            result = await self._bounded(self.model.generate(role, context, session), deadline, rid, 'dialogue')
            if not self._valid(session, rid, deadline, generation, visual) or instance not in self.roles or self.roles[instance] != role:
                job['status'] = 'stale'
                return
            require(isinstance(result, dict) and isinstance(result.get('text', ''), str) and len(result.get('text', '')) <= 4000, '模型回复格式无效')
            text, action = result.get('text', ''), result.get('actionId')
            if action:
                require(any(a['actionId'] == action for a in role['supportedActions']), '模型选择了未加载动作', 'unsupported-capability')
                require(self.body is not None and callable(getattr(self.body,'send',None)), '缺少身体执行依赖', 'unsupported-capability')
            if text:
                self._add(dict(type='character', instanceId=instance, name=role['name']), text, audience, rid=rid)
            if action:
                self._start_action(role, action, rid, deadline, session)
            job['status'] = 'done' if text or action else 'silent'
        except asyncio.CancelledError:
            job['status'] = 'cancelled'
            job['error'] = dict(code='cancelled', message='模型任务已取消', retryable=True)
        except ProtocolError as exc:
            job['status'] = 'timeout' if exc.error['code'] == 'timeout' else 'failed'
            job['error'] = exc.error
        except Exception:
            job['status'] = 'failed'
            job['error'] = dict(code='unavailable', message='模型调用失败，私有响应已省略', retryable=True)
        finally:
            if session == self.sessionId:
                self._changed()

    def _start_action(self, role, action_id, parent_rid, deadline, session):
        rid = uuid.uuid4().hex
        command = dict(protocol=PROTOCOL, sessionId=session, requestId=rid,
                       expiresAtMs=self.epoch_ms()+max(1, int((deadline-self.clock())*1000)),
                       type='ActionCommand', payload=dict(instanceId=role['instanceId'], actionId=action_id, enqueue=True))
        action = dict(requestId=rid, sessionId=session, instanceId=role['instanceId'], actionId=action_id,
                      loop=next(a['loop'] for a in role['supportedActions'] if a['actionId'] == action_id),
                      status='requested', history=['requested'], parentRequestId=parent_rid)
        self.actions.append(action)
        self._changed()
        async def send():
            try:
                await self._bounded(self.body.send(copy.deepcopy(command)), deadline, rid, 'body')
            except asyncio.CancelledError:
                action['requestError'] = dict(code='cancelled', message='动作传输已取消，未伪造身体终态', retryable=True)
            except Exception:
                action['requestError'] = dict(code='unavailable', message='动作传输失败，未确认接收', retryable=True)
            finally:
                if session == self.sessionId:
                    self._changed()
        self._track(send(), rid, 'body')

    def _action_result(self, req):
        p = req['payload']
        require(identifier(req.get('eventId')) and req.get('source') == 'A' and identifier(req.get('sourceInstanceId')) and
                integer(req.get('sourceSeq')) and req['sourceSeq'] > 0 and integer(req.get('atMs')), '动作反馈必须是 A 的事件信封')
        action = next((a for a in self.actions if a['requestId'] == req['requestId']), None)
        require(action is not None and action['instanceId'] == p.get('instanceId'), '动作关联不存在', 'not-found')
        event_key, signature = (req['sourceInstanceId'], req['eventId']), fingerprint(req)
        previous = self.result_events.get(event_key)
        if previous:
            require(previous['signature'] == signature, '同一反馈事件内容冲突', 'conflict')
            return copy.deepcopy(previous['reply'])
        self.result_events = {k:v for k,v in self.result_events.items() if v['deadline'] > self.clock()}
        require(len(self.result_events) < self.dedup_capacity, '动作事件去重容量已满', 'unavailable')
        status = p.get('status')
        require(isinstance(status,str) and status in ACTION_TRANSITIONS.get(action['status'], set()), '非法动作状态转换', 'conflict')
        require(not (status == 'completed' and action['loop']), '循环动作不得自行完成', 'conflict')
        for key in ('reasonCode','message'):
            require(key not in p or isinstance(p[key],str), '动作反馈原因必须为字符串')
        action['status'] = status
        action['history'].append(status)
        for key in ('reasonCode', 'message'):
            if key in p:
                action[key] = copy.deepcopy(p[key])
        self._add(dict(type='body', name='身体执行反馈'), f"{action['actionId']}：{status}",
                  [action['instanceId']], 'action-feedback', rid=req['requestId'])
        result = self._reply(req, True)
        self.result_events[event_key] = dict(signature=signature, reply=copy.deepcopy(result),
                                            deadline=self.clock()+(req['expiresAtMs']-self.epoch_ms())/1000)
        return result

    async def _set_policy(self, req, deadline):
        require(self.capture is not None and hasattr(self.capture, 'applyObservationPolicy'), '采集端缺少策略确认能力', 'unsupported-capability')
        desired = policy(req['payload'])
        require(desired['policyRevision'] > self.policyRevision, '策略版本必须递增', 'conflict')
        self.policyRevision = desired['policyRevision']
        self.requestedPolicy, self.analysisPaused, self.policyError = desired, True, None
        self.scene += 1
        session, rid = self.sessionId, req['requestId']
        self._changed()
        try:
            result = await self._bounded(self.capture.applyObservationPolicy(copy.deepcopy(desired)), deadline, rid, 'policy')
            require(self._valid(session, rid, deadline) and self.requestedPolicy == desired, '策略确认已过期', 'stale')
            upstream_error(result)
            require(isinstance(result, dict) and result.get('policyRevision') == desired['policyRevision'], '采集端未确认目标策略版本', 'conflict')
            self.confirmedPolicy, self.requestedPolicy = desired, None
            self.analysisPaused = desired['paused']
        except BaseException as exc:
            if session == self.sessionId and self.requestedPolicy == desired:
                self.policyError = exc.error if isinstance(exc, ProtocolError) else dict(code='cancelled' if isinstance(exc,asyncio.CancelledError) else 'unavailable', message='策略同步未完成，分析已暂停', retryable=True)
            raise
        finally:
            if session == self.sessionId:
                self._changed()

    def _observe(self, req, deadline):
        p, session, rid = req['payload'], self.sessionId, req['requestId']
        require(p.get('source', 'window') not in ('self', 'bubble', 'action-feedback'), '自身输出不触发观察', 'unsupported-capability')
        self.scene += 1
        require(not self.analysisPaused and self.confirmedPolicy is not None, '观察未获上游确认或处于暂停', 'unavailable')
        require(self.capture is not None, '缺少采集能力', 'unsupported-capability')
        current = self.confirmedPolicy
        require(p.get('force', False) or self.clock()-self.last_observe >= current['cooldownMs']/1000, '观察冷却中', 'unavailable')
        self.last_observe = self.clock()
        scene, revision, generation = self.scene, current['policyRevision'], self.generation
        capture_req = dict(protocol=PROTOCOL, sessionId=session, requestId=rid, expiresAtMs=req['expiresAtMs'], type='CaptureRequest',
                           payload=dict(policyRevision=revision, sceneId=str(scene), scope=current['scope']))
        job = dict(taskId=uuid.uuid4().hex, requestId=rid, instanceId=None, audience='all', status='running', type='capture')
        self.jobs.append(job)
        self._changed()
        async def observe():
            try:
                result = await self._bounded(self.capture.capture(copy.deepcopy(capture_req)), deadline, rid, 'capture')
                require(self._valid(session, rid, deadline, generation, (scene, revision)), '画面结果已过期', 'stale')
                require(isinstance(result, dict) and result.get('protocol') == PROTOCOL and result.get('type') == 'CaptureResult' and result.get('sessionId') == session and result.get('requestId') == rid, '采集结果关联无效', 'stale')
                shot = result.get('payload', {})
                upstream_error(shot)
                require(shot.get('policyRevision') == revision and shot.get('sceneId') == str(scene), '采集策略或场景已过期', 'stale')
                require(integer(shot.get('capturedAtMs')) and shot['capturedAtMs']>0 and shot.get('source') in ('simulation', 'native'), '画面来源/时间缺失')
                rectangle(shot.get('rect'))
                require(self.resolver is not None, '缺少画面资源解析器', 'unsupported-capability')
                attachment = await self._bounded(self.resolver.describe(shot.get('contentRef'), session), deadline, rid, 'resource')
                attachments = await self._attachments([attachment], deadline, rid, session)
                require(self._valid(session, rid, deadline, generation, (scene, revision)), '解析后的画面已过期', 'stale')
                description = f"{'模拟' if shot['source'] == 'simulation' else '原生'}画面 · {current['scope']} · capturedAtMs={shot['capturedAtMs']}"
                message = self._add(dict(type='environment', name='桌面观察'), description, kind='desktop-observation', attachments=attachments, rid=rid)
                message['observation'] = copy.deepcopy(shot)
                job['status'] = 'done'
                for instance in list(self.roles):
                    self._track(self._respond(instance, 'all', rid, deadline, generation, session, (scene, revision)), rid, 'dialogue')
            except asyncio.CancelledError:
                job['status'] = 'cancelled'
            except ProtocolError as exc:
                job['status'] = exc.error['code'] if exc.error['code'] in ('stale', 'timeout') else 'failed'
                job['error'] = exc.error
            except Exception:
                job['status'] = 'failed'
                job['error'] = dict(code='unavailable', message='采集失败，未分析画面', retryable=True)
            finally:
                if session == self.sessionId:
                    self._changed()
        self._track(observe(), rid, 'capture')

    async def _release_resources(self, session, deadline):
        if not self.resolver or not callable(getattr(self.resolver,'releaseSession',None)):
            return None
        self.resourceCleanupPending.add(session)
        try:
            await self._bounded(self.resolver.releaseSession(session),deadline,session,'cleanup')
            self.resourceCleanupPending.discard(session)
        except ProtocolError as exc:
            return exc.error
        except asyncio.CancelledError:
            return dict(code='cancelled',message='资源释放已取消，需宿主继续清理',retryable=True)
        except Exception:
            return dict(code='unavailable',message='资源桥释放失败，需宿主继续清理',retryable=True)
        return None

    async def _reset(self, deadline):
        old_session = self.sessionId
        for task in list(self.tasks):
            task.cancel()
        self.sessionId = uuid.uuid4().hex
        self.generation += 1
        self.scene += 1
        self.revision = 0
        self.messages, self.actions, self.jobs, self.contexts, self.steps = [], [], [], {}, []
        self.auto_task, self.requestedPolicy, self.policyError = None, None, None
        self.analysisPaused = True
        self.last_observe = float('-inf')
        self._changed()
        return await self._release_resources(old_session,deadline)

    def diagnostics(self):
        self._thread()
        return copy.deepcopy(dict(protocol=PROTOCOL, implementationVersion=IMPLEMENTATION,
                                  sourceInstanceId=self.sourceInstanceId, mode=getattr(self.model, 'mode', 'injected'),
                                  view=self.getView(dict(type='user')), roles=list(self.roles.values()), contexts=self.contexts,
                                  steps=self.steps, listenerErrors=self.listenerErrors,
                                  backgroundTasks=len(self.tasks), policyRevision=self.policyRevision,
                                  resourceCleanupPending=list(self.resourceCleanupPending)))

    async def close(self):
        self._thread()
        self.closed = True
        self.listeners.clear()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.wait(list(self.tasks), timeout=1)
        deadline = self.clock()+1
        errors = []
        for session in self.resourceCleanupPending | {self.sessionId}:
            error = await self._release_resources(session,deadline)
            if error:
                errors.append(error)
        await asyncio.sleep(0)  # 排空已结束任务的回调，不按固定毫秒猜测完成。
        return dict(closed=True, pendingTasks=len(self.tasks), resourceErrors=errors,
                    resourceCleanupPending=list(self.resourceCleanupPending),
                    resourceCleanupDelegated=bool(self.resolver and not callable(getattr(self.resolver,'releaseSession',None))),
                    providerWorkers=getattr(self.model, 'activeRequests', None))
