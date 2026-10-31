import {initPrivatePanels,renderPrivatePanels} from '/private-panels.js';
let opened=false,selected='',sequence=0,busy=false,lastData=null,helpers=null,activeRole='bot',privateKind='all';
const $=id=>document.getElementById(id);
const drafts=new Map(),requests=new Map();
function draftKey(peer=selected){return peer+':'+activeRole;}
export function isPrivateOpen(){return opened;}

export function closePrivateUI(){
  if(!opened)return;
  drafts.set(draftKey(),$('private-text').value);opened=false;sequence++;
  document.querySelector('.main').classList.remove('private-mode');
  $('private-view').hidden=true;$('private-contacts').hidden=true;$('groups').hidden=false;$('white-form').hidden=false;
  $('show-private').classList.remove('active');$('show-private').setAttribute('aria-pressed','false');
  $('show-groups').classList.add('active');$('show-groups').setAttribute('aria-pressed','true');
  $('group-search').value='';$('group-search').placeholder='搜索群名或群号';$('group-search').setAttribute('aria-label','搜索群名或群号');
  document.querySelectorAll('.tab').forEach(el=>el.hidden=false);
}

function renderContacts(data){
  const area=$('private-contacts'),term=$('group-search').value.trim().toLowerCase();
  $('group-count').textContent=data.contacts.length;
  const signature=JSON.stringify([selected,term,data.contacts]);if(area.dataset.signature===signature)return;
  area.dataset.signature=signature;area.replaceChildren();$('group-count').textContent=data.contacts.length;
  for(const row of data.contacts){
    if(!(row.name+' '+row.id).toLowerCase().includes(term))continue;
    const button=helpers.node('button',undefined,'group'+(selected===row.id?' active':''));button.type='button';button.setAttribute('aria-current',String(selected===row.id));
    const avatar=helpers.node('span',String(row.name||'?').trim().charAt(0),'group-avatar');
    const text=helpers.node('span');text.append(helpers.node('span',row.name,'group-name'),helpers.node('span',row.last_text,'group-sub'));
    button.append(avatar,text);button.title=row.name+' · '+row.id;
    button.onclick=()=>{drafts.set(draftKey(),$('private-text').value);selected=row.id;$('private-text').value=drafts.get(draftKey())||'';loadPrivate().catch(showError);};
    area.append(button);
  }
  if(!area.children.length)area.append(helpers.node('div',term?'没有匹配的联系人':'暂时没有符合条件的授权群好友。刷新后显示群友列表，可在行为设置管理白名单和黑名单。','empty'));
}

function showError(error){if(opened)$('private-service-state').textContent=error.message;}

async function loadPrivate(){
  if(!opened)return;
  const ticket=++sequence,peer=selected;
  let data;
  try{data=await helpers.api('/api/private?'+new URLSearchParams({peer,kind:privateKind}));}
  catch(error){if(peer&&error.message.includes('联系人')){selected='';return loadPrivate();}throw error;}
  if(!data.member_scope){const settings=await helpers.api('/api/config');data.member_scope=settings.config.member_labels_global_enabled?'global':'private';}
  if(!opened||ticket!==sequence)return;
  lastData=data;renderContacts(data);renderPrivatePanels(data,peer);
  $('private-trace-count').textContent=data.event_counts?.all||0;
  $('group-title').textContent=peer?(data.name||peer):'私信';
  document.querySelector('.group-meta').hidden=false;
  $('group-subtitle').textContent=peer?'QQ '+peer:'QQ好友的一对一会话';$('session-detail').textContent='';
  $('group-status').textContent=data.enabled?(data.status.online?'私信服务运行中':'私信服务连接中'):'自动回复已关闭';
  $('private-auto').checked=data.enabled;
  $('private-service-state').textContent=data.status.error?'私信连接异常：'+data.status.error:
    data.enabled?'已读取 '+data.contacts.length+' 位好友 · 名单控制自动回复，会话独立保存':'私信服务已关闭，可打开“自动回复”启用';
  if(peer&&data.access_reason)$('private-service-state').textContent=data.access_reason+'；仍可查看和管理该好友';
  const system=activeRole==='system',canSend=!!peer&&(system||data.enabled&&data.status.online&&data.can_chat);
  $('private-text').disabled=!canSend;$('private-send').disabled=!canSend||busy;
  $('private-send').textContent=system?'保存系统提示词':'发送私信';
  $('private-system-lifetime').hidden=!system;
  $('private-clear-system').hidden=!system||!peer||!data.system_instruction;
  $('private-role').options[0].textContent=(data.bot_name||'机器人')+'消息（发私信）';
  $('private-text').placeholder=system?'为当前好友设置回复提示词，不会发送到QQ':peer?'以'+data.bot_name+'的身份发送文字私信':'选择联系人后发送文字私信';
  if(system&&peer&&!drafts.has(draftKey())&&document.activeElement!==$('private-text'))$('private-text').value=data.system_instruction||'';
  const query=$('private-record-search').value.trim().toLowerCase();
  const events=(data.events||[]).filter(row=>!query||(row.text+' '+row.kind+' '+JSON.stringify(row.details||{})).toLowerCase().includes(query));
  const area=$('private-messages'),signature=JSON.stringify([peer,privateKind,query,events]);
  if(area.dataset.signature!==signature){
    area.dataset.signature=signature;const bottom=area.scrollHeight-area.scrollTop-area.clientHeight<70;
    const openRows=new Set([...area.querySelectorAll('details[open]')].map(el=>el.dataset.event));area.replaceChildren();
    for(const event of events){
      const stamp=new Date(event.stamp*1000).toLocaleTimeString('zh-CN',{hour12:false});
      if(['received','sent'].includes(event.kind)){
        const own=event.kind==='sent',row=helpers.node('article',undefined,'private-message '+(own?'own':'incoming'));
        row.append(helpers.node('div',(own?data.bot_name:data.name||peer)+' · '+stamp+(!own&&data.profile?.blocked?' · 屏蔽':''),'private-message-meta'),helpers.node('div',event.text,'private-bubble'));area.append(row);
      }else if(event.kind==='system'){
        const card=helpers.node('article',undefined,'private-system-record');
        card.append(helpers.node('div',stamp+' · 系统提示词 · 仅当前私聊','private-message-meta'),helpers.node('div',event.text,'private-system-text'));area.append(card);
      }else{
        const row=helpers.node('details',undefined,'private-flow');row.dataset.event=String(event.id);row.open=openRows.has(String(event.id));row.dataset.level=event.level;
        const summary=helpers.node('summary'),label=({stage:'执行阶段',decision:'决策',trigger:'触发'})[event.kind]||event.kind;
        summary.append(helpers.node('span',stamp,'flow-time'),helpers.node('span',label,'kind-chip '+(event.level==='ERROR'?'bad':event.kind==='decision'?'decision':event.kind==='trigger'?'trigger':'')),helpers.node('span',event.text,'flow-text'));
        row.append(summary,helpers.node('pre',event.text+'\n'+JSON.stringify(event.details||{},null,2),'private-flow-detail'));area.append(row);
      }
    }
    if(!events.length)area.append(helpers.node('div',!peer?'选择左侧联系人，查看私信':query?'没有匹配的记录':privateKind==='flow'?'暂无执行过程。新私信触发回复后会在这里实时显示。':privateKind==='system'?'当前私聊还没有系统提示词记录':privateKind==='chat'?'还没有私信聊天记录':'还没有私信记录。群友发来新私信后会在这里显示。','empty'));
    if($('private-follow').checked&&(bottom||area.dataset.peer!==peer))area.scrollTop=area.scrollHeight;area.dataset.peer=peer;
  }
  $('private-log-count').textContent='显示 '+events.length+' 条 · 当前筛选 '+(data.events||[]).length+' 条；聊天与执行日志分开保留';
  const panel=$('private-impression'),profile=data.impression||{},profileKey=JSON.stringify([peer,profile.observations,profile.familiarity]);
  if(panel.dataset.signature!==profileKey){
    panel.dataset.signature=profileKey;panel.replaceChildren();
    if(peer){
      panel.append(helpers.node('h3',profile.familiarity||'印象尚少'));
      const observations=profile.observations||[];let facts=0;
      for(const item of observations){
        const card=helpers.node('section',undefined,'impression-card');
        card.append(helpers.node('p','本群观察到 '+item.message_count+' 条本人发言','hint'));
        if(item.speech_style)card.append(helpers.node('p','常用句子约 '+item.speech_style.median_message_length+' 字，参考近期 '+item.speech_style.sample_count+' 条表达'));
        for(const fact of item.interests||[]){card.append(helpers.node('p',fact.content));facts++;}
        panel.append(card);
      }
      if(!facts)panel.append(helpers.node('p','具体兴趣仍在慢慢了解，小C不会凭空编造印象。','hint'));
    }else panel.append(helpers.node('div','选择联系人后查看','empty'));
  }
  const request=data.requests?.[0];
  $('private-send-state').textContent=system?'仅影响当前好友的后续回复，不发送到QQ；直到修改或清除':request?({queued:'私信已排队',sending:'正在发送私信',sent:'私信已发送',uncertain:'发送结果未确认，请到QQ核对，系统不会自动重发',cancelled:'发送已取消'}[request.status]||'私信会话单独保存'):'私信会话单独保存';
}

export function initPrivateUI(options){
  helpers=options;
  initPrivatePanels({node:helpers.node,act:helpers.act,refresh:loadPrivate});
  document.querySelectorAll('[data-private-kind]').forEach(button=>button.onclick=()=>{privateKind=button.dataset.privateKind;document.querySelectorAll('[data-private-kind]').forEach(item=>item.classList.toggle('active',item===button));loadPrivate().catch(showError);});
  $('private-record-search').oninput=()=>loadPrivate().catch(showError);
  $('show-private').onclick=()=>{
    document.querySelector('.main').classList.add('private-mode');
    opened=true;$('private-view').hidden=false;$('private-contacts').hidden=false;$('groups').hidden=true;$('white-form').hidden=true;
    $('show-private').classList.add('active');$('show-private').setAttribute('aria-pressed','true');$('show-groups').classList.remove('active');$('show-groups').setAttribute('aria-pressed','false');
    $('group-search').value='';$('group-search').placeholder='搜索昵称或QQ号';$('group-search').setAttribute('aria-label','搜索私信联系人');
    $('group-tabs').hidden=true;$('group-actions').hidden=true;document.querySelectorAll('.tab').forEach(el=>el.hidden=true);
    loadPrivate().catch(showError);
  };
  $('show-groups').onclick=()=>{closePrivateUI();options.restore();};
  $('group-search').addEventListener('input',()=>{if(opened&&lastData)renderContacts(lastData);});
  $('private-refresh').onclick=()=>loadPrivate().catch(showError);
  $('private-auto').onchange=async()=>{try{await helpers.act({action:'config',key:'private_reply_enabled',value:$('private-auto').checked},$('private-auto'));await loadPrivate();}catch(error){showError(error);}};
  $('private-role').onchange=()=>{drafts.set(draftKey(),$('private-text').value);activeRole=$('private-role').value;$('private-text').value=drafts.get(draftKey())||(activeRole==='system'?lastData?.system_instruction||'':'');loadPrivate().catch(showError);};
  $('private-text').addEventListener('input',()=>drafts.set(draftKey(),$('private-text').value));
  $('private-clear-system').onclick=async()=>{if(!selected||busy)return;const peer=selected;busy=true;try{await helpers.act({action:'private_system',peer,text:''},$('private-clear-system'));drafts.delete(peer+':system');if(selected===peer&&activeRole==='system')$('private-text').value='';await loadPrivate();}catch(error){showError(error);}finally{busy=false;}};
  $('private-composer').onsubmit=async event=>{
    event.preventDefault();const peer=selected,text=$('private-text').value.trim();if(!peer||!text||busy)return;
    if(activeRole==='system'){
      busy=true;try{await helpers.act({action:'private_system',peer,text},$('private-send'));drafts.set(peer+':system',text);await loadPrivate();}catch(error){showError(error);}finally{busy=false;$('private-send').disabled=!selected||(activeRole==='bot'&&(!lastData?.status.online||!lastData?.enabled||!lastData?.can_chat));}return;
    }
    const key=peer+'\n'+text;if(!requests.has(key))requests.set(key,crypto.randomUUID());busy=true;$('private-send').disabled=true;
    try{await helpers.act({action:'private_send',peer,text,request_id:requests.get(key)},$('private-send'));
      drafts.delete(peer+':bot');if(selected===peer&&activeRole==='bot'&&$('private-text').value.trim()===text)$('private-text').value='';requests.delete(key);await loadPrivate();
    }catch(error){showError(error);}finally{busy=false;$('private-send').disabled=!selected||(activeRole==='bot'&&(!lastData?.status.online||!lastData?.enabled||!lastData?.can_chat));}
  };
  setInterval(()=>{if(opened&&!document.hidden&&!busy)loadPrivate().catch(showError);},2000);
}
