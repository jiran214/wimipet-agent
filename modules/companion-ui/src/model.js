export const VERSION = '0.1.0';
export const BUBBLE_POLICY = Object.freeze({ max: 3, lifetime: 10000, width: 250, height: 156, gap: 12 });

export function recipientValid(recipient, roles) {
  return recipient === 'all' || roles.some(role => role.id === recipient && role.interactive);
}

export function validateImage(file) {
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) throw new Error('仅支持 PNG、JPEG、WebP、GIF 图片');
  if (file.size > 5 * 1024 * 1024) throw new Error('图片不能超过 5 MB');
}

// 坐标已由宿主转换为舞台 CSS 像素。候选位置逐行避让，放不下则留给历史面板。
export function layoutBubbles(messages, roles, viewport, now, panelOpen, policy = BUBBLE_POLICY) {
  if (panelOpen || viewport.width < 80 || viewport.height < 100) return [];
  const width = Math.min(policy.width, viewport.width - 24);
  const height = Math.min(policy.height, viewport.height - 24);
  const candidates = messages.filter(message => {
    const role = roles.find(item => item.id === message.speakerId);
    return role?.visible && now - message.at < policy.lifetime && now >= message.at;
  }).slice(-policy.max);
  const placed = [];
  for (const message of candidates) {
    const role = roles.find(item => item.id === message.speakerId);
    const x = Math.max(12, Math.min(viewport.width - width - 12, role.x - width / 2));
    const preferred = Math.max(12, Math.min(viewport.height - height - 12, role.y - height - 36));
    const ys = [preferred, ...Array.from({ length: Math.ceil(viewport.height / (height + policy.gap)) }, (_, i) => 12 + i * (height + policy.gap))];
    const y = ys.find(top => top + height <= viewport.height - 12 && !placed.some(other => x < other.x + width + policy.gap && x + width + policy.gap > other.x && top < other.y + height + policy.gap && top + height + policy.gap > other.y));
    if (y !== undefined) placed.push({ message, x, y, width, height });
  }
  return placed;
}

// 只拥有展示状态；snapshot 始终直接读取获准消息源。
export function createController(host, changed = () => {}) {
  let disposed = false;
  let generation = 0;
  let counter = 0;
  const state = { panelOpen: false, recipient: 'all', kind: 'message', draft: '', attachments: [], pending: false, preparing: false, notice: '', seen: new Set() };
  const publish = () => { if (!disposed) changed(); };
  const unsubscribe = host.subscribe(publish);
  return {
    state,
    snapshot: () => host.getSnapshot(),
    update(values) { Object.assign(state, values); publish(); },
    async send() {
      if (state.pending || state.preparing || disposed) return false;
      if (!recipientValid(state.recipient, host.getSnapshot().roles)) { state.notice = '接收角色已离开，请重新选择；草稿已保留。'; publish(); return false; }
      if (!state.draft.trim() && !state.attachments.length) { state.notice = '请先输入内容或添加图片。'; publish(); return false; }
      const epoch = generation;
      const request = { requestId: `ui-${Date.now()}-${++counter}`, kind: state.kind, recipient: state.recipient, text: state.draft.trim(), attachments: [...state.attachments] };
      state.pending = true; state.notice = '正在提交…'; publish();
      try {
        await host.submit(request);
        if (disposed || epoch !== generation) return false;
        state.draft = ''; state.attachments = []; state.notice = '上游已受理；回复与处理结果以消息源为准。';
        return true;
      } catch (error) {
        if (!disposed && epoch === generation) state.notice = '提交失败，草稿与图片已保留。可修正后重试。';
        return false;
      } finally {
        if (!disposed && epoch === generation) { state.pending = false; publish(); }
      }
    },
    resetPresentation(resetHost = () => {}) { generation++; Object.assign(state, { panelOpen: false, kind: 'message', recipient: 'all', draft: '', attachments: [], pending: false, preparing: false, notice: '', seen: new Set() }); resetHost(); publish(); },
    dispose() { disposed = true; generation++; unsubscribe(); }
  };
}
