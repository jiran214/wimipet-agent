import { VERSION, validateInput, validateImage, publicError } from './protocol.js';
export { VERSION, validateImage };
export const BUBBLE_POLICY = Object.freeze({ max: 3, lifetime: 10000, width: 250, height: 156, gap: 12 });
export const recipientValid = (recipient, roles) => recipient === 'all' || roles.some(role => role.instanceId === recipient && role.interactive);

// 只接受宿主已经映射为舞台 CSS 像素的位置；epoch/replay 时间不在刷新时重新赋值。
export function layoutBubbles(messages, roles, viewport, now, panelOpen, policy = BUBBLE_POLICY) {
  if (panelOpen || viewport.width < 80 || viewport.height < 100) return [];
  const width = Math.min(policy.width, viewport.width - 24), height = Math.min(policy.height, viewport.height - 24);
  const candidates = [...messages].sort((a, b) => a.seq - b.seq).filter(message => {
    const role = roles.find(item => item.instanceId === message.speaker.instanceId);
    return role?.position.visible && now - message.atMs < policy.lifetime && now >= message.atMs;
  }).slice(-policy.max);
  const placed = [];
  for (const message of candidates) {
    const position = roles.find(item => item.instanceId === message.speaker.instanceId).position;
    const x = Math.max(12, Math.min(viewport.width - width - 12, position.x - width / 2));
    const preferred = Math.max(12, Math.min(viewport.height - height - 12, position.y - height - 36));
    const ys = [preferred, ...Array.from({ length: Math.ceil(viewport.height / (height + policy.gap)) }, (_, i) => 12 + i * (height + policy.gap))];
    const y = ys.find(top => top + height <= viewport.height - 12 && !placed.some(other => x < other.x + width + policy.gap && x + width + policy.gap > other.x && top < other.y + height + policy.gap && top + height + policy.gap > other.y));
    if (y !== undefined) placed.push({ message, x, y, width, height });
  }
  return placed;
}

export function createController(host, changed = () => {}) {
  let disposed = false, generation = 0, sessionId;
  const initial = () => ({ panelOpen: false, recipient: 'all', kind: 'message', draft: '', attachments: [], pending: false, preparing: false, notice: '', error: null, seen: new Set() });
  const state = initial();
  const publish = () => { if (!disposed) changed(); };
  const resetState = () => { generation++; state.attachments.forEach(item => host.releaseDraft(item)); Object.assign(state, initial()); };
  const unsubscribe = host.subscribe(() => {
    const next = host.getSnapshot().sessionId;
    if (sessionId && next !== sessionId) resetState();
    sessionId = next; publish();
  });
  sessionId = host.getSnapshot().sessionId;
  return {
    state, snapshot: () => host.getSnapshot(),
    update(values) { if (disposed) return; Object.assign(state, values); publish(); },
    async send() {
      if (state.pending || state.preparing || disposed) return false;
      try { validateInput({ kind: state.kind, recipient: state.recipient, text: state.draft, attachments: state.attachments }, host.getSnapshot().roles, host.limits); }
      catch (error) { state.error = publicError(error); state.notice = `${error.message}；草稿已保留。`; publish(); return false; }
      const epoch = generation, currentSession = sessionId;
      const input = { requestId: crypto.randomUUID(), kind: state.kind, recipient: state.recipient, text: state.draft, attachments: [...state.attachments] };
      state.pending = true; state.error = null; state.notice = '正在提交…'; publish();
      try {
        const receipt = await host.submit(input);
        if (disposed || epoch !== generation || currentSession !== host.getSnapshot().sessionId) return false;
        if (receipt.accepted !== true) throw new Error('invalid receipt');
        state.draft = ''; state.attachments = []; state.notice = '上游已受理；回复与处理结果以获准消息源为准。'; return true;
      } catch (error) {
        if (!disposed && epoch === generation) { state.error = publicError(error); state.notice = `提交失败 [${state.error.code}]，草稿与图片已保留。`; }
        return false;
      } finally { if (!disposed && epoch === generation) { state.pending = false; publish(); } }
    },
    resetPresentation(resetHost = () => {}) { resetState(); host.invalidateDrafts(); resetHost(); sessionId = host.getSnapshot().sessionId; publish(); },
    dispose() { if (disposed) return; disposed = true; generation++; unsubscribe(); host.invalidateDrafts(); state.attachments = []; }
  };
}
