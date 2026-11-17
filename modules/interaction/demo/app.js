const $ = id => document.getElementById(id);
let state, lastInput, renderedRoles = '', lastMessages = '', noticeTimer;
function notice(text) { $('notice').textContent=text; $('notice').style.display='block'; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('notice').style.display='none',4000); }
async function api(path, data) {
  try { const response=await fetch('/api/'+path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); const value=await response.json(); if(!response.ok) throw Error(value.error||'请求失败'); return value; }
  catch(error) { notice(error.message); throw error; }
}
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function names(id) { return state.roles.find(r=>r.id===id)?.name || ({user:'你',director:'导演',environment:'模拟环境',body:'假身体反馈'}[id]||id); }
async function command(path, value={}) { try { state=await api(path,value); render(); } catch {} }
function render() {
  $('mode').textContent=state.mode;
  const ids=state.roles.map(r=>r.id).join();
  if(ids!==renderedRoles) {
    renderedRoles=ids; $('roles').replaceChildren();
    for(const id of ['target','debug-role']) { const old=$(id).value; $(id).replaceChildren(); if(id==='target')$(id).append(new Option('全体 · 公开','all')); for(const role of state.roles) $(id).append(new Option(role.name+(id==='target'?' · 私聊':''),role.id)); if([...$(id).options].some(o=>o.value===old))$(id).value=old; }
    for(const role of state.roles) {
      const card=el('div',undefined,'role'), title=el('h3',role.name); title.append(el('span',role.id)); card.append(title);
      const label=el('label','人设快照'), text=el('textarea'); text.rows=3; text.value=role.persona; label.append(text); card.append(label);
      const row=el('div',undefined,'row'), behavior=el('select'); behavior.setAttribute('aria-label',role.name+'模拟模型行为'); for(const [v,t] of [['normal','正常固定回复'],['fail','模拟失败'],['timeout','模拟超时'],['silent','保持安静']])behavior.append(new Option(t,v));
      const save=el('button','应用'), remove=el('button','移除'); save.onclick=()=>command('role',{id:role.id,persona:text.value,behavior:behavior.value}); remove.onclick=()=>command('remove',{id:role.id}); row.append(behavior,save,remove);card.append(row);$('roles').append(card);
    }
  }
  const signature=JSON.stringify(state.messages);
  if(signature!==lastMessages) {
    lastMessages=signature;
    if(state.messages.length) $('messages').replaceChildren();
    else $('messages').replaceChildren(el('p','今天的共同经历，从你的一句话开始。','empty'));
    for(const m of state.messages) {
      const node=el('article',undefined,'message '+(m.audience!=='all'?'private':m.sender==='user'?'user':''));
      const meta=el('div',undefined,'meta'); meta.append(el('span',`#${m.seq} ${names(m.sender)}`),el('span',m.audience==='all'?'公开':`仅 ${m.audience.map(names).join('、')}`)); node.append(meta,el('p',m.text));
      if(m.image){const img=el('img');img.src=m.image.startsWith('data:')?m.image:'/'+m.image;img.alt=m.image.startsWith('data:')?'用户主动分享的图片':'自带固定图片样例';node.append(img);} $('messages').append(node);
    }
    $('messages').scrollTop=$('messages').scrollHeight;
  }
  $('count').textContent=state.messages.length+' 条消息'; $('auto-status').textContent=state.auto?'接话进行中':'自动交流已停止';
  $('context').textContent=JSON.stringify(state.contexts[$('debug-role').value]||[],null,2);
  $('jobs').replaceChildren(...state.jobs.slice(-12).reverse().map(j=>el('div',`${names(j.role)} · ${j.status} · ${j.id.slice(0,6)}${j.error?' / '+j.error:''}`, 'job')));
  $('actions').replaceChildren(...state.actions.slice(-8).reverse().map(a=>{ const node=el('div',`${names(a.role)} → ${a.action} · ${a.status}`,'action'); for(const [status,title] of (a.status==='requested'?[['accepted','接受'],['rejected','拒绝']]:a.status==='accepted'?[['completed','完成'],['rejected','拒绝']]:[])) {const b=el('button',title);b.onclick=()=>command('feedback',{id:a.id,status});node.append(b);} return node; }));
}
function send(kind='text', image) {const text=$('text').value.trim()||(kind==='image'?'分享一张窗边植物的图片':kind==='story'?'窗外下雨了，大家决定一起照顾一盆新来的绿植。':''); if(!text)return notice('先写一句话吧'); lastInput={text,kind,targets:$('target').value==='all'?null:[$('target').value],...(image?{image}: {})}; command('send',lastInput);$('text').value='';}
$('send').onclick=()=>send(); $('image').onclick=()=>send('image','share.png'); $('upload').onclick=()=>$('file').click(); $('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;if(!['image/png','image/jpeg'].includes(file.type)||file.size>2*1024*1024)return notice('请选择不超过 2MB 的 PNG / JPEG'); const reader=new FileReader();reader.onload=()=>send('image',reader.result);reader.readAsDataURL(file);$('file').value='';}; $('story').onclick=()=>send('story'); $('replay').onclick=()=>lastInput?command('send',lastInput):notice('尚无可重放输入');
$('text').onkeydown=e=>{if(e.ctrlKey&&e.key==='Enter')send();};
$('auto').onclick=()=>command('auto',{rounds:Number($('rounds').value)}); $('stop').onclick=()=>command('stop');
$('reset').onclick=async()=>{await command('reset');lastInput=null;renderedRoles='';$('paused').checked=false;$('scope').value='active-window';$('cooldown').value=10;render();notice('样例会话已恢复；未触及用户文件');};
$('observe').onclick=()=>command('observe');$('window').onclick=()=>command('window');
for(const id of ['paused','scope','cooldown'])$(id).onchange=()=>command('observation',{paused:$('paused').checked,scope:$('scope').value,cooldown:Number($('cooldown').value)});
$('debug-role').onchange=()=>render();
$('export').onclick=()=>{const safeState=JSON.parse(JSON.stringify(state,(key,value)=>key==='image'&&typeof value==='string'&&value.startsWith('data:')?'[用户上传图片已省略]':value));const report={scenario:'互动实验室/固定绿植样例 v1',version:state.version,expected:'私聊隔离、自动交流有界、错误可恢复、动作反馈准确',actual:$('notes').value,recordedAt:new Date().toISOString(),...safeState};const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='interaction-diagnostics.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function poll(){try{state=await api('state');render();}catch{}setTimeout(poll,600);}poll();
