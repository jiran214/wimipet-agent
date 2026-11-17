const $ = id => document.getElementById(id);
let state, view, lastInput, attachment=null, draftRequest=null, renderedRoles='', lastMessages='', noticeTimer;
function notice(text){$('notice').textContent=text;$('notice').style.display='block';clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').style.display='none',5000);}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,data){const response=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw Error(value.error?.message||'请求失败');return value;}
function check(receipt){if(!receipt.payload.accepted){const error=Error(receipt.payload.error.code+' · '+receipt.payload.error.message);error.code=receipt.payload.error.code;throw error;}return receipt;}
async function command(path,data={}){try{const receipt=check(await api(path,data));await refresh();return receipt;}catch(error){notice(error.message);await refresh();return null;}}
function name(id){return state.roles.find(r=>r.instanceId===id)?.name||id||'画面采集';}
function request(type,payload){return{protocol:'wimipet/1',sessionId:view.sessionId,requestId:crypto.randomUUID(),expiresAtMs:Date.now()+60000,type,payload};}
function render(){
  $('mode').textContent=state.mode;
  $('identity').textContent=`${state.implementationVersion} · ${state.sourceInstanceId.slice(0,8)} · 会话 ${view.sessionId.slice(0,8)}`;
  const ids=state.roles.map(r=>r.instanceId+":"+r.revision).join();
  if(ids!==renderedRoles){
    renderedRoles=ids;$('roles').replaceChildren();
    for(const id of ['target','debug-role']){const old=$(id).value;$(id).replaceChildren();if(id==='target')$(id).append(new Option('全体 · 公开','all'));for(const role of state.roles)$(id).append(new Option(`${role.name} / ${role.instanceId}${id==='target'?' · 私聊':''}`,role.instanceId));if([...$(id).options].some(o=>o.value===old))$(id).value=old;}
    for(const role of state.roles){
      const card=el('div',undefined,'role');card.append(el('h3',role.name),el('small',role.instanceId+' · '+role.packageId+' / r'+role.revision));
      const label=el('label','人设快照 / 最多 20000 码点'),text=el('textarea');text.rows=3;text.value=role.persona;label.append(text);card.append(label);
      const row=el('div',undefined,'row'),behavior=el('select');behavior.setAttribute('aria-label',role.instanceId+'模拟模型行为');for(const[v,t]of[['normal','正常固定回复'],['fail','模拟失败'],['timeout','模拟超时'],['silent','保持安静']])behavior.append(new Option(t,v));behavior.value=state.modelBehaviors?.[role.instanceId]||'normal';
      const save=el('button','应用'),remove=el('button','移除');save.onclick=()=>command('role',{instanceId:role.instanceId,persona:text.value,behavior:behavior.value});remove.onclick=()=>command('remove',{instanceId:role.instanceId});row.append(behavior,save,remove);card.append(row,el('p','已加载动作：'+role.supportedActions.map(a=>a.actionId).join('、'),'hint'));$('roles').append(card);
    }
  }
  const signature=JSON.stringify(view.messages);
  if(signature!==lastMessages){lastMessages=signature;$('messages').replaceChildren();if(!view.messages.length)$('messages').append(el('p','从一句话，或一张没有正文的图片开始。','empty'));
    for(const m of view.messages){const node=el('article',undefined,'message '+(m.audience!=='all'?'private':m.speaker.type==='user'?'user':'')),meta=el('div',undefined,'meta');meta.append(el('span',`#${m.seq} ${m.speaker.name}`),el('span',m.audience==='all'?'公开':`仅 ${m.audience.map(name).join('、')}`));node.append(meta,el('p',m.text));const when=el('time',new Date(m.atMs).toLocaleTimeString('zh-CN'));when.dateTime=new Date(m.atMs).toISOString();node.append(when);for(const item of m.attachments){const img=el('img');img.src='/resource/'+item.contentRef;img.alt=item.name;node.append(img);}$('messages').append(node);}$('messages').scrollTop=$('messages').scrollHeight;
  }
  $('count').textContent=view.messages.length+' 条消息';$('auto-status').textContent=view.processing.auto?'接话进行中':'自动交流已停止';
  $('context').textContent=JSON.stringify(state.contexts[$('debug-role').value]||[],null,2);
  $('jobs').replaceChildren(...view.processing.tasks.slice(-12).reverse().map(j=>{const n=el('div',`${name(j.instanceId)} · ${j.status} · ${j.requestId.slice(0,8)}${j.error?' / '+j.error.code:''}`,'job');if(j.status==='running'){const b=el('button','取消此请求');b.onclick=()=>command('cancel',{requestId:j.requestId});n.append(b);}return n;}));
  const transitions={requested:['queued','accepted','rejected'],queued:['accepted','rejected','interrupted','failed'],accepted:['completed','interrupted','failed']};const labels={queued:'排队',accepted:'接受',rejected:'拒绝',completed:'完成',interrupted:'打断',failed:'失败'};
  $('actions').replaceChildren(...view.processing.actions.slice(-8).reverse().map(a=>{const n=el('div',`${name(a.instanceId)} → ${a.actionId} · ${a.status}`,'action');n.append(el('small',a.history.join(' → ')));for(const status of transitions[a.status]||[]){if(status==='completed'&&a.loop)continue;const b=el('button',labels[status]);b.onclick=()=>command('feedback',{requestId:a.requestId,status});n.append(b);}if(a.requestError)n.append(el('p',a.requestError.message));return n;}));
  const obs=view.processing.observation,actual=obs.confirmed;$('capture-behavior').value=state.captureBehavior||'normal';
  $('policy-status').textContent=obs.error?`同步失败：${obs.error.code}；分析保持暂停；最后确认 ${actual?"r"+actual.policyRevision+(actual.paused?" 暂停":" 允许观察"):"无"}`:obs.requested?`同步中 r${obs.requested.policyRevision}；等待采集端确认，分析已暂停`:actual?`已确认 r${actual.policyRevision} · ${actual.paused?'暂停':'允许观察'} · ${actual.cooldownMs}ms`:'尚未收到采集端确认';
  if(actual&&(!obs.requested||obs.error)){$('paused').checked=actual.paused;$('scope').value=actual.scope;$('cooldown').value=actual.cooldownMs/1000;}
  $('draft-attachment').replaceChildren();if(attachment){const img=el('img');img.src='/resource/'+attachment.contentRef;img.alt=attachment.name;const b=el('button','取消附件');b.onclick=async()=>{await api('release',{contentRef:attachment.contentRef});attachment=null;draftRequest=null;render();};$('draft-attachment').append(img,b);}
}
async function refresh(){[view,state]=await Promise.all([api('view'),api('state')]);render();}
async function send(kind='message'){
  const text=$('text').value;if([...text].length>4000)return notice('正文最多 4000 Unicode 码点');if(!text.trim()&&!attachment)return notice('写一句话或选择图片');
  const payload={kind,recipient:$('target').value,text,attachments:attachment?[attachment]:[]};const signature=JSON.stringify(payload);
  if(!draftRequest||draftRequest.signature!==signature||draftRequest.envelope.sessionId!==view.sessionId)draftRequest={signature,envelope:request('UserInput',payload)};
  try{check(await api('dispatch',draftRequest.envelope));lastInput=structuredClone(payload);$('text').value='';attachment=null;draftRequest=null;await refresh();}catch(error){if(error.code==='stale')draftRequest=null;notice(error.message+'；草稿已保留');}
}
async function prepare(image){try{const result=await api('prepare',{image});if(attachment)await api('release',{contentRef:attachment.contentRef});attachment=result.attachment;draftRequest=null;render();}catch(error){notice(error.message);}}
$('send').onclick=()=>send();$('image').onclick=async()=>{await prepare('share.png');await send();};$('upload').onclick=()=>$('file').click();$('file').onchange=()=>{const file=$('file').files[0];if(!file)return;if(!['image/png','image/jpeg'].includes(file.type)||file.size>2*1048576)return notice('请选择不超过 2MiB 的 PNG / JPEG');const reader=new FileReader();reader.onload=()=>prepare(reader.result);reader.readAsDataURL(file);$('file').value='';};
$('story').onclick=()=>{if(!$('text').value)$('text').value='窗外下雨了，大家决定一起照顾一盆新来的绿植。';send('story-event');};
$('replay').onclick=async()=>{if(!lastInput)return notice('尚无可重放输入');try{check(await api('dispatch',request('UserInput',lastInput)));await refresh();}catch(error){notice(error.message);}};
$('text').onkeydown=e=>{if(e.ctrlKey&&e.key==='Enter')send();};$('auto').onclick=()=>command('auto',{rounds:Number($('rounds').value)});$('stop').onclick=()=>command('stop');
$('reset').onclick=async()=>{const receipt=await command('reset');if(receipt){attachment=null;lastInput=null;draftRequest=null;renderedRoles='';$('text').value='';render();notice('已建立新会话，旧引用和迟到结果已隔离');}};
$('observe').onclick=()=>command('observe');$('window').onclick=()=>command('window');
for(const id of ['paused','scope','cooldown','capture-behavior'])$(id).onchange=()=>command('observation',{paused:$('paused').checked,scope:$('scope').value,cooldownMs:Math.round(Number($('cooldown').value)*1000),behavior:$('capture-behavior').value});
$('debug-role').onchange=render;
$('export').onclick=()=>{const report={scenario:'C0002 / 共同角色包 v1 + 模拟加载器',expected:'图片无正文、获准视图、完整动作事实、策略确认、去重取消和新会话隔离',actual:$('notes').value,recordedAt:new Date().toISOString(),...state,view};const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='interaction-v1-diagnostics.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function poll(){try{await refresh();}catch(error){notice('连接中断：'+error.message);}setTimeout(poll,650);}poll();
