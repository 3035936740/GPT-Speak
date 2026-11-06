import {initPrivateRenderer,renderPrivateContacts,renderPrivateRecords,refreshPrivateAvatarImages} from '/private-chat-renderer.js';
import {initPrivatePanels,renderPrivatePanels} from '/private-panels.js';
import {settingSwitch} from '/private-settings.js';
let opened=false,selected='',sequence=0,busy=false,lastData=null,helpers=null,activeRole='bot',privateKind='all';
let privateAutoSwitch;
const $=id=>document.getElementById(id);
const drafts=new Map(),requests=new Map();
const attachments=new Map(),replyTargets=new Map();
function setReply(event){if(!event.details?.message_id)return;replyTargets.set(selected,event.details.message_id);$('private-role').value='bot';$('private-role').onchange();renderAttachments();$('private-text').focus();}
function renderAttachments(){
  const area=$('private-attachments');if(!area)return;area.replaceChildren();area.hidden=activeRole!=='bot';
  const reply=replyTargets.get(selected);if(reply){const chip=helpers.node('span','引用消息 '+reply,'composer-target'),remove=helpers.node('button','×');remove.type='button';remove.onclick=()=>{replyTargets.delete(selected);renderAttachments();};chip.append(remove);area.append(chip);}
  for(const image of attachments.get(selected)||[]){const card=helpers.node('span',undefined,'composer-attachment'),img=helpers.node('img'),remove=helpers.node('button','×');img.src='data:'+image.type+';base64,'+image.data;img.alt='图片'+image.slot;remove.type='button';remove.onclick=()=>{attachments.set(selected,(attachments.get(selected)||[]).filter(row=>row!==image));renderAttachments();};card.append(img,helpers.node('span','[图片'+image.slot+']'),remove);area.append(card);}
}
async function addImages(files){
  const peer=selected;if(!peer||activeRole!=='bot')return;const existing=[...(attachments.get(peer)||[])];
  if(existing.length+files.length>2)throw Error('最多附加2张图片');
  if(files.some(f=>!['image/png','image/jpeg','image/gif','image/webp'].includes(f.type)||f.size>5*1024*1024))throw Error('仅支持PNG/JPEG/GIF/WebP，单张最多5MiB');
  if(existing.reduce((sum,f)=>sum+f.size,0)+files.reduce((sum,f)=>sum+f.size,0)>8*1024*1024)throw Error('图片合计最多8MiB');
  for(const file of files){const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const slot=Math.max(0,...existing.map(row=>row.slot))+1;existing.push({slot,data:encoded,size:file.size,type:file.type});}
  attachments.set(peer,existing);renderAttachments();
}
function initPrivateControls(){
  document.querySelector('.group-header').append($('private-controls'));$('private-controls').hidden=true;
  const oldToolbar=document.querySelector('.private-toolbar');document.querySelector('.group-heading').append($('private-service-state'));
  $('private-service-state').hidden=true;
  const auto=$('private-auto');auto.setAttribute('aria-label','私信自动回复');
  privateAutoSwitch=settingSwitch(helpers.node,auto);
  const autoControl=helpers.node('div',undefined,'sticker-master');
  autoControl.append(helpers.node('span','自动回复'),privateAutoSwitch.state,privateAutoSwitch.track);
  $('private-controls').append(autoControl,$('private-refresh'));oldToolbar.remove();
  const options=$('private-composer').querySelector('.composer-options'),upload=helpers.node('button','上传图片');upload.id='private-upload';upload.type='button';const files=helpers.node('input');files.type='file';files.multiple=true;files.accept='image/png,image/jpeg,image/gif,image/webp';files.hidden=true;upload.onclick=()=>files.click();files.onchange=()=>{addImages([...files.files]).catch(showError);files.value='';};options.append(upload,files);
  const previews=helpers.node('div',undefined,'composer-attachments');previews.id='private-attachments';$('private-text').after(previews);
  const level=helpers.node('select');level.id='private-level';level.setAttribute('aria-label','筛选私聊警告和错误');for(const [value,text] of [['all','所有记录'],['WARNING','警告和错误'],['ERROR','仅错误']])level.append(new Option(text,value));level.onchange=()=>loadPrivate().catch(showError);document.querySelector('.private-log-toolbar').append(level);
  $('private-text').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('private-composer').requestSubmit();}});
  $('private-text').addEventListener('paste',e=>{const files=[...e.clipboardData.files].filter(f=>f.type.startsWith('image/'));if(files.length){e.preventDefault();addImages(files).catch(showError);}});
  $('private-friends-refresh').onclick=async()=>{try{await helpers.act({action:'private_refresh'},$('private-friends-refresh'));await loadPrivate();}catch(error){showError(error);}};
  const reconnectButton=helpers.node('button','重连 NoneBot');
  reconnectButton.type='button';reconnectButton.dataset.privateService='true';
  reconnectButton.title='重启共享NoneBot网关并等待客户端回连；所有群聊和私聊会短暂重连';
  reconnectButton.onclick=async()=>{
    try{await helpers.act({action:'private_reconnect',mode:'nonebot'},reconnectButton);await loadPrivate();}
    catch(error){showError(error);}
  };
  $('private-controls').insertBefore(reconnectButton,$('private-session-stop'));
  const mcpButton=helpers.node('button','检查 GPT Speak MCP');
  mcpButton.type='button';mcpButton.dataset.privateService='true';
  mcpButton.title='建立临时GPT Speak MCP会话，读取本地联系人列表；Codex已有连接需在Codex内重连';
  mcpButton.onclick=async()=>{
    try{
      if(!(await helpers.api('/api/config')).mcp_integration)throw Error('请先重启 GPT Speak 控制台，加载 GPT Speak MCP 检查功能');
      await helpers.act({action:'private_reconnect',mode:'mcp'},mcpButton);await loadPrivate();
    }catch(error){showError(error);}
  };
  $('private-controls').insertBefore(mcpButton,$('private-session-stop'));
  $('private-media-refresh').onclick=async()=>{if(!selected)return;try{await helpers.act({action:'private_hydrate',peer:selected},$('private-media-refresh'));await loadPrivate();}catch(error){showError(error);}};
  $('private-session-stop').onclick=async()=>{if(!selected)return;try{await helpers.act({action:'private_session',peer:selected,mode:lastData?.session?.paused?'resume':'stop'},$('private-session-stop'));await loadPrivate();}catch(error){showError(error);}};
  $('private-context-clear').onclick=async()=>{if(!selected||!confirm('清空当前好友的短期上下文？长期记忆和知识库保留。'))return;try{await helpers.act({action:'private_session',peer:selected,mode:'clear'},$('private-context-clear'));await loadPrivate();}catch(error){showError(error);}};
  $('private-receive-only').onclick=async()=>{if(!selected)return;try{await helpers.act({action:'private_setting',peer:selected,scope:'contact',key:'reply_enabled',value:lastData?.contact_settings?.reply_enabled===false},$('private-receive-only'));await loadPrivate();}catch(error){showError(error);}};
  $('private-avatar-refresh').onclick=async()=>{if(!selected)return;try{await helpers.act({action:'private_avatar_refresh',peer:selected},$('private-avatar-refresh'));await refreshPrivateAvatarImages();}catch(error){showError(error);}};
}
function draftKey(peer=selected){return peer+':'+activeRole;}
export function isPrivateOpen(){return opened;}

export function closePrivateUI(){
  if(!opened)return;
  drafts.set(draftKey(),$('private-text').value);opened=false;sequence++;
  $('private-service-state').hidden=true;
  $('private-controls').hidden=true;
  document.querySelector('.main').classList.remove('private-mode');
  $('private-view').hidden=true;$('private-contacts').hidden=true;$('groups').hidden=false;$('white-form').hidden=false;
  $('show-private').classList.remove('active');$('show-private').setAttribute('aria-pressed','false');
  $('show-groups').classList.add('active');$('show-groups').setAttribute('aria-pressed','true');
  $('group-search').value='';$('group-search').placeholder='搜索群名或群号';$('group-search').setAttribute('aria-label','搜索群名或群号');
  document.querySelectorAll('.tab').forEach(el=>el.hidden=false);
}

function renderContacts(data){
  $('group-count').textContent=data.contacts.length;
  renderPrivateContacts($('private-contacts'),data,selected,$('group-search').value.trim().toLowerCase(),peer=>{
    drafts.set(draftKey(),$('private-text').value);selected=peer;$('private-text').value=drafts.get(draftKey())||'';
    loadPrivate().catch(showError);
  });
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
  const unavailable=data.contacts.find(row=>row.id===peer)?.unavailable_reason;
  $('group-status').textContent=unavailable||(data.enabled?(data.status.online?'私信服务运行中':'私信服务连接中'):'自动回复已关闭');
  $('private-auto').checked=data.enabled;
  privateAutoSwitch.update();
  $('private-service-state').textContent=data.status.error?'私信连接异常：'+data.status.error:
    data.enabled?'已读取 '+data.contacts.length+' 位好友 · 名单控制自动回复，会话独立保存':'私信服务已关闭，可打开“自动回复”启用';
  if(peer&&data.access_reason)$('private-service-state').textContent=data.access_reason+'；仍可查看和管理该好友';
  for(const button of $('private-controls').querySelectorAll('button'))button.disabled=!peer&&button.id!=='private-friends-refresh'&&!button.dataset.privateService;
  $('private-session-stop').textContent=data.session?.paused?'恢复当前会话':'终止当前会话';
  $('private-receive-only').textContent=data.contact_settings?.reply_enabled===false?'恢复AI处理':'仅接收消息';
  const system=activeRole==='system',canSend=!!peer&&!unavailable&&(system||data.enabled&&data.status.online&&data.can_chat);
  $('private-text').disabled=!canSend;$('private-send').disabled=!canSend||busy;
  $('private-send').textContent=system?'保存系统提示词':'发送私信';
  $('private-system-lifetime').hidden=!system;renderAttachments();$('private-upload').hidden=system;
  $('private-clear-system').hidden=!system||!peer||!data.system_instruction;
  $('private-role').options[0].textContent=(data.bot_name||'机器人')+'消息（发私信）';
  $('private-text').placeholder=system?'为当前好友设置回复提示词，不会发送到QQ':peer?'以'+data.bot_name+'的身份发送文字私信':'选择联系人后发送文字私信';
  if(system&&peer&&!drafts.has(draftKey())&&document.activeElement!==$('private-text'))$('private-text').value=data.system_instruction||'';
  $('private-text').placeholder+='\nEnter 发送·Shift+Enter 换行';
  const query=$('private-record-search').value.trim().toLowerCase();
  const events=(data.events||[]).filter(row=>!query||(row.text+' '+row.kind+' '+JSON.stringify(row.details||{})).toLowerCase().includes(query));
  const filtered=events.filter(row=>!$('private-level')||$('private-level').value==='all'||($('private-level').value==='ERROR'?row.level==='ERROR':['WARNING','ERROR'].includes(row.level)));
  renderPrivateRecords($('private-messages'),data,peer,filtered,setReply);
  $('private-log-count').textContent=filtered.length+' / '+(data.event_counts?.all||0)+' 条记录';
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
  initPrivatePanels({...helpers,refresh:loadPrivate});
  initPrivateRenderer({...helpers,member:()=>document.querySelector('[data-private-tab=member]').click()});
  initPrivateControls();
  document.querySelectorAll('[data-private-kind]').forEach(button=>button.onclick=()=>{privateKind=button.dataset.privateKind;document.querySelectorAll('[data-private-kind]').forEach(item=>item.classList.toggle('active',item===button));loadPrivate().catch(showError);});
  $('private-record-search').oninput=()=>loadPrivate().catch(showError);
  $('show-private').onclick=()=>{
    document.querySelector('.main').classList.add('private-mode');
    $('private-controls').hidden=false;
    $('private-service-state').hidden=false;
    opened=true;$('private-view').hidden=false;$('private-contacts').hidden=false;$('groups').hidden=true;$('white-form').hidden=true;
    $('show-private').classList.add('active');$('show-private').setAttribute('aria-pressed','true');$('show-groups').classList.remove('active');$('show-groups').setAttribute('aria-pressed','false');
    $('group-search').value='';$('group-search').placeholder='搜索昵称或QQ号';$('group-search').setAttribute('aria-label','搜索私信联系人');
    $('group-tabs').hidden=true;$('group-actions').hidden=true;document.querySelectorAll('.tab').forEach(el=>el.hidden=true);
    loadPrivate().catch(showError);
  };
  $('show-groups').onclick=()=>{closePrivateUI();options.restore();};
  $('group-search').addEventListener('input',()=>{if(opened&&lastData)renderContacts(lastData);});
  $('private-refresh').onclick=()=>loadPrivate().catch(showError);
  $('private-auto').onchange=async()=>{const input=$('private-auto');privateAutoSwitch.update();try{await helpers.act({action:'config',key:'private_reply_enabled',value:input.checked},input);await loadPrivate();}catch(error){input.checked=!!lastData?.enabled;privateAutoSwitch.update();showError(error);}};
  $('private-role').onchange=()=>{drafts.set(draftKey(),$('private-text').value);activeRole=$('private-role').value;$('private-text').value=drafts.get(draftKey())||(activeRole==='system'?lastData?.system_instruction||'':'');loadPrivate().catch(showError);};
  $('private-text').addEventListener('input',()=>drafts.set(draftKey(),$('private-text').value));
  $('private-clear-system').onclick=async()=>{if(!selected||busy)return;const peer=selected;busy=true;try{await helpers.act({action:'private_system',peer,text:''},$('private-clear-system'));drafts.delete(peer+':system');if(selected===peer&&activeRole==='system')$('private-text').value='';await loadPrivate();}catch(error){showError(error);}finally{busy=false;}};
  $('private-composer').onsubmit=async event=>{
    event.preventDefault();const peer=selected,text=$('private-text').value.trim();if(!peer||(!text&&!(attachments.get(peer)||[]).length)||busy)return;
    if(activeRole==='system'){
      busy=true;try{await helpers.act({action:'private_system',peer,text},$('private-send'));drafts.set(peer+':system',text);await loadPrivate();}catch(error){showError(error);}finally{busy=false;$('private-send').disabled=!selected||(activeRole==='bot'&&(!lastData?.status.online||!lastData?.enabled||!lastData?.can_chat));}return;
    }
    const images=attachments.get(peer)||[],reply_id=replyTargets.get(peer)||'';const key=JSON.stringify([peer,text,images,reply_id]);if(!requests.has(key))requests.set(key,crypto.randomUUID());busy=true;$('private-send').disabled=true;
    try{await helpers.act({action:'private_send',peer,text,request_id:requests.get(key),images:images.map(({slot,data})=>({slot,data})),reply_id},$('private-send'));
      drafts.delete(peer+':bot');attachments.delete(peer);replyTargets.delete(peer);renderAttachments();if(selected===peer&&activeRole==='bot'&&$('private-text').value.trim()===text)$('private-text').value='';requests.delete(key);await loadPrivate();
    }catch(error){showError(error);}finally{busy=false;$('private-send').disabled=!selected||(activeRole==='bot'&&(!lastData?.status.online||!lastData?.enabled||!lastData?.can_chat));}
  };
  setInterval(()=>{if(opened&&!document.hidden&&!busy)loadPrivate().catch(showError);},2000);
}
