const $ = id => document.getElementById(id);
let state = {}, mapState = {}, lastFrame = -1, busy = false, mapBusy = false, closed = false;
let streamRun = null, mapVersion = null, mapDirty = true, boxesDirty = true, resizeDirty = true;
let overlayKey = '', detectionKey = '', stateTimer = null, mapTimer = null;
const statusLabels = {idle:'待机',loading:'加载中',running:'运行中',paused:'已暂停',resetting:'刷新中',exited:'已退出',error:'运行异常'};
const num = (v, n=2) => Number.isFinite(v) ? v.toFixed(n) : '—';
const text = (id, value) => {const next=String(value ?? '—');if($(id).textContent!==next)$(id).textContent=next;};
const esc = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
for (const action of ['start','pause','reset','exit']) $(action).addEventListener('click', async()=>{
  $(action).disabled = true;
  try {
    const response = await fetch('/api/'+action,{method:'POST'}); if(!response.ok) throw Error('操作失败');
    if(action==='exit'){
      closed=true;clearInterval(stateTimer);clearInterval(mapTimer);stopStreams();
      state={status:'exited',message:'所有平台进程正在关闭',has_started:false,events:[]};render(state);
      text('connection','平台已退出');$('connection-dot').className='';
      text('event','已发送完整退出指令。再次使用请执行 bash start.sh。');
      for(const id of ['start','pause','reset','exit'])$(id).disabled=true;
    }else await poll();
  }
  catch(e) {text('event',e.message);}
});
async function poll(){
  if(busy||closed)return; busy=true;
  try {
    const r=await fetch('/api/state');if(!r.ok)throw Error('连接中断');const next=await r.json();if(closed)return;state=next;
    $('connection-dot').className='connected';text('connection','服务已连接');render(state);
  } catch(e){if(!closed){$('connection-dot').className='';text('connection','连接中断');text('event',e.message);}}
  finally{busy=false;}
}
function stopStreams(){for(const id of ['camera','overview'])$(id).removeAttribute('src');streamRun=null;}
function streams(s){
  if(!s.has_started){if(streamRun!==null)stopStreams();return;}
  if(streamRun===s.run_id)return;streamRun=s.run_id;
  $('camera').src='/api/stream.mjpg?view=front&run='+encodeURIComponent(s.run_id);
  $('overview').src='/api/stream.mjpg?view=overview&run='+encodeURIComponent(s.run_id);
}
async function pollMap(){
  if(mapBusy||closed||!state.has_started)return;mapBusy=true;
  const runId=state.run_id;
  try{
    const response=await fetch('/api/map');if(!response.ok)return;const next=await response.json();
    if(closed||!state.has_started||state.run_id!==runId)return;
    const version=next.map_seq??JSON.stringify([next.map_points?.length,next.trajectory?.length,next.position]);
    if(version!==mapVersion){mapState=next;mapVersion=version;mapDirty=true;}
  }catch(e){/* Camera and controls remain independent of a delayed map. */}
  finally{mapBusy=false;}
}
function render(s){
  const on=s.has_started;
  text('status',statusLabels[s.status]||s.status);text('message',s.message);
  $('status-dot').className='status-dot '+s.status;
  $('start').disabled=['loading','running','resetting','exited'].includes(s.status)||s.stop_reason==='MODEL_STOP'||s.stop_reason==='STEP_LIMIT';
  $('start').querySelector('span').textContent=s.status==='paused'?'继续仿真':'开始仿真';
  $('pause').disabled=s.status!=='running';$('reset').disabled=['loading','resetting','exited'].includes(s.status);$('exit').disabled=s.status==='exited';
  text('target',on?s.target:'—');text('instruction',on?'寻找目标物体 · 本地模型自主导航':'开始后随机加载场景与任务');
  text('scene',on?s.scene:'—');text('steps',on?`${s.steps} / ${s.max_steps}`:'—');text('altitude',on?'1.40 m':'—');
  for(const [i,k] of ['x','y','z'].entries())text('pos-'+k,on&&s.position?num(s.position[i],3):'—');
  text('loc-error',on&&s.slam_mean_error_m!=null?fullDecimal(s.slam_mean_error_m)+' m':'—');
  $('loc-error').title=on&&s.slam_mean_error_m!=null?`完整有效数字；原始数值 ${s.slam_mean_error_m}`:'';
  text('loc-state',on?(s.slam_mean_error_m===0?'当前误差确为 0':s.missing_tracking_frames?'丢跟踪 '+s.missing_tracking_frames+' 帧':'全部有效小数位'):'等待测量');
  text('tracking',on?(s.tracking==='IMU_ONLY'?'惯导接续':s.tracking==='OK'?'视觉 + 惯导':s.tracking||'初始化中'):'等待初始化');
  $('tracking').className='badge '+(s.tracking==='OK'?'ok':on?'lost':'');
  text('map-count',on?(s.map_point_count??mapState.map_points?.length??'—'):'—');
  if(!on){text('map-scale','世界坐标 / m');mapState={};mapVersion=null;mapDirty=true;}
  const controlHz=s.controller?.measured_hz;
  text('e2e-hz',on&&Number.isFinite(controlHz)?(controlHz<=20?'20':num(controlHz,2)):'—');
  text('frequency-kind','');text('performance-note','');
  $('e2e-hz').title='';
  text('recognition-model',on?(s.recognition_model||'初始化中'):'本地视觉推理');
  const boxes=(s.detections||[]).slice().sort((a,b)=>Number(b.is_target)-Number(a.is_target));
  const nextDetection=JSON.stringify([on,s.recognition_error,boxes]);
  if(nextDetection!==detectionKey){detectionKey=nextDetection;
    $('detections').innerHTML=!on?'<div class="text-empty">开始后显示类别与分数</div>':s.recognition_error?`<div class="text-empty">${esc(s.recognition_error)}</div>`:boxes.length?boxes.slice(0,5).map(b=>`<div class="detection-row ${b.is_target?'target':''}"><span><i></i>${esc(b.class_name||b.label)}${b.is_target?' · 目标':''}</span><span class="detection-confidence">${b.source?.includes('DINO')?'相似度 '+num(b.confidence??b.score,2):num((b.confidence??b.score??0)*100,1)+'%'}</span></div>`).join(''):'<div class="text-empty">当前画面未检出目标</div>';
  }
  $('camera-empty').hidden=on;$('camera-empty').style.display=on?'none':'flex';
  $('map-empty').style.display=on?'none':'flex';$('camera-hud').hidden=!on;$('action').hidden=!on;
  $('camera').hidden=!on;$('boxes').hidden=!on;$('map').hidden=!on;$('overview').hidden=!on;$('overview-empty').style.display=on?'none':'flex';
  streams(s);
  if(on){
    text('camera-time',`t = ${num(s.frame_timestamp)} s`);text('live-state',s.status==='running'?'LIVE':'PAUSED');text('action',s.current_action||'INITIALIZING');
    const nextOverlay=JSON.stringify([s.detection_seq,s.status]);
    if(nextOverlay!==overlayKey){overlayKey=nextOverlay;boxesDirty=true;}
  }else{lastFrame=-1;}
  const ev=s.events?.at(-1);text('event',s.error|| (ev?`${ev.time}  ${ev.text}`:'准备就绪，点击“开始仿真”启动一次课堂实验。'));
  text('run-id',on?'RUN '+s.run_id:'');
  text('box-note',on&&s.detection_time?`识别更新 ${Math.max(0,(Date.now()/1000-s.detection_time)).toFixed(1)} s 前`:'目标类别由 ObjectNav 任务提供');
}
function fit(canvas){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1,w=Math.round(r.width*d),h=Math.round(r.height*d);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}const c=canvas.getContext('2d');c.setTransform(d,0,0,d,0,0);c.clearRect(0,0,r.width,r.height);return[c,r.width,r.height];}
function drawBoxes(s){const[c,w,h]=fit($('boxes'));if(s.detection_time==null||(Date.now()/1000-s.detection_time>1.2&&s.status==='running'))return;
  for(const b of s.detections||[]){if(!b.xyxy)continue;let[x1,y1,x2,y2]=b.xyxy;const sx=w/640,sy=h/480;x1*=sx;x2*=sx;y1*=sy;y2*=sy;
    c.strokeStyle=b.is_target?'#54edba':'#7dc0f2';c.lineWidth=1.5;c.strokeRect(x1,y1,x2-x1,y2-y1);
    const score=b.source?.includes('DINO')?'sim '+num(b.confidence??b.score,2):num((b.confidence??b.score)*100,0)+'%';
    const label=`${b.class_name||b.label} ${score}`;c.font='11px Segoe UI';const tw=c.measureText(label).width;
    const ly=Math.max(17,y1);c.fillStyle='#102c3bdf';c.fillRect(x1,ly-18,tw+12,18);c.fillStyle=b.is_target?'#7ff5ca':'#b0dbff';c.fillText(label,x1+5,ly-5);
  }
}
function drawMap(s){const[c,w,h]=fit($('map')),points=s.map_points||[],trail=s.trajectory||[];
  let xmin=Infinity,xmax=-Infinity,zmin=Infinity,zmax=-Infinity;
  for(const p of points.concat(trail)){if(!p||!Number.isFinite(p[0])||!Number.isFinite(p[2]))continue;xmin=Math.min(xmin,p[0]);xmax=Math.max(xmax,p[0]);zmin=Math.min(zmin,p[2]);zmax=Math.max(zmax,p[2]);}
  if(!Number.isFinite(xmin))return;
  const span=Math.max(xmax-xmin,zmax-zmin,3),scale=Math.min(w-46,h-38)/span,cx=(xmin+xmax)/2,cz=(zmin+zmax)/2;
  const project=p=>[w/2+(p[0]-cx)*scale,h/2+(p[2]-cz)*scale];
  c.strokeStyle='#dde6ed';c.lineWidth=.6;const spacing=span>20?5:span>8?2:1;
  for(let x=Math.floor(xmin/spacing)*spacing;x<=xmax+spacing;x+=spacing){const u=project([x,0,0])[0];c.beginPath();c.moveTo(u,0);c.lineTo(u,h);c.stroke();}
  for(let z=Math.floor(zmin/spacing)*spacing;z<=zmax+spacing;z+=spacing){const v=project([0,0,z])[1];c.beginPath();c.moveTo(0,v);c.lineTo(w,v);c.stroke();}
  c.fillStyle='#9eafbc88';for(const p of points){if(p?.length<3)continue;const[x,y]=project(p);c.fillRect(x,y,1.5,1.5);}
  if(trail.length){c.strokeStyle='#0a9d94';c.lineWidth=1.8;c.beginPath();trail.forEach((p,i)=>{const[x,y]=project(p);i?c.lineTo(x,y):c.moveTo(x,y)});c.stroke();
    const[x0,y0]=project(trail[0]);c.fillStyle='white';c.strokeStyle='#0a9d94';c.beginPath();c.arc(x0,y0,3.5,0,2*Math.PI);c.fill();c.stroke();
    const[x,y]=project(trail.at(-1));c.fillStyle='#14a69820';c.beginPath();c.arc(x,y,11,0,2*Math.PI);c.fill();c.fillStyle='#079e90';c.beginPath();c.arc(x,y,4,0,2*Math.PI);c.fill();
  }
  c.fillStyle='#8298a8';c.font='9px Consolas';c.fillText(`+X →   +Z ↓   grid ${spacing} m`,13,h-11);text('map-scale',`网格 ${spacing} m`);
}
function paint(){
  if(state.has_started&&!closed){if(mapDirty||resizeDirty){drawMap(mapState);mapDirty=false;}if(boxesDirty||resizeDirty){drawBoxes(state);boxesDirty=false;}resizeDirty=false;}
  if(!closed)requestAnimationFrame(paint);
}
window.addEventListener('resize',()=>{resizeDirty=true;});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){resizeDirty=true;poll();pollMap();}});
poll();stateTimer=setInterval(poll,200);mapTimer=setInterval(pollMap,700);requestAnimationFrame(paint);
