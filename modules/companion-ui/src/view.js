import { createController, layoutBubbles, validateImage } from './model.js';
import { publicError } from './protocol.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const avatar = role => role.previewUrl ? `<img class="avatar asset-avatar" src="${safeURL(role.previewUrl)}" alt="">` : `<span class="avatar ${['moss', 'clay', 'ink'].includes(role.tone) ? role.tone : 'ink'}" aria-hidden="true">${escape(role.mark || role.name?.slice(0, 1))}</span>`;
const safeURL = value => { if (!value) return ''; try { const url = new URL(value, location.href); return ['http:', 'https:', 'blob:'].includes(url.protocol) ? escape(url.href) : ''; } catch { return ''; } };

export function mountCompanion(root, host) {
  let disposed = false, attachEpoch = 0, settingsPending = false, settingsEpoch = 0, readCount = 0, lastSession;
  root.innerHTML = `
    <section class="companion" aria-label="陪伴界面">
      <header class="room-header"><div><span class="eyebrow">WIMIPET / COMPANION</span><h1>留一处，给日常。</h1></div><div class="header-tools"><span class="mode"></span><button data-action="studio">角色制作 ↗</button><button data-action="settings" aria-expanded="false">观察设置</button></div></header>
      <div class="settings" hidden><strong>观察设置 · 上游状态</strong><p class="observation"></p><label>观察频率 <select class="frequency"><option value="10000">10 秒</option><option value="60000">60 秒</option></select></label><button data-action="observation">暂停观察</button><p>这里只提交意图；状态以宿主返回为准。</p></div>
      <div class="room-grid"><section class="world" aria-label="角色位置与气泡"><div class="stage-caption"><span>01 / 网页舞台</span><span>宿主已映射位置 · 非原生桌面窗口</span></div><div class="stage"><div class="ambient"><span>在各自的位置，<br>继续同一段对话。</span></div><div class="role-layer"></div><div class="bubble-layer"></div></div><footer class="world-footer"><div class="role-list" aria-label="在场角色"></div><button data-action="panel" aria-expanded="false">展开对话 <span class="unread"></span> ↗</button></footer></section>
      <section class="conversation" hidden aria-label="对话面板"><header><div><span class="eyebrow">同一段对话</span><h2>把没读完的，慢慢读完。</h2></div><button data-action="panel" aria-label="收起对话面板">收起 ↙</button></header><div class="history" role="log" aria-label="消息历史" tabindex="0"></div><button class="latest" hidden>有新消息 · 返回最新</button></section></div>
      <form class="composer"><div class="composer-options"><label>发送给 <select class="recipient" aria-label="接收对象"></select></label><label>参与方式 <select class="kind"><option value="message">日常对话</option><option value="story-event">故事事件 / 条件</option></select></label><span class="processing"></span></div><label class="sr-only" for="companion-draft">消息或故事事件内容</label><textarea id="companion-draft" placeholder="说句话，或者为故事写下一个新的开场…" rows="2"></textarea><div class="attachments"></div><div class="composer-bottom"><div><label class="file-button">添加图片<input type="file" accept="image/png,image/jpeg" class="sr-only" aria-label="添加图片"></label><span class="hint">也可以拖入图片 · PNG/JPEG · 单张 / 2MiB · 正文 4000 码点</span></div><button type="button" data-action="cancel" hidden>取消提交</button><button class="primary" type="submit">发送 ↗</button></div><p class="notice" role="status" aria-live="polite"></p></form>
    </section>`;
  const $ = selector => root.querySelector(selector);
  const stage = $('.stage'), history = $('.history'), textarea = $('textarea');
  const controller = createController(host, render);
  function render() {
    if (disposed) return;
    const snapshot = controller.snapshot(), state = controller.state;
    if (lastSession && lastSession !== snapshot.sessionId) { attachEpoch++; settingsEpoch++; settingsPending = false; readCount = 0; }
    lastSession = snapshot.sessionId;
    $('.mode').textContent = `${snapshot.mode} · ${host.declaration.implementationVersion} · ${snapshot.sessionId.slice(-8)}`;
    $('.hint').textContent = `可拖入图片 · 最多 ${host.limits.attachmentCount} 张 / 每张 ${host.limits.attachmentBytes} 字节 · 正文 ${host.limits.textCodePoints} 码点`;
    $('input[type="file"]').accept = host.limits.attachmentMimes.join(',');
    $('.conversation').hidden = !state.panelOpen;
    $('.room-grid').classList.toggle('expanded', state.panelOpen);
    root.querySelectorAll('[data-action="panel"]').forEach(button => button.setAttribute('aria-expanded', String(state.panelOpen)));
    const unread = snapshot.messages.filter(message => !state.seen.has(message.messageId)).length;
    $('.unread').textContent = unread ? `(${unread})` : '';
    const previous = $('.recipient').value;
    $('.recipient').innerHTML = `<option value="all">全体角色</option>${snapshot.roles.map(role => `<option value="${escape(role.instanceId)}" ${role.interactive ? '' : 'disabled'}>${escape(role.name)}</option>`).join('')}${state.recipient !== 'all' && !snapshot.roles.some(role => role.instanceId === state.recipient) ? `<option value="${escape(state.recipient)}">已离开的角色 · 请重选</option>` : ''}`;
    $('.recipient').value = state.recipient || previous;
    $('.kind').value = state.kind;
    $('.role-list').innerHTML = snapshot.roles.length ? snapshot.roles.map(role => `<span>${avatar(role)}${escape(role.name)}</span>`).join('') : '<span>暂无在场角色</span>';
    const bounds = stage.getBoundingClientRect();
    $('.role-layer').innerHTML = snapshot.roles.filter(role => role.position.visible).map(role => `<div class="stage-role" style="left:${Math.max(38, Math.min(bounds.width - 38, role.position.x))}px;top:${Math.max(60, Math.min(bounds.height - 54, role.position.y))}px">${avatar(role)}<span>${escape(role.name)}</span></div>`).join('');
    const bubbles = layoutBubbles(snapshot.messages, snapshot.roles, bounds, host.now(), state.panelOpen);
    $('.bubble-layer').innerHTML = bubbles.map(({ message, x, y, width, height }) => `<button class="bubble" data-message="${escape(message.messageId)}" style="left:${x}px;top:${y}px;width:${width}px;height:${height}px"><small>${escape(message.speaker.name)} · #${message.seq}</small><span>${escape(message.text)}</span><em>展开完整对话 ↗</em></button>`).join('');
    if (state.panelOpen) {
      const bottom = history.scrollHeight - history.scrollTop - history.clientHeight < 50;
      const scroll = history.scrollTop;
      history.innerHTML = snapshot.messages.length ? snapshot.messages.map((message, index) => `<article class="message" data-id="${escape(message.messageId)}">${avatar(message.speaker)}<div><header><strong>${escape(message.speaker.name || '历史角色')}</strong><small>#${message.seq}${message.kind === 'story-event' ? ' · 故事事件' : ''} · ${message.audience === 'all' ? '全体' : escape(message.audience.map(id => snapshot.roles.find(role => role.instanceId === id)?.name || id).join('、'))}</small></header><p>${escape(message.text)}</p>${(message.attachments || []).map(item => `<a href="${safeURL(item.previewUrl)}" target="_blank" rel="noopener noreferrer"><img src="${safeURL(item.previewUrl)}" alt="${escape(item.name)}" loading="lazy"></a>`).join('')}</div></article>`).join('') : '<div class="empty">这里还没有消息。<br>从一句问候开始，也很好。</div>';
      if (bottom) { history.scrollTop = history.scrollHeight; markRead(); } else history.scrollTop = scroll;
      $('.latest').hidden = bottom || snapshot.messages.length === readCount;
    }
    $('.processing').textContent = snapshot.processing === 'working' ? '宿主处理中…' : snapshot.processing === 'failed' ? '宿主处理失败 · 可重试' : '准备好听你说';
    $('.notice').textContent = snapshot.connectionError ? `消息源错误 [${snapshot.connectionError.code}]，发送已停用。` : state.notice;
    $('[data-action="cancel"]').hidden = !state.pending && !state.preparing && !settingsPending;
    if (textarea.value !== state.draft) textarea.value = state.draft;
    textarea.disabled = state.pending;
    $('.primary').disabled = state.pending || state.preparing || Boolean(snapshot.connectionError);
    $('.primary').textContent = state.pending ? '正在提交…' : '发送 ↗';
    $('.recipient').disabled = state.pending;
    $('.kind').disabled = state.pending;
    $('input[type="file"]').disabled = state.pending || state.preparing || Boolean(snapshot.connectionError);
    $('.attachments').innerHTML = state.attachments.map(item => `<span class="attachment"><img src="${safeURL(item.previewUrl)}" alt=""><span>${escape(item.name)}</span><button type="button" data-remove="${escape(item.attachmentId)}" aria-label="移除 ${escape(item.name)}" ${state.pending ? 'disabled' : ''}>×</button></span>`).join('');
    $('.observation').textContent = `${snapshot.mode || '宿主'}：${snapshot.observationPolicy.paused ? '观察已暂停' : '观察进行中'} · 策略 #${snapshot.observationPolicy.policyRevision} / ${snapshot.observationPolicy.cooldownMs}ms${settingsPending ? ' · 等待确认…' : ''}`;
    const cooldown = String(snapshot.observationPolicy.cooldownMs);
    $('.frequency').innerHTML = `<option value="10000">10 秒</option><option value="60000">60 秒</option>${['10000', '60000'].includes(cooldown) ? '' : `<option value="${cooldown}">${cooldown} 毫秒</option>`}`;
    $('.frequency').value = cooldown;
    $('.frequency').disabled = settingsPending;
    $('[data-action="observation"]').disabled = settingsPending;
    $('[data-action="observation"]').textContent = snapshot.observationPolicy.paused ? '恢复观察' : '暂停观察';
  }
  function markRead() { const messages = controller.snapshot().messages; messages.forEach(message => controller.state.seen.add(message.messageId)); readCount = messages.length; $('.unread').textContent = ''; }
  async function addFiles(files) {
    if (controller.state.pending || controller.state.preparing) return;
    const epoch = attachEpoch;
    let validated = false;
    controller.update({ preparing: true, notice: '正在准备图片…' });
    try {
      files.forEach(file => validateImage(file, host.limits));
      if (files.length + controller.state.attachments.length > host.limits.attachmentCount) throw new Error(`协商上限为 ${host.limits.attachmentCount} 张；本次图片全部未添加`);
      validated = true;
      for (const file of files) {
        const attachment = await host.prepareAttachment(file);
        if (disposed || epoch !== attachEpoch) { host.releaseDraft(attachment); return; }
        controller.state.attachments.push(attachment);
      }
      controller.update({ notice: '图片已就绪；发送后交由宿主理解。' });
    } catch (error) { if (!disposed && epoch === attachEpoch) controller.update({ error: publicError(error), notice: validated ? `图片准备失败 [${publicError(error).code}]，草稿已保留。` : error.message }); }
    finally { if (!disposed && epoch === attachEpoch) controller.update({ preparing: false }); }
  }
  async function settings(paused, cooldownMs) {
    if (settingsPending) return;
    const epoch = settingsEpoch, session = controller.snapshot().sessionId;
    const current = controller.snapshot().observationPolicy;
    settingsPending = true; render();
    try {
      await host.changeSettings({ ...current, policyRevision: current.policyRevision + 1, paused, cooldownMs });
      if (epoch === settingsEpoch && !disposed && session === controller.snapshot().sessionId) controller.update({ notice: '设置意图已提交；仅上方上游确认策略代表实际生效。' });
    } catch (error) {
      if (epoch === settingsEpoch && !disposed && session === controller.snapshot().sessionId) controller.update({ notice: `设置未成功 [${publicError(error).code}]；保留上游实际状态。` });
    } finally { if (epoch === settingsEpoch && !disposed) { settingsPending = false; render(); } }
  }
  const abort = new AbortController();
  const on = (element, event, handler) => element.addEventListener(event, handler, { signal: abort.signal });
  on(root, 'click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    const action = button.dataset.action;
    host.onUiEvent?.({ kind: 'ui-action', action: action || (button.dataset.message ? 'open-message' : button.dataset.remove ? 'remove-attachment' : 'other') });
    if (action === 'panel' || button.dataset.message) { controller.update({ panelOpen: button.dataset.message ? true : !controller.state.panelOpen }); if (controller.state.panelOpen) { history.scrollTop = history.scrollHeight; markRead(); history.focus(); } }
    if (action === 'settings') { $('.settings').hidden = !$('.settings').hidden; button.setAttribute('aria-expanded', String(!$('.settings').hidden)); }
    if (action === 'studio') { const session = controller.snapshot().sessionId; host.openStudio().then(() => { if (!disposed && session === controller.snapshot().sessionId) controller.update({ notice: '制作入口请求已提交；是否真实打开取决于宿主。' }); }).catch(error => { if (!disposed && session === controller.snapshot().sessionId) controller.update({ notice: `制作入口未打开 [${publicError(error).code}]。` }); }); }
    if (action === 'observation') { const current = controller.snapshot().observationPolicy; settings(!current.paused, current.cooldownMs); }
    if (button.dataset.remove && !controller.state.pending) { const item = controller.state.attachments.find(item => item.attachmentId === button.dataset.remove); controller.update({ attachments: controller.state.attachments.filter(item => item.attachmentId !== button.dataset.remove) }); if (item) host.releaseDraft(item); }
    if (action === 'cancel') host.cancelPending();
  });
  on($('.frequency'), 'change', event => settings(controller.snapshot().observationPolicy.paused, Number(event.target.value)));
  on($('.recipient'), 'change', event => { const recipient = event.target.value; controller.update({ recipient }); host.onUiEvent?.({ kind: 'recipient', recipient }); });
  on($('.kind'), 'change', event => { const kind = event.target.value; controller.update({ kind }); host.onUiEvent?.({ kind: 'input-kind', inputKind: kind }); });
  on(textarea, 'input', event => { controller.state.draft = event.target.value; });
  on($('.composer'), 'submit', event => { event.preventDefault(); controller.send(); });
  on($('input[type="file"]'), 'change', event => { addFiles([...event.target.files]); event.target.value = ''; });
  on($('.composer'), 'dragover', event => { event.preventDefault(); $('.composer').classList.add('dragging'); });
  on($('.composer'), 'dragleave', () => $('.composer').classList.remove('dragging'));
  on($('.composer'), 'drop', event => { event.preventDefault(); $('.composer').classList.remove('dragging'); addFiles([...event.dataTransfer.files]); });
  on(history, 'scroll', () => { if (history.scrollHeight - history.scrollTop - history.clientHeight < 50) { markRead(); $('.latest').hidden = true; } });
  on($('.latest'), 'click', () => { history.scrollTop = history.scrollHeight; markRead(); $('.latest').hidden = true; });
  const resize = new ResizeObserver(render); resize.observe(stage);
  const timer = setInterval(() => { if (!controller.state.panelOpen) render(); }, 1000);
  render();
  return { controller, addFiles, refresh: render, reset(resetHost) { attachEpoch++; settingsEpoch++; settingsPending = false; readCount = 0; controller.resetPresentation(resetHost); }, dispose() { disposed = true; attachEpoch++; settingsEpoch++; clearInterval(timer); resize.disconnect(); abort.abort(); controller.dispose(); root.replaceChildren(); } };
}
