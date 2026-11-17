import { roles as seedRoles, events } from '../fixtures/scenes.js';
import { createCompanionHost } from '../src/host.js';
import { PROTOCOL, VERSION, CAPABILITIES, LIMITS, ContractError, publicError, createRequestLedger, validateInput, validatePolicy, validateEnvelope, summarizeCharacterPackage } from '../src/protocol.js';
import { createDemoResourceBridge } from './resources.js';

export function createDemoHost({ characterPackage, runtime, scheduler, timeoutMs = 10000 } = {}) {
  const sourceListeners = new Set(), sceneListeners = new Set(), logListeners = new Set(), jobs = new Map();
  const resources = createDemoResourceBridge(), ledger = createRequestLedger(runtime ? { epochNow: runtime.epochNow, monoNow: runtime.monoNow } : {});
  const now = runtime?.epochNow || Date.now;
  const schedule = scheduler || { set: (fn, ms) => setTimeout(fn, ms), clear: id => clearTimeout(id) };
  let view, scene, index = 0, seq = 0, closed = false, failNext = false, holdNext = false;
  const log = [];
  function emit() { for (const listener of sourceListeners) listener(); for (const listener of sceneListeners) listener(); }
  function record(kind, fields = {}) { log.push({ step: log.length + 1, kind, ...fields }); for (const listener of logListeners) listener(); }
  function cancelJob(requestId) { const job = jobs.get(requestId); if (job) job.finish(new ContractError('cancelled')); }
  function delay(request) {
    return new Promise((resolve, reject) => {
      const job = { timer: null, deadlineTimer: null, done: false, finish(error) { if (job.done) return; job.done = true; schedule.clear(job.timer); schedule.clear(job.deadlineTimer); jobs.delete(request.requestId); error ? reject(error) : resolve(); } };
      if (!holdNext) job.timer = schedule.set(() => job.finish(), 450);
      job.deadlineTimer = schedule.set(() => job.finish(new ContractError('timeout')), Math.max(0, request.expiresAtMs - now()));
      holdNext = false; jobs.set(request.requestId, job);
    });
  }
  function reset(scenario = 'conversation', clockMode = 'live') {
    const old = view?.sessionId;
    for (const id of jobs.keys()) cancelJob(id);
    ledger.clear(); failNext = false; holdNext = false; index = 0; seq = 0; log.length = 0;
    const sessionId = `demo-${crypto.randomUUID()}`;
    const roles = scenario === 'empty' ? [] : seedRoles.map((role, i) => ({ instanceId: role.id, packageId: `demo-${role.id}`, revision: 1, name: role.name, mark: role.mark, tone: role.tone, interactive: true, position: { x: role.x, y: role.y, coordinateSpace: 'stage-css-px', visible: true } }));
    if (characterPackage && roles.length) {
      const base = summarizeCharacterPackage(characterPackage, 'instance-one');
      Object.assign(roles[0], base, { name: `${base.name} · 一`, tone: 'moss' });
      Object.assign(roles[1], summarizeCharacterPackage(characterPackage, 'instance-two'), { name: `${base.name} · 二`, tone: 'clay' });
      if (base.avatarRef) resources.registerAvatar(base.avatarRef, characterPackage.assets[characterPackage.character.avatarAssetId], sessionId);
    }
    view = { sessionId, revision: 0, messages: [], processing: 'idle' };
    scene = { sessionId, scene: scenario, mode: `模拟 · 网页舞台 · 未连接 A/B/C${clockMode === 'replay' ? ' · 回放时钟' : ''}`, sourceInstanceId: `demo-producer-${sessionId}`, roles, observationPolicy: { policyRevision: 1, paused: false, scope: 'active-window', cooldownMs: 10000, excludedRegions: [] }, clock: clockMode === 'replay' ? { mode: 'replay', atMs: now() } : { mode: 'live' } };
    if (scenario !== 'empty') next(false);
    emit(); // 先移除旧 DOM 引用，再释放会话资源。
    if (old) resources.releaseSession(old);
    record('reset', { sessionId, scene: scenario });
  }
  function next(notify = true) {
    if (closed || index >= events.length || scene.scene === 'empty') return false;
    const event = events[index++], roleIndex = seedRoles.findIndex(role => role.id === event.speakerId);
    if (scene.clock.mode === 'replay' && index > 1) scene.clock.atMs += 2400;
    const identities = characterPackage ? ['instance-one', 'instance-two', 'qiu'] : seedRoles.map(role => role.id);
    const instanceId = identities[roleIndex];
    const role = scene.roles.find(role => role.instanceId === instanceId);
    if (event.move && role) Object.assign(role.position, { x: event.move.x, y: event.move.y });
    view.messages.push({ messageId: `fixture-${index}`, seq: ++seq, atMs: scene.clock.mode === 'replay' ? scene.clock.atMs : now(), speaker: { type: 'character', instanceId, name: role?.name || seedRoles[roleIndex].name, avatarRef: role?.avatarRef }, audience: 'all', kind: 'message', text: event.text, attachments: [] });
    if (event.remove) scene.roles = scene.roles.filter(role => role.instanceId !== identities[seedRoles.findIndex(item => item.id === event.remove)]);
    view.revision++; if (notify) emit(); record('fixture', { index }); return true;
  }
  reset();
  const peerDeclaration = { protocol: PROTOCOL, module: 'assembly', implementationVersion: `demo-${VERSION}`, capabilities: [...CAPABILITIES], limits: { ...LIMITS } };
  const transport = {
    declaration: peerDeclaration,
    request(request) {
      return ledger.execute(request, view.sessionId, async () => {
        record('request', { requestId: request.requestId, sessionId: request.sessionId, type: request.type, recipient: request.payload.recipient, textLength: [...(request.payload.text || '')].length, attachmentCount: request.payload.attachments?.length, policyRevision: request.payload.policyRevision, cooldownMs: request.payload.cooldownMs });
        const payload = request.payload;
        try {
          if (request.type === 'UserInput') { validateInput(payload, scene.roles); payload.attachments.forEach(item => resources.resolve(item.contentRef, request.sessionId)); view.processing = 'working'; view.revision++; emit(); }
          else if (request.type === 'ObservationPolicy') { validatePolicy(payload); if (payload.policyRevision !== scene.observationPolicy.policyRevision + 1) throw new ContractError('conflict'); }
          else if (request.type !== 'OpenStudio') throw new ContractError('unsupported-capability');
          await delay(request);
          validateEnvelope(request, view.sessionId, now());
          if (failNext) { failNext = false; throw new ContractError('unavailable'); }
          if (request.type === 'UserInput') {
            validateInput(payload, scene.roles);
            view.messages.push({ messageId: `demo-user-${request.requestId}`, seq: ++seq, atMs: scene.clock.mode === 'replay' ? scene.clock.atMs : now(), speaker: { type: payload.kind === 'story-event' ? 'director' : 'user', name: '你' }, audience: payload.recipient === 'all' ? 'all' : [payload.recipient], kind: payload.kind, text: payload.text, attachments: structuredClone(payload.attachments), requestId: request.requestId });
            resources.finishRequest(request.requestId, true);
            view.processing = 'idle'; view.revision++; emit();
          } else if (request.type === 'ObservationPolicy') { scene.observationPolicy = structuredClone(payload); emit(); }
          else record('open-studio', { result: '模拟捕获，未启动 B' });
          record('accepted', { requestId: request.requestId });
          return { protocol: PROTOCOL, sessionId: request.sessionId, requestId: request.requestId, payload: request.type === 'UserInput' ? { accepted: true } : { policyRevision: scene.observationPolicy.policyRevision } };
        } catch (error) {
          if (view.sessionId === request.sessionId) { if (request.type === 'UserInput') { view.processing = 'failed'; view.revision++; } emit(); }
          resources.finishRequest(request.requestId, false);
          record('failure', { requestId: request.requestId, code: publicError(error).code });
          return { protocol: PROTOCOL, sessionId: request.sessionId, requestId: request.requestId, payload: { accepted: false, error: publicError(error) } };
        }
      });
    },
    cancel(request) { return ledger.execute(request, view.sessionId, () => {
      if (request.type !== 'CancelRequest' || typeof request.payload.requestId !== 'string') throw new ContractError('invalid-input');
      record('cancel', { requestId: request.payload.requestId }); cancelJob(request.payload.requestId);
      return { protocol: PROTOCOL, sessionId: request.sessionId, requestId: request.requestId, payload: { accepted: true } };
    }); }
  };
  const host = createCompanionHost({
    conversation: { getView: () => view, subscribe(viewer, listener) { if (viewer.type !== 'user') throw new ContractError('invalid-input'); sourceListeners.add(listener); return () => sourceListeners.delete(listener); } },
    presentation: { getSnapshot: () => scene, subscribe(listener) { sceneListeners.add(listener); return () => sceneListeners.delete(listener); }, onUiEvent(event) { record(event.kind, event); } },
    transport, resources, runtime, timeoutMs
  });
  const disposeHost = host.dispose;
  return Object.assign(host, {
    log, next, reset, record, resources, transport,
    subscribeLog(listener) { logListeners.add(listener); return () => logListeners.delete(listener); },
    setFailure() { failNext = true; }, setHold() { holdNext = true; },
    stats: () => ({ sourceSubscriptions: sourceListeners.size + sceneListeners.size, jobs: jobs.size, ...resources.stats() }),
    dispose() { if (closed) return; disposeHost(); closed = true; for (const id of jobs.keys()) cancelJob(id); resources.dispose(); sourceListeners.clear(); sceneListeners.clear(); logListeners.clear(); }
  });
}
