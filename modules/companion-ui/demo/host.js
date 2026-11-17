import { roles, events } from '../fixtures/scenes.js';
import { validateImage } from '../src/model.js';

export function createDemoHost() {
  const listeners = new Set();
  const urls = new Set();
  const timers = new Map();
  let snapshot, index, serial = 0, epoch = 0, closed = false, failNext = false;
  const log = [];
  function emit() { for (const listener of listeners) listener(); }
  function record(kind, fields = {}) { log.push({ step: log.length + 1, kind, ...fields }); emit(); }
  function cancel() { for (const [id, reject] of timers) { clearTimeout(id); reject(new Error('场景已重置')); } timers.clear(); }
  function delay() { return new Promise((resolve, reject) => { const id = setTimeout(() => { timers.delete(id); resolve(); }, 450); timers.set(id, reject); }); }
  function reset(scene = 'conversation') {
    epoch++; failNext = false; cancel(); for (const url of urls) URL.revokeObjectURL(url); urls.clear();
    index = 0; log.length = 0;
    snapshot = { sessionId: 'demo-room-v1', scene, roles: scene === 'empty' ? [] : structuredClone(roles), messages: [], settings: { observationPaused: false, frequency: 'balanced' }, processing: 'idle', mode: '模拟 · 未连接 A/B/C' };
    if (scene !== 'empty') next();
    record('reset', { scene });
  }
  function next() {
    if (closed || index >= events.length || snapshot.scene === 'empty') return false;
    const event = events[index++];
    if (event.move) Object.assign(snapshot.roles.find(role => role.id === event.move.id), event.move);
    const role = roles.find(item => item.id === event.speakerId);
    snapshot.messages.push({ id: `fixture-${index}`, speakerId: role.id, speakerName: role.name, mark: role.mark, tone: role.tone, text: event.text, kind: 'message', at: Date.now(), attachments: [] });
    if (event.remove) snapshot.roles = snapshot.roles.filter(role => role.id !== event.remove);
    record('fixture', { index }); return true;
  }
  reset();
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    log, next, reset,
    get failNext() { return failNext; },
    set failNext(value) { failNext = value; },
    async submit(request) {
      const current = epoch;
      record('submit', { requestId: request.requestId, recipient: request.recipient, inputKind: request.kind, textLength: request.text.length, attachmentCount: request.attachments.length });
      snapshot.processing = 'working'; emit();
      await delay();
      if (closed || current !== epoch) throw new Error('场景已变化');
      if (this.failNext) { this.failNext = false; snapshot.processing = 'failed'; record('failure', { code: 'DEMO_REJECTED' }); throw new Error('模拟失败'); }
      if (request.recipient !== 'all' && !snapshot.roles.some(role => role.id === request.recipient)) { snapshot.processing = 'failed'; record('failure', { code: 'RECIPIENT_GONE' }); throw new Error('角色离开'); }
      snapshot.messages.push({ ...request, id: `demo-user-${++serial}`, speakerId: 'user', speakerName: '你', mark: '我', tone: 'moss', at: Date.now() });
      snapshot.processing = 'idle'; record('accepted', { requestId: request.requestId });
    },
    async changeSettings(intent) {
      const current = epoch;
      record('settings-intent', { requestId: intent.requestId, observationPaused: intent.observationPaused, frequency: intent.frequency });
      await delay();
      if (closed || current !== epoch) throw new Error('场景已变化');
      if (this.failNext) { this.failNext = false; record('failure', { code: 'SETTINGS_REJECTED' }); throw new Error('模拟设置失败'); }
      snapshot.settings = { observationPaused: intent.observationPaused, frequency: intent.frequency }; record('settings-confirmed');
    },
    async prepareAttachment(file) {
      validateImage(file);
      const url = URL.createObjectURL(file); urls.add(url);
      return { id: `demo-image-${++serial}`, name: file.name, type: file.type, size: file.size, url };
    },
    openStudio() { record('open-studio', { result: '模拟捕获，未连接角色制作工具' }); },
    record,
    onUiEvent(event) { record(event.kind, event); },
    dispose() { closed = true; epoch++; cancel(); for (const url of urls) URL.revokeObjectURL(url); urls.clear(); listeners.clear(); }
  };
}
