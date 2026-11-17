import { PROTOCOL, CAPABILITIES, declaration, negotiate, ContractError, fail, publicError, validateInput, validateImage, validateAttachment, validatePolicy, nonempty } from './protocol.js';

const defaultRuntime = { epochNow: Date.now, monoNow: () => performance.now(), setTimer: (fn, ms) => setTimeout(fn, ms), clearTimer: id => clearTimeout(id), id: () => crypto.randomUUID() };
export const USER_VIEWER = Object.freeze({ type: 'user' });
function validateView(view) {
  if (!view || !nonempty(view.sessionId) || !Number.isSafeInteger(view.revision) || view.revision < 0 || !Array.isArray(view.messages)) fail('invalid-input');
  const ids = new Set(), seqs = new Set();
  for (const message of view.messages) {
    if (!nonempty(message.messageId) || ids.has(message.messageId) || !Number.isSafeInteger(message.seq) || message.seq < 0 || seqs.has(message.seq) || !Number.isSafeInteger(message.atMs) || typeof message.text !== 'string' || !['message', 'story-event', 'desktop-observation', 'action-feedback'].includes(message.kind)) fail('invalid-input');
    const speaker = message.speaker;
    if (!speaker || !['user', 'character', 'environment', 'director', 'body'].includes(speaker.type) || !nonempty(speaker.name) || (speaker.type === 'character' && (!nonempty(speaker.instanceId) || speaker.instanceId === 'all'))) fail('invalid-input');
    if (message.audience !== 'all' && (!Array.isArray(message.audience) || !message.audience.every(nonempty))) fail('invalid-input');
    if (!Array.isArray(message.attachments)) fail('invalid-input');
    message.attachments.forEach(item => validateAttachment(item));
    ids.add(message.messageId); seqs.add(message.seq);
  }
}

// 正式宿主适配入口。conversation 仅允许 C 的用户获准视图；不导入 demo。
export function createCompanionHost({ conversation, presentation, transport, resources, requiredCapabilities, runtime = defaultRuntime, timeoutMs = 10000 }) {
  const limits = negotiate(transport.declaration, [...new Set([...CAPABILITIES, ...(requiredCapabilities || [])])]);
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 300000) fail('invalid-input');
  const listeners = new Set(), pending = new Map(), draftRefs = new Set();
  let snapshot, disposed = false, resourceEpoch = 0, sourceError = null;
  const notify = () => { for (const listener of [...listeners]) listener(); };
  const reportCancel = operation => {
    if (operation.local) { try { resources.cancelPreparation({ requestId: operation.requestId, sessionId: operation.sessionId }); } catch {} return; }
    // 取消本身也是有期限的普通信封；不向 IPC 传 AbortSignal。
    const request = { protocol: PROTOCOL, sessionId: operation.sessionId, requestId: runtime.id(), expiresAtMs: runtime.epochNow() + timeoutMs, type: 'CancelRequest', payload: { requestId: operation.requestId } };
    try { Promise.resolve(transport.cancel(request)).catch(() => {}); } catch { /* 本地终结不依赖对端取消成功。 */ }
  };
  function cancelPending(code = 'cancelled') {
    for (const operation of [...pending.values()]) { operation.reject(new ContractError(code)); reportCancel(operation); }
  }
  function releaseDrafts() {
    for (const ref of draftRefs) resources.releaseDraft(ref);
    draftRefs.clear();
  }
  function refresh() {
    if (disposed) return;
    try {
      const view = conversation.getView(USER_VIEWER);
      const scene = presentation.getSnapshot();
      validateView(view);
      if (scene.sessionId !== view.sessionId) fail('stale');
      if (!Array.isArray(scene.roles) || !nonempty(scene.mode) || (scene.clock?.mode === 'replay' && !Number.isSafeInteger(scene.clock.atMs))) fail('invalid-input');
      validatePolicy(scene.observationPolicy);
      const instanceIds = new Set();
      for (const role of scene.roles) {
        if (!nonempty(role.instanceId) || role.instanceId === 'all' || instanceIds.has(role.instanceId) || !nonempty(role.name) || !nonempty(role.packageId) || !Number.isSafeInteger(role.revision) || role.revision < 1 || typeof role.interactive !== 'boolean' || role.position?.coordinateSpace !== 'stage-css-px' || typeof role.position.visible !== 'boolean' || !Number.isFinite(role.position.x) || !Number.isFinite(role.position.y)) fail('invalid-input');
        instanceIds.add(role.instanceId);
      }
      if (snapshot?.sessionId === view.sessionId && view.revision < snapshot.revision) fail('stale');
      if (snapshot && snapshot.sessionId !== view.sessionId) { resourceEpoch++; cancelPending('stale'); releaseDrafts(); }
      // 可丢弃的只读投影视图；消息 ID/seq/atMs 原样保留，不向源写入。
      const preview = ref => ref ? resources.preview(ref) : undefined;
      const messages = [...view.messages].sort((a, b) => a.seq - b.seq).map(message => ({ ...message, speaker: { ...message.speaker, previewUrl: preview(message.speaker.avatarRef) }, attachments: message.attachments.map(item => ({ ...item, previewUrl: preview(item.contentRef) })) }));
      snapshot = { ...scene, sessionId: view.sessionId, revision: view.revision, messages, processing: view.processing, roles: scene.roles.map(role => ({ ...role, position: { ...role.position }, previewUrl: preview(role.avatarRef) })), connectionError: null };
      sourceError = null;
    } catch (error) {
      sourceError = publicError(error);
      if (!snapshot) throw error;
      snapshot = { ...snapshot, connectionError: sourceError };
    }
    notify();
  }
  // 先订阅再取快照，避免初始化时漏更新；revision 用于拒绝倒退。
  const offConversation = conversation.subscribe(USER_VIEWER, refresh);
  let offPresentation;
  try { offPresentation = presentation.subscribe(refresh); refresh(); }
  catch (error) { offConversation(); offPresentation?.(); throw error; }

  function request(type, payload, requestId = runtime.id(), expiresAtMs = runtime.epochNow() + timeoutMs, localWork) {
    if (disposed) return Promise.reject(new ContractError('cancelled'));
    if (sourceError) return Promise.reject(new ContractError('unavailable'));
    if (pending.has(requestId)) return Promise.reject(new ContractError('conflict'));
    if (expiresAtMs <= runtime.epochNow()) return Promise.reject(new ContractError('stale'));
    const envelope = { protocol: PROTOCOL, sessionId: snapshot.sessionId, requestId, expiresAtMs, type, payload };
    const deadline = runtime.monoNow() + expiresAtMs - runtime.epochNow();
    return new Promise((resolve, reject) => {
      let finished = false;
      const finish = (error, result) => {
        if (finished) return;
        finished = true; runtime.clearTimer(operation.timer); pending.delete(requestId);
        error ? reject(error) : resolve(result);
      };
      const operation = { sessionId: envelope.sessionId, requestId, local: Boolean(localWork), reject: error => finish(error) };
      operation.timer = runtime.setTimer(() => { finish(new ContractError('timeout')); reportCancel(operation); }, Math.max(0, deadline - runtime.monoNow()));
      pending.set(requestId, operation);
      Promise.resolve().then(() => {
        if (finished) throw new ContractError('cancelled');
        return localWork ? localWork(envelope) : transport.request(envelope);
      }).then(response => {
        if (finished) { if (localWork && response?.payload?.contentRef) resources.releaseDraft(response.payload.contentRef); return; }
        if (runtime.monoNow() >= deadline) { if (localWork && response?.payload?.contentRef) resources.releaseDraft(response.payload.contentRef); finish(new ContractError('timeout')); reportCancel(operation); return; }
        if (envelope.sessionId !== snapshot.sessionId) { finish(new ContractError('stale')); return; }
        if (response?.protocol !== PROTOCOL || response.sessionId !== envelope.sessionId || response.requestId !== requestId) { finish(new ContractError('conflict')); return; }
        if (response.payload?.error) { const error = response.payload.error; finish(new ContractError(error.code, undefined, Boolean(error.retryable))); return; }
        finish(null, response.payload);
      }, error => finish(error instanceof ContractError ? error : new ContractError('unavailable')));
    });
  }
  const host = {
    declaration, limits,
    getSnapshot: () => snapshot,
    now: () => snapshot.clock?.mode === 'replay' ? snapshot.clock.atMs : runtime.epochNow(),
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    onUiEvent: event => presentation.onUiEvent?.(event),
    async submit(input) {
      if (disposed) fail('cancelled');
      if (sourceError) fail('unavailable');
      validateInput(input, snapshot.roles, limits);
      if (!nonempty(input.requestId)) fail('invalid-input');
      if (input.expiresAtMs !== undefined && !Number.isSafeInteger(input.expiresAtMs)) fail('invalid-input');
      const attachments = input.attachments.map(({ attachmentId, name, mime, sizeBytes, contentRef }) => ({ attachmentId, name, mime, sizeBytes, contentRef }));
      const sessionId = snapshot.sessionId;
      attachments.forEach(item => resources.retainInFlight(item.contentRef, sessionId, input.requestId));
      // previewUrl 在此显式剥离，不能进入 C 的模型引用。
      let receipt;
      try { receipt = await request('UserInput', { kind: input.kind, recipient: input.recipient, text: input.text, attachments }, input.requestId, input.expiresAtMs); }
      catch (error) {
        // 取消/超时不证明对端未受理；此时资源保留到权威终态或会话释放。
        if (['invalid-input', 'not-found', 'conflict', 'stale', 'unsupported-version', 'unsupported-capability'].includes(error.code)) resources.finishRequest(input.requestId, false);
        throw error;
      }
      if (receipt?.accepted !== true) { resources.finishRequest(input.requestId, false); throw new ContractError('invalid-input'); }
      resources.finishRequest(input.requestId, true);
      attachments.forEach(item => { draftRefs.delete(item.contentRef); resources.retainSubmitted(item.contentRef, sessionId); });
      return receipt;
    },
    async prepareAttachment(file) {
      if (disposed) throw new ContractError('cancelled');
      validateImage(file, limits);
      const epoch = resourceEpoch, sessionId = snapshot.sessionId;
      // 文件只进入注入的浏览器资源桥；这里的任务标签不会发送给 transport。
      const attachment = await request('local-resource-prepare', {}, undefined, undefined, async envelope => {
        const prepared = await resources.prepare(file, { sessionId, requestId: envelope.requestId, expiresAtMs: envelope.expiresAtMs });
        return { protocol: PROTOCOL, sessionId, requestId: envelope.requestId, payload: prepared };
      });
      if (epoch !== resourceEpoch || disposed) { resources.releaseDraft(attachment.contentRef); throw new ContractError('stale'); }
      try { validateAttachment(attachment, limits); } catch (error) { resources.releaseDraft(attachment.contentRef); throw error; }
      draftRefs.add(attachment.contentRef);
      return { ...attachment, previewUrl: resources.preview(attachment.contentRef) };
    },
    releaseDraft(attachment) { if (draftRefs.delete(attachment.contentRef)) resources.releaseDraft(attachment.contentRef); },
    changeSettings(policy) { validatePolicy(policy); return request('ObservationPolicy', policy); },
    openStudio() { return request('OpenStudio', {}); },
    cancelPending,
    invalidateDrafts() { resourceEpoch++; cancelPending(); releaseDrafts(); },
    dispose() { if (disposed) return; disposed = true; resourceEpoch++; cancelPending(); offConversation(); offPresentation(); listeners.clear(); releaseDrafts(); }
  };
  return host;
}
