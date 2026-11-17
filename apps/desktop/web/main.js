import {createCompanionHost,mountCompanion} from '/ui/index.js';
import {CAPABILITIES,LIMITS} from '/ui/protocol.js';
const status=document.querySelector('#status');
async function api(path,data) {
  const response=await fetch(path,data===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok) throw new Error(result.error||'连接暂时中断');
  return result;
}
const listeners=new Set();
let latest=await api('/api/state');
const resources={
  preview:ref=>'/resource/'+encodeURIComponent(ref),
  async prepare(file,{sessionId}) {
    const dataBase64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
    return api('/api/prepare',{dataBase64,mime:file.type,name:file.name,sessionId});
  },
  // References are conservatively retained until this session ends, including timed-out submissions.
  cancelPreparation(){}, releaseDraft(contentRef){api('/api/release',{contentRef}).catch(()=>{});}, retainInFlight(){}, finishRequest(){}, retainSubmitted(){}
};
const transport={
  declaration:{protocol:'wimipet/1',module:'assembly',implementationVersion:'0.1.0',capabilities:CAPABILITIES,limits:LIMITS},
  request(request) {
    if(request.type==='OpenStudio') {
      window.open('http://127.0.0.1:'+latest.studioPort,'wimipet-studio','noopener');
      return Promise.resolve({...request,payload:{accepted:true}});
    }
    return api('/api/dispatch',request);
  },
  cancel:request=>api('/api/dispatch',request)
};
function projectedView(){
  const view=latest.view, jobs=view.processing.tasks;
  return {...view,processing:jobs.some(j=>['queued','running','requested'].includes(j.status))?'working':['failed','timeout'].includes(jobs.at(-1)?.status)?'failed':'idle'};
}
function projectedScene(){
  const bounds=document.querySelector('.stage')?.getBoundingClientRect();
  return {...latest.scene,roles:latest.scene.roles.map(role=>({...role,position:{...role.position,x:role.position.x*(bounds?.width||900)/900,y:role.position.y*(bounds?.height||450)/450}}))};
}
const host=createCompanionHost({timeoutMs:90000,conversation:{getView:projectedView,subscribe:(_,fn)=>{listeners.add(fn);return()=>listeners.delete(fn);}},presentation:{getSnapshot:projectedScene,subscribe:()=>()=>{}},transport,resources});
const unmount=mountCompanion(document.querySelector('#app'),host);
let active=true;
async function poll(){
  try {latest=await api('/api/state');for(const listener of listeners)listener();}
  catch {if(active)status.textContent='连接已断开，请确认启动窗口仍在运行。';}
  if(active)setTimeout(poll,250);
}
poll();
// Render A's current sequence frame independently of message polling.
const frames=setInterval(()=>{document.querySelectorAll('.stage-role img').forEach(img=>{img.src=img.src.split('?')[0]+'?t='+Date.now();});},100);
for(const [id,path] of [['auto','/api/auto'],['stop','/api/stop']])document.querySelector('#'+id).onclick=async()=>{
  try{const receipt=await api(path,{});status.textContent=receipt.payload?.error?'操作未被接受，请稍后重试。':id==='auto'?'大家开始接话了。':'已停止接话。';}catch(error){status.textContent=error.message;}
};
document.querySelector('#package').onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  try{if(file.size>32*1048576)throw new Error('角色包不能超过 32MiB');await api('/api/package',JSON.parse(await file.text()));status.textContent='新伙伴已加入，可以选择它聊天了。';}catch{status.textContent='角色包未能加载，请从角色制作台导出已确认的角色包。';}
  event.target.value='';
};
window.addEventListener('pagehide',()=>{active=false;clearInterval(frames);unmount.dispose();host.dispose();});
document.querySelector('#exit').onclick=async()=>{
  try{await api('/api/exit',{});active=false;clearInterval(frames);unmount.dispose();host.dispose();status.textContent='伙伴们已休息，程序已退出。可以关闭这个页面。';}catch(error){status.textContent=error.message;}
};
