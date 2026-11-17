import { mountCompanion, VERSION, PROTOCOL } from '../src/index.js';
import { createDemoHost } from './host.js';
const characterPackage = await fetch('/fixtures/v1/character-package.json').then(response => { if (!response.ok) throw new Error('共同样例未加载'); return response.json(); });
const host = createDemoHost({ characterPackage });
let ui = mountCompanion(document.querySelector('#app'), host), playback = null, stopped = false;
const $ = selector => document.querySelector(selector);
function pause() { clearInterval(playback); playback = null; $('#play').textContent = '播放'; }
function play() { if (stopped) restart(); pause(); $('#play').textContent = '暂停'; playback = setInterval(() => { if (!host.next()) { pause(); $('#demo-status').textContent = '固定样例播放结束。可从头回放或打开面板回看。'; } }, Number($('#speed').value)); }
function restart(scene = 'conversation', clockMode = 'live') { pause(); ui.reset(() => host.reset(scene, clockMode)); if (stopped) { ui = mountCompanion($('#app'), host); stopped = false; } $('#demo-status').textContent = scene === 'empty' ? '空场景；可提交文字查看回调，恢复初始场景可返回角色。' : '已恢复固定初始场景，仅清理体验数据。'; }
$('#play').onclick = () => playback ? pause() : play();
$('#step').onclick = () => { if (stopped) restart(); pause(); host.next(); };
$('#speed').onchange = () => { host.record('speed', { milliseconds: Number($('#speed').value) }); if (playback) play(); };
$('#replay').onclick = () => { restart('conversation', 'replay'); play(); };
$('#reset').onclick = () => restart();
$('#empty').onclick = () => restart('empty');
$('#fail').onclick = () => { host.setFailure(); $('#demo-status').textContent = '已设定：下一次消息或设置请求将失败。'; };
$('#hold').onclick = () => { host.setHold(); $('#demo-status').textContent = '已设定：下一次请求保持等待，可取消或等待 10 秒超时。'; };
$('#stop').onclick = () => { pause(); ui.dispose(); host.reset('empty'); stopped = true; $('#demo-status').textContent = '体验已停止：界面计时器、订阅和附件已清理。点击播放或恢复可重新开始。'; };
const unsubscribe = host.subscribeLog(() => { $('#callbacks').textContent = JSON.stringify(host.log.slice(-30), null, 2); });
$('#callbacks').textContent = JSON.stringify(host.log, null, 2);
$('#sample').onclick = async () => {
  if (stopped) restart();
  const response = await fetch('/fixtures/sample.png');
  const blob = await response.blob();
  await ui.addFiles([new File([blob], '窗边样例.png', { type: 'image/png' })]);
};
const redact = value => value.replace(/(?:sk-|Bearer\s+)[\w-]+/gi, '[已隐藏]').slice(0, 2000);
$('#export').onclick = () => {
  const data = { module: 'D', protocol: PROTOCOL, version: VERSION, sessionId: host.getSnapshot().sessionId, scenario: host.getSnapshot().scene, fixture: 'character-package@a24c406', mode: 'simulation', steps: host.log, expected: redact($('#expected').value), actual: redact($('#actual').value), limitations: ['未验证真实模型', '未验证原生窗口与跨模块兼容'] };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'companion-ui-feedback.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
window.addEventListener('pagehide', () => { pause(); ui.dispose(); unsubscribe(); host.dispose(); });
