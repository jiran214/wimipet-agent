import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createCompanionHost } from '../src/host.js';
import { createController, layoutBubbles } from '../src/model.js';
import { PROTOCOL, LIMITS, CAPABILITIES, ContractError, negotiate, validateImage, validateInput, createRequestLedger, summarizeCharacterPackage } from '../src/protocol.js';
import { createDemoHost } from '../demo/host.js';
import { createDemoResourceBridge } from '../demo/resources.js';

const attachment = { attachmentId: 'image', name: 'sample.png', mime: 'image/png', sizeBytes: 100, contentRef: 'opaque-image', previewUrl: 'blob:preview-only' };
const role = { instanceId: 'instance-one', packageId: 'package', revision: 1, name: '甲', interactive: true, position: { x: 140, y: 250, visible: true, coordinateSpace: 'stage-css-px' } };
const policy = { policyRevision: 1, paused: false, scope: 'active-window', cooldownMs: 10000, excludedRegions: [] };
const input = (extra = {}) => ({ requestId: 'input-1', kind: 'message', recipient: 'all', text: '你好', attachments: [], ...extra });
const message = (id, seq, atMs = 1000) => ({ messageId: id, seq, atMs, speaker: { type: 'character', instanceId: role.instanceId, name: '甲' }, audience: 'all', kind: 'message', text: id, attachments: [] });
const drain = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function clock() {
  let mono = 0, epoch = 100000, serial = 0;
  const timers = new Map();
  const runtime = { epochNow: () => epoch, monoNow: () => mono, id: () => `generated-${++serial}`, setTimer(fn, ms) { const id = ++serial; timers.set(id, { at: mono + ms, fn }); return id; }, clearTimer: id => timers.delete(id) };
  return { runtime, advance(ms) { mono += ms; epoch += ms; for (const [id, timer] of [...timers]) if (timer.at <= mono) { timers.delete(id); timer.fn(); } }, jumpEpoch(ms) { epoch += ms; }, size: () => timers.size };
}
function harness({ respond, peer, runtime, resources, timeoutMs } = {}) {
  const calls = [], cancels = [], sourceListeners = new Set(), sceneListeners = new Set(), viewers = [];
  let view = { sessionId: 'session-one', revision: 1, messages: [message('allowed', 1)], processing: 'idle' };
  let scene = { sessionId: view.sessionId, roles: [structuredClone(role)], observationPolicy: structuredClone(policy), mode: 'test-injected-source' };
  const bridge = resources || { prepare: async () => ({ ...attachment }), preview: () => 'blob:preview-only', releaseDraft() {}, retainInFlight() {}, retainSubmitted() {}, finishRequest() {}, cancelPreparation() {} };
  const host = createCompanionHost({
    conversation: { getView(viewer) { viewers.push(viewer); return view; }, subscribe(viewer, fn) { viewers.push(viewer); sourceListeners.add(fn); return () => sourceListeners.delete(fn); } },
    presentation: { getSnapshot: () => scene, subscribe(fn) { sceneListeners.add(fn); return () => sceneListeners.delete(fn); } },
    transport: { declaration: peer || { protocol: PROTOCOL, module: 'assembly', implementationVersion: 'test', capabilities: [...CAPABILITIES], limits: LIMITS }, request(envelope) { calls.push(envelope); return respond ? respond(envelope) : Promise.resolve({ protocol: PROTOCOL, sessionId: envelope.sessionId, requestId: envelope.requestId, payload: { accepted: true } }); }, cancel(envelope) { cancels.push(envelope); return Promise.resolve(); } },
    resources: bridge, runtime, timeoutMs
  });
  return { host, calls, cancels, viewers, get view() { return view; }, get scene() { return scene; }, update(nextView, nextScene) { view = nextView || view; scene = nextScene || scene; for (const fn of sourceListeners) fn(); for (const fn of sceneListeners) fn(); }, count: () => sourceListeners.size + sceneListeners.size };
}

test('能力握手拒绝未知版本、未知必需能力和缺失能力，取双方限制交集', () => {
  const peer = { protocol: PROTOCOL, module: 'assembly', implementationVersion: 'test', capabilities: [...CAPABILITIES], limits: { ...LIMITS, textCodePoints: 2000, attachmentMimes: ['image/png'] } };
  assert.equal(negotiate(peer).textCodePoints, 2000); assert.deepEqual(negotiate(peer).attachmentMimes, ['image/png']);
  assert.throws(() => negotiate({ ...peer, protocol: 'wimipet/2' }), { code: 'unsupported-version' });
  assert.throws(() => negotiate(peer, ['future-required']), { code: 'unsupported-capability' });
  assert.throws(() => harness({ peer: { ...peer, capabilities: [] } }), { code: 'unsupported-capability' });
});

test('正式 facade 只订阅用户获准视图，按 seq 保序且不重写 atMs，不编辑源 List', () => {
  const h = harness(); const original = h.view.messages;
  h.update({ ...h.view, revision: 2, messages: [message('later', 5, 20), message('earlier', 2, 9000)] });
  assert.deepEqual(h.host.getSnapshot().messages.map(item => item.messageId), ['earlier', 'later']);
  assert.equal(h.host.getSnapshot().messages[0].atMs, 9000); assert.equal(original.length, 1);
  assert.ok(h.viewers.every(viewer => viewer.type === 'user'));
  assert.ok(!h.host.getSnapshot().messages.some(item => item.text.includes('private')));
  h.host.dispose(); assert.equal(h.count(), 0);
});

test('不接受 revision 倒退、重复 seq 或物理位置误传；错误可见且禁止继续提交', async () => {
  const h = harness();
  h.update({ ...h.view, revision: 0 }); assert.equal(h.host.getSnapshot().connectionError.code, 'stale');
  await assert.rejects(h.host.submit(input()), { code: 'unavailable' });
  h.update({ ...h.view, revision: 2, messages: [message('a', 1), message('b', 1)] }); assert.equal(h.host.getSnapshot().connectionError.code, 'invalid-input');
  h.update({ ...h.view, revision: 3, messages: [] }, { ...h.scene, roles: [{ ...role, position: { ...role.position, coordinateSpace: 'desktop-physical-px' } }] }); assert.equal(h.host.getSnapshot().connectionError.code, 'invalid-input');
  h.host.dispose();
});

test('单 PNG 无正文合法；模型请求无 previewUrl、File 或 blob；指定实例不变', async () => {
  const h = harness();
  await h.host.submit(input({ text: '', recipient: 'instance-one', attachments: [attachment] }));
  const request = h.calls[0];
  assert.equal(request.protocol, PROTOCOL); assert.equal(request.sessionId, 'session-one'); assert.ok(Number.isSafeInteger(request.expiresAtMs));
  assert.equal(request.type, 'UserInput'); assert.equal(request.payload.text, ''); assert.equal(request.payload.recipient, 'instance-one');
  assert.equal(request.payload.attachments[0].contentRef, 'opaque-image'); assert.ok(!JSON.stringify(request).includes('blob:'));
  assert.equal('previewUrl' in request.payload.attachments[0], false); h.host.dispose();
});

test('Unicode 码点、大小和格式遵守基线；多图整批拒绝，不静默丢弃', () => {
  validateInput(input({ text: '𠮷'.repeat(4000) }), [role]);
  assert.throws(() => validateInput(input({ text: '𠮷'.repeat(4001) }), [role]), { code: 'invalid-input' });
  validateImage({ type: 'image/jpeg', size: 2 * 1048576 });
  assert.throws(() => validateImage({ type: 'image/png', size: 2 * 1048576 + 1 }), { code: 'invalid-input' });
  for (const type of ['image/webp', 'image/gif', 'image/svg+xml']) assert.throws(() => validateImage({ type, size: 100 }), { code: 'invalid-input' });
  assert.throws(() => validateInput(input({ attachments: [attachment, attachment] }), [role]), { code: 'invalid-input' });
  assert.throws(() => validateInput(input({ attachments: [{ ...attachment, contentRef: 'blob:invalid' }] }), [role]), { code: 'invalid-input' });
});

test('InputReceipt 拒绝不清草稿；成功后清空；请求期间不重复提交', async () => {
  let wait = deferred(); const h = harness({ respond: () => wait.promise }); const ui = createController(h.host);
  ui.update({ recipient: 'instance-one', kind: 'story-event', draft: '故事条件' });
  const first = ui.send(); assert.equal(await ui.send(), false); await drain(); assert.equal(h.calls.length, 1);
  wait.resolve({ protocol: PROTOCOL, sessionId: 'session-one', requestId: h.calls[0].requestId, payload: { accepted: false, error: { code: 'unavailable', message: 'sk-private-provider-secret', retryable: true } } });
  assert.equal(await first, false); assert.equal(ui.state.draft, '故事条件'); assert.equal(ui.state.recipient, 'instance-one'); assert.ok(!ui.state.notice.includes('secret'));
  wait = deferred(); const second = ui.send(); await drain(); wait.resolve({ protocol: PROTOCOL, sessionId: 'session-one', requestId: h.calls[1].requestId, payload: { accepted: true } });
  assert.equal(await second, true); assert.equal(ui.state.draft, ''); ui.dispose(); h.host.dispose();
});

test('取消与超时都有结构化错误，保留草稿且发出关联取消；使用单调截止时间', async () => {
  const c = clock(), wait = deferred(), h = harness({ runtime: c.runtime, timeoutMs: 100, respond: () => wait.promise });
  const ui = createController(h.host); ui.update({ draft: '保留' });
  const first = ui.send(); await drain(); h.host.cancelPending(); assert.equal(await first, false); assert.equal(ui.state.error.code, 'cancelled');
  const second = ui.send(); await drain(); c.jumpEpoch(-500000); c.advance(101);
  assert.equal(await second, false); assert.equal(ui.state.error.code, 'timeout'); assert.equal(ui.state.draft, '保留'); assert.equal(h.cancels.length, 2);
  assert.equal(h.cancels[0].type, 'CancelRequest'); assert.equal(h.cancels[0].payload.requestId, h.calls[0].requestId);
  ui.dispose(); h.host.dispose(); assert.equal(c.size(), 0);
});

test('过期请求不转发；响应关联错误明确拒绝', async () => {
  const c = clock(), h = harness({ runtime: c.runtime });
  await assert.rejects(h.host.submit(input({ expiresAtMs: c.runtime.epochNow() })), { code: 'stale' }); assert.equal(h.calls.length, 0); h.host.dispose();
  const mismatch = harness({ respond: async request => ({ protocol: PROTOCOL, sessionId: request.sessionId, requestId: 'wrong', payload: { accepted: true } }) });
  await assert.rejects(mismatch.host.submit(input()), { code: 'conflict' }); mismatch.host.dispose();
});

test('新 session 自动隔离迟到成功；销毁取消等待、释放订阅，不清除新草稿', async () => {
  const wait = deferred(), h = harness({ respond: () => wait.promise }), ui = createController(h.host);
  ui.update({ draft: '旧草稿' }); const pending = ui.send(); await drain();
  h.update({ sessionId: 'session-two', revision: 0, messages: [], processing: 'idle' }, { ...h.scene, sessionId: 'session-two' });
  ui.update({ draft: '新草稿', recipient: 'instance-one' });
  wait.resolve({ protocol: PROTOCOL, sessionId: 'session-one', requestId: h.calls[0].requestId, payload: { accepted: true } });
  await pending; assert.equal(ui.state.draft, '新草稿'); assert.equal(ui.state.pending, false);
  ui.dispose(); h.host.dispose(); assert.equal(h.count(), 0);
});

test('接收角色移除不广播；观察策略含具体数值、排除范围与版本，回执不等于实际确认', async () => {
  const h = harness(), ui = createController(h.host);
  ui.update({ draft: '保留', recipient: 'instance-one' }); h.update(null, { ...h.scene, roles: [] });
  assert.equal(await ui.send(), false); assert.equal(h.calls.length, 0); assert.equal(ui.state.recipient, 'instance-one');
  const next = { ...policy, policyRevision: 2, paused: true, cooldownMs: 60000, excludedRegions: [{ x: -100, y: 20, width: 40, height: 50 }] };
  await h.host.changeSettings(next); assert.deepEqual(h.calls[0].payload, next);
  assert.equal(h.host.getSnapshot().observationPolicy.paused, false);
  h.update(null, { ...h.scene, observationPolicy: next }); assert.equal(h.host.getSnapshot().observationPolicy.policyRevision, 2);
  ui.dispose(); h.host.dispose();
});

test('气泡按 seq 布局、边缘有界不重叠，面板开启和过期时为零；历史时间不刷新', () => {
  const roles = Array.from({ length: 6 }, (_, i) => ({ ...role, instanceId: `${i}`, position: { ...role.position, x: i % 2 ? -900 : 9999, y: 0 } }));
  const messages = roles.map((item, i) => ({ ...message(`${i}`, i), speaker: { type: 'character', instanceId: item.instanceId, name: `${i}` } })).reverse();
  const viewport = { width: 600, height: 400 };
  const boxes = layoutBubbles(messages, roles, viewport, 2000, false); assert.ok(boxes.length > 0 && boxes.length <= 3);
  assert.deepEqual(boxes.map(box => box.message.seq), [...boxes.map(box => box.message.seq)].sort());
  for (const box of boxes) { assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= 600 && box.y + box.height <= 400); for (const other of boxes) if (other !== box) assert.ok(box.x + box.width <= other.x || other.x + other.width <= box.x || box.y + box.height <= other.y || other.y + other.height <= box.y); }
  assert.deepEqual(layoutBubbles(messages, roles, viewport, 2000, true), []); assert.deepEqual(layoutBubbles(messages, roles, viewport, 99999, false), []);
});

test('去重容量不会逐出有效请求；重复同 ID 不重做、不同内容 conflict、过期 stale', async () => {
  const c = clock(), ledger = createRequestLedger({ capacity: 1, epochNow: c.runtime.epochNow, monoNow: c.runtime.monoNow }); let calls = 0;
  const request = { protocol: PROTOCOL, sessionId: 's', requestId: 'r', expiresAtMs: c.runtime.epochNow() + 100, type: 'UserInput', payload: { text: 'x' } };
  const run = () => { calls++; return 'accepted'; };
  assert.equal(await ledger.execute(request, 's', run), 'accepted'); await ledger.execute(request, 's', run); assert.equal(calls, 1);
  await assert.rejects(ledger.execute({ ...request, payload: { text: 'different' } }, 's', run), { code: 'conflict' });
  await assert.rejects(ledger.execute({ ...request, requestId: 'another' }, 's', run), { code: 'unavailable' });
  c.advance(101); await assert.rejects(ledger.execute(request, 's', run), { code: 'stale' });
});

test('共同样例字节一致，真实 D 消费同包双实例身份和头像引用（非全链路）', async () => {
  const shared = await readFile(new URL('../../../docs/contracts/fixtures/v1/character-package.json', import.meta.url));
  const local = await readFile(new URL('../fixtures/v1/character-package.json', import.meta.url));
  const hash = bytes => createHash('sha256').update(bytes).digest('hex'); assert.equal(hash(local), hash(shared));
  const pkg = JSON.parse(local), one = summarizeCharacterPackage(pkg, 'instance-one'), two = summarizeCharacterPackage(pkg, 'instance-two');
  assert.equal(one.packageId, two.packageId); assert.notEqual(one.instanceId, two.instanceId); assert.equal(one.avatarRef, two.avatarRef);
  const host = createDemoHost({ characterPackage: pkg }); assert.equal(host.getSnapshot().roles[0].instanceId, 'instance-one'); assert.ok(host.getSnapshot().roles[0].previewUrl.startsWith('blob:'));
  while (host.next()) {} assert.equal(host.getSnapshot().messages.length, 7); assert.ok(host.getSnapshot().messages.some(message => message.speaker.instanceId === 'instance-two'));
  const sessionId = host.getSnapshot().sessionId; host.reset(); assert.notEqual(host.getSnapshot().sessionId, sessionId); assert.equal(host.stats().resources, 1);
  host.reset('conversation', 'replay'); const firstAt = host.getSnapshot().messages[0].atMs; host.next();
  assert.equal(host.now(), firstAt + 2400); assert.equal(host.getSnapshot().messages[0].atMs, firstAt);
  host.dispose(); assert.deepEqual(host.stats(), { sourceSubscriptions: 0, jobs: 0, resources: 0, preparing: 0 });
});

test('资源桥验证真实 PNG 字节；草稿释放、在途/已提交引用与会话清理分开', async () => {
  const bridge = createDemoResourceBridge(), bytes = await readFile(new URL('../fixtures/sample.png', import.meta.url));
  const file = new File([bytes], 'sample.png', { type: 'image/png' });
  const context = { sessionId: 's', requestId: 'r', expiresAtMs: Date.now() + 10000 };
  await assert.rejects(bridge.prepare(new File(['not-png'], 'fake.png', { type: 'image/png' }), context), { code: 'invalid-input' });
  const draft = await bridge.prepare(file, context); assert.ok(bridge.preview(draft.contentRef).startsWith('blob:')); assert.deepEqual(bridge.resolve(draft.contentRef, 's'), new Uint8Array(bytes));
  bridge.retainInFlight(draft.contentRef, 's', 'input'); bridge.releaseDraft(draft.contentRef); assert.equal(bridge.stats().resources, 1);
  bridge.finishRequest('input', true); assert.equal(bridge.stats().resources, 1); bridge.releaseSession('s'); assert.equal(bridge.stats().resources, 0);
  const next = await bridge.prepare(file, context); bridge.releaseDraft(next.contentRef); assert.equal(bridge.stats().resources, 0); bridge.dispose();
});

test('附件准备可超时，迟到资源会释放，销毁后不发布', async () => {
  const c = clock(), wait = deferred(), released = [];
  const h = harness({ runtime: c.runtime, timeoutMs: 10, resources: { prepare: () => wait.promise, preview: () => 'blob:test', releaseDraft: ref => released.push(ref), cancelPreparation() {} } });
  const pending = h.host.prepareAttachment({ type: 'image/png', size: 100, name: 'sample.png' }); await drain(); c.advance(11);
  await assert.rejects(pending, { code: 'timeout' }); wait.resolve(attachment); await drain(); assert.deepEqual(released, ['opaque-image']);
  h.host.dispose(); assert.equal(c.size(), 0);
});

test('模拟接收方有自己的截止时间，旧会话取消不能影响新请求', async () => {
  const c = clock(), host = createDemoHost({ runtime: c.runtime, scheduler: { set: c.runtime.setTimer, clear: c.runtime.clearTimer } });
  const sessionId = host.getSnapshot().sessionId;
  host.setHold();
  const request = { protocol: PROTOCOL, sessionId, requestId: 'receiver-only', expiresAtMs: c.runtime.epochNow() + 50, type: 'UserInput', payload: { kind: 'message', recipient: 'all', text: '有界等待', attachments: [] } };
  const pending = host.transport.request(request); await drain(); c.advance(51); const receipt = await pending;
  assert.equal(receipt.payload.error.code, 'timeout'); assert.equal(host.stats().jobs, 0);
  host.reset();
  await assert.rejects(host.transport.cancel({ ...request, requestId: 'cancel-old', expiresAtMs: c.runtime.epochNow() + 100, type: 'CancelRequest', payload: { requestId: 'receiver-only' } }), { code: 'stale' });
  host.dispose(); assert.equal(c.size(), 0);
});
