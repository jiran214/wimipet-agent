import test from 'node:test';
import assert from 'node:assert/strict';
import { createController, layoutBubbles, validateImage } from '../src/model.js';
import { createDemoHost } from '../demo/host.js';

test('只读取获准快照；面板切换不写入或克隆消息源；销毁退订', () => {
  const messages = Object.freeze([{ id: 'allowed', text: '公开内容' }]);
  const snapshot = { roles: [], messages }; let subscribers = 0;
  const controller = createController({ getSnapshot: () => snapshot, subscribe: () => { subscribers++; return () => subscribers--; } });
  controller.update({ panelOpen: true }); controller.update({ panelOpen: false });
  assert.equal(controller.snapshot().messages, messages); assert.equal(messages.length, 1);
  controller.dispose(); assert.equal(subscribers, 0);
});

test('指定角色、故事与附件按原请求提交；失败保留草稿，重试成功清空', async () => {
  let reject = true, captured;
  const controller = createController({ getSnapshot: () => ({ roles: [{ id: 'a', interactive: true }] }), subscribe: () => () => {}, submit: async request => { captured = request; if (reject) throw new Error('failure'); } });
  controller.update({ recipient: 'a', kind: 'story-event', draft: '一阵风吹来', attachments: [{ id: 'image' }] });
  assert.equal(await controller.send(), false); assert.equal(controller.state.draft, '一阵风吹来');
  reject = false; assert.equal(await controller.send(), true);
  assert.equal(captured.recipient, 'a'); assert.equal(captured.kind, 'story-event'); assert.equal(captured.attachments[0].id, 'image'); assert.equal(controller.state.draft, '');
  controller.dispose();
});

test('移除的接收对象不会退化成广播；全体发送不要求角色存在', async () => {
  let calls = 0;
  const controller = createController({ getSnapshot: () => ({ roles: [] }), subscribe: () => () => {}, submit: async request => { calls++; assert.equal(request.recipient, 'all'); } });
  controller.update({ recipient: 'removed', draft: '保留' });
  assert.equal(await controller.send(), false); assert.equal(calls, 0); assert.equal(controller.state.draft, '保留');
  controller.update({ recipient: 'all' }); assert.equal(await controller.send(), true); assert.equal(calls, 1); controller.dispose();
});

test('重复提交被阻止；重置后迟到成功不会清除新草稿', async () => {
  let finish, calls = 0;
  const controller = createController({ getSnapshot: () => ({ roles: [] }), subscribe: () => () => {}, submit: () => { calls++; return new Promise(resolve => finish = resolve); } });
  controller.update({ draft: '旧内容' }); const first = controller.send(); await controller.send(); assert.equal(calls, 1);
  controller.resetPresentation(); controller.update({ draft: '新草稿' }); finish(); await first;
  assert.equal(controller.state.draft, '新草稿'); assert.equal(controller.state.pending, false); controller.dispose();
});

test('重置先清理展示再切换宿主，订阅不会看见旧草稿；销毁后迟到结果不发布', async () => {
  let finish, publications = 0;
  const controller = createController({ getSnapshot: () => ({ roles: [] }), subscribe: () => () => {}, submit: () => new Promise(resolve => finish = resolve) }, () => publications++);
  controller.update({ draft: '旧草稿', panelOpen: true, kind: 'story-event', attachments: [{ id: 'old' }] });
  controller.resetPresentation(() => { assert.equal(controller.state.attachments.length, 0); assert.equal(controller.state.panelOpen, false); assert.equal(controller.state.kind, 'message'); });
  controller.update({ draft: '等待' }); const pending = controller.send(); controller.dispose();
  const count = publications; finish(); await pending; assert.equal(publications, count);
});

test('密集气泡有界、不重叠、保序；展开面板只展示面板；移除和过期不产生气泡', () => {
  const roles = Array.from({ length: 6 }, (_, i) => ({ id: `${i}`, x: i % 2 ? -900 : 9999, y: 0, visible: true }));
  const messages = roles.map((role, i) => ({ id: `${i}`, speakerId: role.id, at: 1000 }));
  const viewport = { width: 600, height: 400 };
  const bubbles = layoutBubbles(messages, roles, viewport, 2000, false);
  assert.ok(bubbles.length > 0 && bubbles.length <= 3);
  for (const box of bubbles) { assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= 600 && box.y + box.height <= 400); for (const other of bubbles) if (other !== box) assert.ok(box.x + box.width <= other.x || other.x + other.width <= box.x || box.y + box.height <= other.y || other.y + other.height <= box.y); }
  assert.deepEqual(layoutBubbles(messages, roles, viewport, 2000, true), []);
  assert.deepEqual(layoutBubbles(messages, [], viewport, 2000, false), []);
  assert.deepEqual(layoutBubbles(messages, roles, viewport, 99999, false), []);
});

test('附件类型与大小边界；拒绝伪装的普通文件 MIME', () => {
  validateImage({ type: 'image/png', size: 5 * 1024 * 1024 });
  assert.throws(() => validateImage({ type: 'image/svg+xml', size: 1 }));
  assert.throws(() => validateImage({ type: 'image/png', size: 5 * 1024 * 1024 + 1 }));
});

test('替身固定样例、移除后历史身份、设置确认及资源停止', async () => {
  const host = createDemoHost();
  while (host.next()) {}
  const snapshot = host.getSnapshot(); assert.equal(snapshot.messages.length, 7); assert.equal(snapshot.roles.length, 2);
  assert.equal(snapshot.messages.find(message => message.id === 'fixture-6').speakerName, '余白');
  const promise = host.changeSettings({ observationPaused: true, frequency: 'low' });
  assert.equal(host.getSnapshot().settings.observationPaused, false); await promise;
  assert.equal(host.getSnapshot().settings.observationPaused, true);
  host.failNext = true; await assert.rejects(host.changeSettings({ observationPaused: false, frequency: 'balanced' })); assert.equal(host.getSnapshot().settings.observationPaused, true);
  const pending = host.submit({ requestId: 'test', recipient: 'all', text: 'x', attachments: [] }); host.reset();
  await assert.rejects(pending); assert.equal(host.getSnapshot().messages.length, 1);
  host.dispose(); assert.equal(host.next(), false);
});
