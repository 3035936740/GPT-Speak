import {initPrivateUI,isPrivateOpen,closePrivateUI} from '/private-ui.js';
import {knowledgeTools} from '/knowledge-tools.js';
import {openCollectionEditor,collectionSummary,memoryCollection,collectionDraftInput,settingSwitch,decisionDefaults,roleSettingsSummary,nonebotClientURL} from '/private-settings.js';
let activePrompt='persona',activeScope='global',configSequence=0;const promptDrafts={},configDrafts={};

const $=id=>document.getElementById(id);
const keyFromURL=new URLSearchParams(location.hash.slice(1)).get('key');
if(keyFromURL){sessionStorage.setItem('qq-debug-key',keyFromURL);history.replaceState(null,'',location.pathname);}
const token=sessionStorage.getItem('qq-debug-key')||'';
let selected=null,kind='all',snapshot=null,configData=null,polling=false,pollSequence=0,lastEvents='',currentTab='logs',selectedEvent=null,closed=false,toastTimer=0;
const labels={system:'系统干预',received:'收到消息',sent:'Bot 发送',stage:'执行阶段',decision:'决策',trigger:'触发',group:'会话'};
function windowUsage(chars, limit){
  return limit===0?chars+' 字（不限）':chars+' / '+limit+' 字';
}

const titles={bot_name:'人格名字',bot_aliases:'点名识别别名',model:'回复模型',reasoning_effort:'思考等级',fast_mode:'Fast 模式',passive_probability:'普通水群概率',sticker_reply_probability:'表情反应偏好',no_words_sticker_probability:'纯表情偏好',followup_sticker_probability:'文字后跟表情偏好',burst_reply_probability:'连续分段发送偏好',friendly_tease_probability:'朋友间玩笑偏好',console_max_text_chars:'调试记录文本上限',console_max_records:'调试记录条数上限',max_prompt_chars:'普通模型输入字符上限',context_messages:'传入模型的近期消息条数',max_replies_per_hour:'每小时消息上限',max_images_per_hour:'每小时绘图上限',image_summary_window_messages:'图片摘要滑动有效期',image_repeat_window_messages:'复读观察窗口',image_repeat_min_count:'复读候选次数',image_repeat_pair_confidence:'连续两次的复读置信度',image_repeat_full_streak:'明确复读所需连续次数',chat_group_whitelist:'群白名单',enabled:'机器人启用',multi_role_enabled:'模型分工',role_models:'分工角色模型',shared_sticker_library:'跨群共用表情库',style_learning_enabled:'表达学习',memory_enabled:'群聊记忆',proactive_topics_enabled:'主动开话题',image_generation_enabled:'绘图启用',recent_chat_journal_max_messages:'近期聊天存档条数',recent_chat_journal_hours:'近期聊天存档时长'};
// Order mirrors the settings page, independent of the order of saved keys.
const settingsGroups=[
  ['基础设置',
    ['identity','Bot 身份与接入','设置 Bot 的名字、运行状态和授权群聊。',
      ['', 'enabled receive_only bot_name expected_self_id chat_group_whitelist']],
    ['model','模型与分工','设置日常回复模型和内部判断所用的模型。',
      ['回复模型', 'model reasoning_effort fast_mode text_transport model_timeout_seconds'],
      ['决策与角色分工', 'multi_role_enabled role_models role_timeout_seconds']],
    ['connection','连接设置','管理 OneBot 消息连接和 GPT Speak MCP 接入。',
      ['NoneBot2 反向连接', 'nonebot_listen_host nonebot_port nonebot_access_token nonebot_bridge_url nonebot_api_timeout_seconds'],
      ['QQ\u8d44\u6599\u5237\u65b0\u4e0e\u7f13\u5b58', 'group_directory_refresh_seconds friend_directory_refresh_seconds member_directory_refresh_seconds avatar_cache_seconds'],
      ['GPT Speak MCP', '@gpt_speak_mcp']]],
  ['聊天行为',
    ['participation','群聊触发与节奏','控制 Bot 何时回应点名、参与聊天或主动开话题。',
      ['点名与冷却', 'cooldown bot_aliases name_match_sensitivity at_context_sensitivity mention_cooldown_seconds'],
      ['日常接话', `passive_trigger_mode passive_probability passive_min_messages passive_cooldown_seconds
        passive_window_seconds passive_round_seconds passive_idle_compensation_enabled passive_retry_backoff_seconds
        adaptive_participation_enabled social_planner_enabled conversation_state_ttl_seconds`],
      ['主动开话题', 'proactive_topics_enabled proactive_topic_probability proactive_topic_quiet_minutes proactive_topic_min_hours proactive_topic_max_hours']],
    ['reply','回复风格与格式','调整接话语气、引用和分段，以及失败时的表现。',
      ['理解与语气', 'peer_reply_enabled high_eq_enabled friendly_tease_probability pinyin_abbreviation_guess_enabled'],
      ['发送格式', 'at_reply_probability quote_reply_probability burst_reply_probability max_reply_parts max_reply_chars reply_failure_log_only']],
    ['member','群友资料与标记','管理跨会话标记、模型可参考的资料及自动规则。',
      ['标记范围与模型参考', 'member_labels_global_enabled member_reference_type_enabled member_reference_aliases_enabled member_reference_note_enabled'],
      ['账号类型提示词', 'member_prompt_owner member_prompt_human member_prompt_AI member_prompt_bot'],
      ['模型自行标记', 'member_model_type_enabled member_model_aliases_enabled member_model_note_enabled'],
      ['指令机器人识别', 'member_auto_bot_marking_enabled member_auto_bot_command_prefixes member_auto_bot_window_messages member_auto_bot_min_observations'],
      ['自动屏蔽', 'member_model_block_enabled member_model_block_min_messages member_model_block_rules']],
    ['private','私信接入与默认行为','设置私信服务、好友准入和初始偏好。单个好友的设置可在私信页修改。',
      ['私信准入', 'private_reply_enabled private_history_fetch_enabled private_access_mode private_user_whitelist private_user_blacklist'],
      ['回复与参考资料', 'private_reply_cooldown_seconds private_max_reply_chars private_vision_enabled private_use_group_impressions private_memory_retrieval_mode'],
      ['私信额度', 'private_max_replies_per_hour private_max_model_calls_per_day']]],
  ['功能与工具',
    ['sticker','表情发送与收录','管理表情总开关、回复偏好和共享库收录规则。',
      ['', 'stickers_enabled'],
      ['发送偏好', 'sticker_reply_probability no_words_sticker_probability followup_sticker_probability sticker_recent_suppression_strength max_stickers_per_reply'],
      ['收录与共享', 'shared_sticker_library sticker_admission_rule sticker_ignore_market_faces sticker_repeat_review_min_count sticker_repeat_review_step sticker_upload_retry_seconds']],
    ['image','识图与绘图','设置图片理解、生成和生成图片的保留方式。',
      ['识图与摘要', 'background_image_reading_enabled image_direct_original_enabled image_summary_level image_summary_window_messages max_images_per_call max_image_bytes vision_timeout_seconds'],
      ['同图复读', 'image_repeat_window_messages image_repeat_min_count image_repeat_pair_confidence image_repeat_full_streak'],
      ['绘图与额度', 'image_generation_enabled image_backend image_timeout_seconds image_cooldown_seconds max_images_per_hour max_images_per_day'],
      ['生成图片清理', 'generated_image_ttl_hours generated_image_cleanup_mode generated_image_cleanup_interval_minutes generated_image_cleanup_daily_time generated_image_windows_schedule']],
    ['attachments','文件与转发记录','管理群文件、参考附件和合并转发的读取。',
      ['文件与附件', 'group_file_read_enabled model_max_files model_max_file_bytes'],
      ['转发聊天记录', 'forward_messages_enabled forward_images_enabled']],
    ['profile-tools','资料查询与联网','设置公开资料查询、联网回答和查询后绘图。',
      ['公开资料查询', 'model_avatar_query_enabled model_profile_query_enabled profile_query_cache_seconds'],
      ['联网与绘图联动', 'web_answer_max_chars tool_workflow_enabled tool_workflow_max_steps']],
    ['large','任务（实验性）','设置文件交付任务、执行环境、配额与保留期限。',
      ['任务与模型', 'large_tasks_enabled large_task_model large_task_reasoning_effort large_task_fast_mode large_task_timeout_seconds large_task_step_timeout_seconds large_task_script_max_chars large_task_background_chars large_task_max_prompt_chars large_task_max_steps large_task_stall_limit'],
      ['执行环境', 'large_task_executor large_task_sandbox_runtime large_task_docker_wsl large_task_container_mode large_task_container_name large_task_sandbox_image large_task_dockerfile'],
      ['接收与交付', 'large_task_max_concurrent large_task_daily_limit large_task_prepare_notice_timeout_seconds large_task_max_file_bytes large_task_send_incomplete large_task_retention_hours']]],
  ['记忆与检索',
    ['memory','群记忆与学习','设置群记忆检索、表达学习和近期聊天存档。',
      ['记忆检索', 'memory_enabled memory_retrieval_mode knowledge_chat_retrieval_enabled semantic_selection_enabled semantic_candidate_chars'],
      ['后台学习', 'style_learning_enabled style_adaptation_strength learning_min_messages learning_interval_seconds learning_prompt_chars'],
      ['聊天存档', 'recent_chat_journal_max_messages recent_chat_journal_hours']],
    ['knowledge','知识库','设置当前群和共享知识的检索方式及片段预算。',
      ['', 'knowledge_enabled knowledge_retrieval_mode knowledge_max_chars']],
    ['embedding','Embedding 服务','知识库与群聊、私聊记忆共用的向量服务与重排设置。',
      ['向量服务', 'knowledge_embedding_provider knowledge_embedding_model knowledge_embedding_ollama_url knowledge_embedding_ollama_cpu knowledge_embedding_api_url'],
      ['重排服务', 'knowledge_reranker_url knowledge_reranker_model']]],
  ['管理与维护',
    ['limits','聊天额度与模型输入','分别限制普通聊天的发送、调用次数和模型输入。',
      ['消息与调用额度', 'max_replies_per_hour max_replies_per_day max_model_calls_per_day'],
      ['模型输入', 'max_prompt_chars context_messages context_message_chars']],
    ['owner-control','Bot 指令','管理 Bot 主在 QQ 聊天中执行 CLI 命令的入口。',
      ['', 'owner_commands_enabled owner_command_prefix owner_command_aliases']],
    ['debug','WebUI 与本地记录','管理控制台访问、聊天显示及记录窗口。',
      ['WebUI 访问', 'webui_host webui_port webui_key'],
      ['聊天显示与记录', 'avatar_remote_enabled chat_image_display console_max_records console_max_text_chars']]]
];
const promptInfo={persona:'Bot 的性格、说话方式和背景设定。保存后下一轮回复生效。',reply_protocol:'Bot 回复时要遵守的格式和行为规则。改错可能导致回复失败，保存前会自动备份旧版本。'};
const shanghai={timeZone:'Asia/Shanghai',hour12:false};
function node(tag,text,className){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;}
function toast(text,error=false){const el=$('toast');el.textContent=text;el.classList.toggle('error',error);el.hidden=!text;clearTimeout(toastTimer);if(text)toastTimer=setTimeout(()=>{el.hidden=true;},error?7000:3500);}
function initial(text){return String(text||'?').trim().charAt(0).toUpperCase()||'?';}
const avatarCache=new Map();
function paintAvatar(el,url,entry){
  if(!url)return;const img=node('img',undefined,'avatar-image');img.alt='';img.decoding='async';
  img.onload=()=>{if(entry.url!==url)return;for(const previous of el.querySelectorAll('img.avatar-image'))previous.remove();el.append(img);};
  img.onerror=()=>img.remove();img.src=url;
}
function fetchAvatar(entry,force=false){
  if(entry.loading)return entry.promise;
  entry.loading=true;entry.time=Date.now();
  entry.promise=fetch('/api/avatar?'+new URLSearchParams({group:entry.group,kind:entry.kind,id:entry.id,...(force?{refresh:'1'}:{})}),{headers:{'X-QQ-Debug-Key':token},cache:'no-store'})
    .then(response=>{if(!response.ok)throw Error('avatar unavailable');return response.blob();})
    .then(blob=>{const previous=entry.url;entry.url=URL.createObjectURL(blob);entry.failed=false;
      for(const el of entry.elements){if(el.isConnected)paintAvatar(el,entry.url,entry);else entry.elements.delete(el);}
      if(previous)setTimeout(()=>URL.revokeObjectURL(previous),30000);return entry.url;
    }).catch(()=>{entry.failed=true;return null;}).finally(()=>{entry.loading=false;});
  return entry.promise;
}
function avatarNode(kind,id,name,className,group=selected){
  const el=node('span',initial(name),className);if(!/^\d+$/.test(String(id||'')))return el;
  const cacheKey=group+':'+kind+':'+id;let entry=avatarCache.get(cacheKey);
  if(!entry){entry={group,kind,id,time:0,failed:false,url:null,elements:new Set()};avatarCache.set(cacheKey,entry);}
  entry.elements.add(el);if(entry.url)paintAvatar(el,entry.url,entry);
  if(!entry.promise||Date.now()-entry.time>(entry.failed?60000:300000))fetchAvatar(entry);
  if(!entry.url)entry.promise.then(url=>{if(url)paintAvatar(el,url,entry);});
  while(avatarCache.size>400){const [key,value]=avatarCache.entries().next().value;avatarCache.delete(key);value.promise?.then(()=>{if(value.url)URL.revokeObjectURL(value.url);});}
  return el;
}
async function refreshVisibleAvatars(force=false){
  const entries=[...avatarCache.values()].filter(entry=>(!force||String(entry.group)===String(selected))&&[...entry.elements].some(el=>el.isConnected)&&(force||Date.now()-entry.time>(entry.failed?60000:300000)));
  let next=0;await Promise.all(Array.from({length:Math.min(4,entries.length)},async()=>{while(next<entries.length)await fetchAvatar(entries[next++],force);}));
  return entries.filter(entry=>entry.failed).length;
}
setInterval(()=>{if(!document.hidden&&!closed)refreshVisibleAvatars().catch(()=>{});},30000);
window.addEventListener('pagehide',()=>{for(const entry of avatarCache.values())if(entry.url)URL.revokeObjectURL(entry.url);avatarCache.clear();});
const interventionModes={context:'当前对话窗口',once:'下一次自然回复',timed:'定时失效',persistent:'直到手动清除'};
const interventionStates={active:'生效中',expired:'已失效',cleared:'已清除',consumed:'已使用'};
function interventionDelete(entry,group=selected){const button=node('button','删除','danger-text system-note-delete');button.type='button';button.disabled=!snapshot?.features?.includes('intervention_delete');button.title=button.disabled?'请重载Debug后台以启用删除':'删除后停止影响后续生成';button.onclick=async event=>{event.stopPropagation();try{await act({action:'system',mode:'delete',id:entry.id,group},button);if($('intervention-dialog').open)await loadInterventions();}catch{}};return button;}
function systemRow(row){const item=node('div',undefined,'system-note'),head=node('div',undefined,'system-note-header'),entry=row.intervention||{};head.append(node('strong','系统干预'),node('span',interventionStates[entry.status]||entry.status||''),node('span',interventionModes[entry.mode]||''),node('span',eventTime(row)),interventionDelete(entry));item.append(head,node('div',row.text||'','system-note-text'));return item;}
const composerDrafts={},manualPending=new Map(),manualDraftIDs=new Map(),composerResults=new Map(),composerBusy=new Set();
const composerAttachments=new Map(),composerImageLoading=new Set(),composerTargets=new Map();let composerUploadTarget=null;
function composerKey(){return $('composer-role').value+':'+selected;}
function attachmentsFor(key){return composerAttachments.get(key)||[];}
function attachmentSignature(key){return attachmentsFor(key).map(image=>image.id).join(':');}
function currentDraftText(key){return $('composer-text').dataset.key===key?$('composer-text').value:composerDrafts[key]?.text||'';}
function clearAttachments(key){for(const image of attachmentsFor(key))URL.revokeObjectURL(image.url);composerAttachments.delete(key);}
function composerResult(group,role,text,error=false){composerResults.set(role+':'+group,{text,error});if(group===selected)renderComposer();}
function renderAttachments(key,system,locked){
  const area=$('composer-attachments'),images=attachmentsFor(key);area.hidden=system||!images.length;
  const signature=key+':'+attachmentSignature(key)+':'+locked;
  if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  for(const image of images){
    const card=node('div',undefined,'composer-attachment'),preview=node('button',undefined,'composer-image-preview'),img=node('img');
    img.src=image.url;img.alt='图片'+image.slot+'：'+image.file.name;preview.type='button';preview.title='查看图片'+image.slot;preview.append(img);
    preview.onclick=()=>showOriginal(image.url,image.file.name||'待发送图片');
    const caption=node('span','[图片'+image.slot+']','code'),remove=node('button','×','composer-image-remove');
    remove.type='button';remove.disabled=locked;remove.setAttribute('aria-label','移除图片'+image.slot);
    remove.onclick=()=>{
      if(composerBusy.has(key)||composerImageLoading.has(key)||manualPending.has(Number(key.split(':')[1])))return;
      composerAttachments.set(key,attachmentsFor(key).filter(item=>item.id!==image.id));URL.revokeObjectURL(image.url);
      const text=currentDraftText(key).replaceAll('[图片'+image.slot+']','');composerDrafts[key]={...composerDrafts[key],text};
      if($('composer-text').dataset.key===key)$('composer-text').value=text;renderComposer();
    };
    card.append(preview,caption,remove);area.append(card);
  }
}
function targetsFor(key){return composerTargets.get(key)||{reply:null,mentions:[]};}
function targetSignature(key){const data=targetsFor(key);return JSON.stringify([data.reply?.message_id||'',data.mentions.map(row=>row.id)]);}
function manualLocked(key){return composerBusy.has(key)||composerImageLoading.has(key)||manualPending.has(Number(key.split(':')[1]));}
function renderComposerTargets(key,system,locked){
  const area=$('composer-targets'),data=targetsFor(key);area.hidden=system||(!data.reply&&!data.mentions.length);
  const signature=key+targetSignature(key)+locked;if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  const chip=(text,label,remove)=>{const box=node('span',undefined,'composer-target'),button=node('button','×');button.type='button';button.disabled=locked;button.setAttribute('aria-label',label);button.onclick=()=>{if(manualLocked(key))return;remove();renderComposer();};box.append(node('span',text),button);area.append(box);};
  if(data.reply)chip('回复 '+data.reply.sender+'：'+data.reply.text,'取消回复',()=>composerTargets.set(key,{...data,reply:null}));
  for(const member of data.mentions)chip('@'+member.name,'取消@'+member.name,()=>{
    composerTargets.set(key,{...data,mentions:data.mentions.filter(row=>row.id!==member.id)});
    const text=currentDraftText(key).replaceAll('[@'+member.id+']','');composerDrafts[key]={...composerDrafts[key],text};if($('composer-text').dataset.key===key)$('composer-text').value=text;
  });
}
function prepareManualTarget(){
  const key='bot:'+selected;if(manualLocked(key))throw Error('当前消息正在发送，请等待结果');
  if(!snapshot.features?.includes('manual_targets'))throw Error('请 /stop 后重新启动后台以加载手动回复和@');
  $('composer-role').value='bot';renderComposer();return key;
}
function selectManualReply(row){
  try{const key=prepareManualTarget();composerTargets.set(key,{...targetsFor(key),reply:{message_id:String(row.message_id),sender:row.sender||snapshot.status.name||'群友',text:String(row.text||'[图片]').slice(0,180)}});renderComposer();$('composer-text').focus();}
  catch(error){toast(error.message,true);}
}
function insertManualMention(member,target){
  const key=target.key;if(manualLocked(key))throw Error('当前消息正在发送，请等待结果');
  const data=targetsFor(key),id=String(member.id),existing=data.mentions.some(row=>row.id===id);
  if(!existing&&data.mentions.length>=8)throw Error('最多@8位群友');
  if(existing){toast('已添加这位群友');return;}
  const text=currentDraftText(key),start=Math.min(target.start??text.length,text.length),end=Math.min(target.end??start,text.length),insert='[@'+id+'] ';
  const next=text.slice(0,start)+insert+text.slice(end);if(next.length>2000)throw Error('文字和占位符合计最多2000字');
  composerTargets.set(key,{...data,mentions:[...data.mentions,{id,name:member.name||id}]});composerDrafts[key]={...composerDrafts[key],text:next};
  if($('composer-text').dataset.key===key){$('composer-text').value=next;$('composer-text').focus();$('composer-text').setSelectionRange(start+insert.length,start+insert.length);}renderComposer();
}
let mentionTarget=null,mentionSequence=0;
async function loadMentionChoices(){
  const ticket=++mentionSequence,target=mentionTarget;if(!target)return;
  const area=$('mention-results'),query=$('mention-search').value.trim();area.replaceChildren(node('div','正在读取群友…','hint'));
  try{
    const data=await api('/api/members?'+new URLSearchParams({group:target.group,q:query}));if(ticket!==mentionSequence||!$('mention-dialog').open)return;
    const choices=new Map();for(const member of data.members)choices.set(String(member.qq_id),{id:String(member.qq_id),name:member.card||member.nickname||String(member.qq_id),aliases:member.aliases||[]});
    if(selected===target.group)for(const row of snapshot.events){const id=String(row.user_id||'');if(!/^[1-9]\d{4,14}$/.test(id)||choices.has(id))continue;const name=row.sender||id;if((name+' '+id).toLowerCase().includes(query.toLowerCase()))choices.set(id,{id,name,aliases:[]});}
    area.replaceChildren();for(const member of choices.values()){
      const button=node('button',undefined,'mention-choice');button.type='button';button.append(node('strong',member.name),node('span',member.id+(member.aliases.length?' · '+member.aliases.join(' / '):''),'hint'));
      button.onclick=()=>{try{insertManualMention(member,target);$('mention-dialog').close();}catch(error){toast(error.message,true);}};area.append(button);
    }
    if(!choices.size)area.append(node('div','没有找到群友，请尝试 QQ 号或其他昵称','empty'));
  }catch(error){if(ticket===mentionSequence)area.replaceChildren(node('div',error.message,'error'));}
}
function openMentionPicker(replaceAt=false){
  try{const key=prepareManualTarget(),input=$('composer-text');mentionTarget={key,group:selected,start:Math.max(0,input.selectionStart-(replaceAt?1:0)),end:input.selectionEnd};$('mention-search').value='';$('mention-dialog').showModal();loadMentionChoices();$('mention-search').focus();}
  catch(error){toast(error.message,true);}
}
$('composer-mention').onclick=()=>openMentionPicker();$('mention-search').oninput=()=>loadMentionChoices();$('mention-close').onclick=()=>$('mention-dialog').close();
$('mention-dialog').addEventListener('close',()=>{mentionSequence++;mentionTarget=null;});
$('composer-text').addEventListener('input',event=>{if($('composer-role').value==='bot'&&!event.isComposing&&event.inputType==='insertText'&&event.data==='@'){const input=event.target,at=input.selectionStart-1;if(at===0||/\s/.test(input.value[at-1]))openMentionPicker(true);}});
function renderComposer(){
  if(!snapshot)return;const input=$('composer-text'),role=$('composer-role').value,key=composerKey(),previous=input.dataset.key;
  if(previous!==key){if(previous)composerDrafts[previous]={...composerDrafts[previous],text:input.value,scope:$('composer-scope').value,lifetime:$('composer-lifetime').value,minutes:$('composer-minutes').value};const draft=composerDrafts[key]||{};input.value=draft.text||'';$('composer-scope').value=draft.scope||'group';$('composer-lifetime').value=draft.lifetime||'context';$('composer-minutes').value=draft.minutes||'30';input.dataset.key=key;}
  const system=role==='system',s=snapshot.status,images=attachmentsFor(key),targets=targetsFor(key),hasTargets=Boolean(targets.reply||targets.mentions.length),locked=composerBusy.has(key)||composerImageLoading.has(key)||!system&&manualPending.has(selected);
  const ready=system?snapshot.features?.includes('system_interventions'):snapshot.features?.includes('manual_send')&&s.enabled&&s.online&&s.manual_send_ready;
  const imagesSupported=snapshot.features?.includes('manual_images');
  document.querySelectorAll('[data-system-option]').forEach(el=>el.hidden=!system);$('composer-minutes-row').hidden=!system||$('composer-lifetime').value!=='timed';
  input.maxLength=system?1500:2000;input.disabled=Boolean(locked);
  input.placeholder=(system?'输入系统提示词，不会立即触发回复':'以 '+s.name+' 的身份向当前群发送消息，可上传或粘贴图片，按 [图片1] 的位置发送')+'\nEnter 发送 · Shift+Enter 换行';
  $('composer-upload').hidden=system;$('composer-upload').disabled=Boolean(locked||!imagesSupported||images.length>=2);
  $('composer-upload').title=imagesSupported?'最多2张；单张≤5MB，合计≤8MB；支持PNG/JPEG/GIF/WebP':'请 /stop 后重新启动后台以加载图片上传';
  renderAttachments(key,system,Boolean(locked));renderComposerTargets(key,system,Boolean(locked));
  $('composer-mention').hidden=system;$('composer-mention').disabled=Boolean(locked||!snapshot.features?.includes('manual_targets'));
  $('composer-role').options[1].textContent=s.name+' 消息（发到群）';$('composer-send').textContent=system?'提交系统提示词':'发送 '+s.name+' 消息';
  $('composer-send').disabled=Boolean(!system&&s.receive_only)||!ready||(!input.value.trim()&&!images.length&&!targets.mentions.length)||Boolean(locked)||(!system&&images.length>0&&!s.manual_images_ready)||(!system&&hasTargets&&!s.manual_targets_ready);
  $('composer-hint').textContent=system?'不立即回复；当前对话窗口 '+(s.text_limit===0?'不限字符':s.text_limit+' 字符')+'。Enter 提交，Shift+Enter 换行':!ready?'先启动并加载支持手动发送的群后台':hasTargets&&!s.manual_targets_ready?'回复和@需要新版群后台，请 /stop 后手动重启':images.length&&!s.manual_images_ready?'图片发送需要新版群后台，请 /stop 后手动重启':'发送到本群 '+selected+'，文字和图片同条发送并加入记忆。最多2张，单张≤5MB，合计≤8MB。Enter 发送，Shift+Enter 换行';
  const result=composerResults.get(key);$('composer-result').textContent=result?.text||'';$('composer-result').classList.toggle('error',Boolean(result?.error));
}
async function addComposerImages(files,target,plain=''){
  const key=target.key,group=Number(key.split(':')[1]);
  if(!key.startsWith('bot:'))throw Error('图片仅支持“消息（发到群）”，系统提示词不能添加图片');
  if(!snapshot?.features?.includes('manual_images'))throw Error('请 /stop 后重新启动后台以加载图片上传');
  if(composerBusy.has(key)||composerImageLoading.has(key)||manualPending.has(group))throw Error('当前消息正在发送，请等待结果');
  const previous=attachmentsFor(key),limits=snapshot.status.manual_image_limits||{max_images:2,max_image_bytes:5*1024*1024,max_total_bytes:8*1024*1024};
  if(previous.length+files.length>limits.max_images)throw Error('最多附加2张图片');
  let total=previous.reduce((sum,item)=>sum+item.file.size,0);
  for(const file of files){if(!file.size||file.size>limits.max_image_bytes)throw Error('单张图片上限5MB');if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)&&!(file.type===''&&/\.(png|jpe?g|gif|webp)$/i.test(file.name)))throw Error('只支持PNG、JPEG、GIF、WebP图片');total+=file.size;}
  if(total>limits.max_total_bytes)throw Error('图片附件合计上限8MB');
  composerImageLoading.add(key);renderComposer();
  try{
  let frozen;
  try{frozen=await Promise.all(files.map(async file=>new File([await file.arrayBuffer()],file.name,{type:file.type,lastModified:file.lastModified})));}
  catch{throw Error('图片读取失败，请重新上传或粘贴');}
  let slot=Math.max(0,...previous.map(image=>image.slot));const added=frozen.map(file=>({id:crypto.randomUUID(),slot:++slot,file,url:URL.createObjectURL(file)}));
  if(slot>999){for(const image of added)URL.revokeObjectURL(image.url);throw Error('图片编号过多，请清空附件后重试');}
  const text=currentDraftText(key),at=Math.min(target.start??text.length,text.length),end=Math.min(target.end??at,text.length);
  const inserted=plain+added.map(image=>'[图片'+image.slot+']').join('');
  if(text.length-(end-at)+inserted.length>2000){for(const image of added)URL.revokeObjectURL(image.url);throw Error('文字和图片占位符合计最多2000字');}
  composerAttachments.set(key,[...previous,...added]);const next=text.slice(0,at)+inserted+text.slice(end);composerDrafts[key]={...composerDrafts[key],text:next};
  if($('composer-text').dataset.key===key){$('composer-text').value=next;$('composer-text').focus();$('composer-text').setSelectionRange(at+inserted.length,at+inserted.length);}
  renderComposer();
  }finally{composerImageLoading.delete(key);renderComposer();}
}
function imagePayload(image){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(Error('图片读取失败'));reader.onload=()=>resolve({slot:image.slot,data:String(reader.result).split(',')[1]});reader.readAsDataURL(image.file);});}
async function checkManualSend(group){
  const pending=manualPending.get(group);if(!pending||pending.checking)return;pending.checking=true;
  try{
    const row=await api('/api/manual-send?'+new URLSearchParams({group,id:pending.id}));
    if(['queued','sending','delivered','record_pending'].includes(row.status)){composerResult(group,'bot',row.status==='record_pending'?'QQ已发送，本地记录正在补写':'手动消息正在排队或发送…');}
    else{
      manualPending.delete(group);manualDraftIDs.delete('bot:'+group);
      if(row.status==='sent'){
        const key='bot:'+group;
        if(currentDraftText(key)===pending.text&&attachmentSignature(key)===pending.attachments&&targetSignature(key)===pending.targets){
          clearAttachments(key);composerTargets.delete(key);composerDrafts[key]={text:''};if($('composer-text').dataset.key===key)$('composer-text').value='';
        }
        composerResult(group,'bot','已发送，消息 ID '+row.message_id+'；图文已加入上下文与手动消息记忆');
      }else composerResult(group,'bot',row.error||'未发送，请检查后台',true);
    }
  }catch(error){composerResult(group,'bot','暂时无法确认发送结果，请核对QQ：'+error.message,true);}
  finally{pending.checking=false;}
}
$('composer-role').onchange=()=>renderComposer();$('composer-lifetime').onchange=()=>renderComposer();$('composer-text').oninput=()=>renderComposer();
$('composer-upload').onclick=()=>{const input=$('composer-text');composerUploadTarget={key:composerKey(),start:input.selectionStart,end:input.selectionEnd};$('composer-files').click();};
$('composer-files').onchange=async()=>{try{const files=[...$('composer-files').files];if(files.length)await addComposerImages(files,composerUploadTarget||{key:composerKey()});}catch(error){composerResult(Number((composerUploadTarget?.key||composerKey()).split(':')[1]),'bot',error.message,true);}finally{$('composer-files').value='';composerUploadTarget=null;}};
$('composer-text').addEventListener('paste',async event=>{
  const files=[...(event.clipboardData?.items||[])].filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);if(!files.length)return;
  event.preventDefault();try{await addComposerImages(files,{key:composerKey(),start:event.target.selectionStart,end:event.target.selectionEnd},event.clipboardData.getData('text/plain')||'');}catch(error){composerResult(selected,$('composer-role').value,error.message,true);}
});
$('composer-text').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.altKey&&!event.metaKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();if(!$('composer-send').disabled)$('composer').requestSubmit();}};
$('composer').onsubmit=async event=>{
  event.preventDefault();const group=selected,role=$('composer-role').value,text=$('composer-text').value,key=composerKey(),images=attachmentsFor(key),attachmentIDs=attachmentSignature(key),targets=targetsFor(key),targetIDs=targetSignature(key);
  if((!text.trim()&&!images.length&&!targets.mentions.length)||composerBusy.has(key)||composerImageLoading.has(key)||role==='bot'&&manualPending.has(group))return;
  composerBusy.add(key);renderComposer();
  try{
    if(role==='system'){
      await act({action:'system',mode:'add',group,text,target_scope:$('composer-scope').value,lifetime:$('composer-lifetime').value,minutes:Number($('composer-minutes').value)},$('composer-send'));
      composerDrafts[key]={text:''};if($('composer-text').dataset.key===key&&$('composer-text').value===text)$('composer-text').value='';composerResult(group,role,'系统提示词已保存，不触发立即回复');
    }else{
      const fingerprint=JSON.stringify([text,attachmentIDs,targetIDs]);let request=manualDraftIDs.get(key);
      if(!request||request.fingerprint!==fingerprint){request={id:crypto.randomUUID().replaceAll('-',''),fingerprint};manualDraftIDs.set(key,request);}
      const payload=await Promise.all(images.map(imagePayload));
      const data=await act({action:'manual_send',group,text,images:payload,reply_id:targets.reply?.message_id||'',at_ids:targets.mentions.map(row=>row.id),request_id:request.id},$('composer-send'));
      manualPending.set(group,{id:data.request.id,text,attachments:attachmentIDs,targets:targetIDs});composerResult(group,role,'手动图文消息已排队，等待群后台发送');await checkManualSend(group);
    }
  }catch(error){composerResult(group,role,error.message,true);}finally{composerBusy.delete(key);renderComposer();}
};
async function api(path,body){if(path==='/api/action'&&body?.action==='knowledge')path='/api/knowledge';const response=await fetch(path,{headers:{'X-QQ-Debug-Key':token,...(body?{'Content-Type':'application/json'}:{})},method:body?'POST':'GET',...(body?{body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
async function act(action,button){if(backendReady!==true&&!['command','restart','pause','delete'].includes(action.action))throw Error('调试服务还是旧版本：请在 CLI 输入 /exit 后重新启动 Debug');const operationGroup=action.group??selected;if(button)button.disabled=true;toast('正在处理…');try{const data=await api('/api/action',{group:operationGroup,...action});if(data.selected!==undefined&&selected===operationGroup)selected=data.selected;toast(data.message||'已完成');if(data.shutdown){closed=true;pollSequence++;$('connection').textContent='调试服务已退出';setDot('connection-dot',false);document.querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=true);}else await poll(true);return data;}catch(error){toast(error.message,true);throw error;}finally{if(button)button.disabled=closed;}}
function setDot(id,online,error=false){$(id).classList.toggle('live',online);$(id).classList.toggle('error',error);}

const groupViews=new Map(),emptyGroups=node('div','','empty');
function renderGroups(){
  if(isPrivateOpen())return;
  const term=$('group-search').value.toLowerCase(),container=$('groups'),present=new Set(snapshot.groups.map(group=>String(group.id))),visible=[];
  $('group-count').textContent=snapshot.groups.length;
  for(const [id,view] of groupViews)if(!present.has(id)){view.button.remove();groupViews.delete(id);}
  for(const group of snapshot.groups){
    const id=String(group.id);let view=groupViews.get(id);
    if(!view){
      const button=node('button',undefined,'group'),avatar=avatarNode('group',group.id,group.name,'group-avatar',group.id),dot=node('span',undefined,'dot'),text=node('span'),name=node('span','','group-name'),sub=node('span','','group-sub');
      button.type='button';button.dataset.group=id;avatar.append(dot);text.append(name,sub);button.append(avatar,text);
      button.onclick=()=>{selectGroup(group.id);if(['system','stickers'].includes(currentTab))switchTab('logs');};
      view={button,avatar,dot,name,sub};groupViews.set(id,view);
    }
    const {button,dot,name,sub}=view,title=group.name+'（'+group.id+'）',subtitle=groupState(group,group)[0]+' · '+group.id,active=String(selected)===id;
    button.classList.toggle('active',active);button.setAttribute('aria-current',String(active));button.title=title;
    dot.classList.toggle('live',group.enabled!==false&&group.online&&!group.stopped);
    if(name.textContent!==group.name)name.textContent=group.name;
    if(sub.textContent!==subtitle)sub.textContent=subtitle;
    view.avatar.firstChild.nodeValue=initial(group.name);
    const cached=avatarCache.get(group.id+':group:'+group.id);
    if(cached?.failed&&Date.now()-cached.time>60000&&!view.avatar.querySelector('img')){
      const replacement=avatarNode('group',group.id,group.name,'group-avatar',group.id);replacement.append(dot);view.avatar.replaceWith(replacement);view.avatar=replacement;
    }
    if((group.name+' '+id).toLowerCase().includes(term))visible.push(button);
  }
  if(!visible.length){emptyGroups.textContent=snapshot.groups.length?'没有匹配的群':'还没有授权的群，在下面输入群号添加';visible.push(emptyGroups);}
  for(const child of [...container.children])if(!visible.includes(child))child.remove();
  visible.forEach((child,index)=>{if(container.children[index]!==child)container.insertBefore(child,container.children[index]||null);});
}
function selectGroup(group){stashPromptDraft();selected=group;selectedEvent=null;lastEvents='';configData=null;databaseData=null;dbSequence++;$('db-add').disabled=true;$('settings').replaceChildren(node('div','正在读取这个群的设置…','empty'));poll(true).then(()=>loadActivePane()).catch(error=>toast(error.message,true));}

let memberSequence=0,memberEditor=null,memberEditorSequence=0,memberScopedReady=false;
const memberLabelText=value=>({AI:'AI',bot:'bot',human:'真人',owner:'bot主'}[value]||'未标记');
const memberSourceText=value=>({manual:'手动',model:'模型推断',auto:'规则推断'}[value]||'未标记');
function memberEvidence(member){return (member.blocked?'已屏蔽（'+memberSourceText(member.block_source)+'），不进入模型。':'')+'当前：'+memberLabelText(member.label)+'（'+memberSourceText(member.source)+'）；独立指令响应 '+(member.observations||0)+' 次。'+(Object.keys(member.prefix_counts||{}).length?'各前缀：'+Object.entries(member.prefix_counts).map(([prefix,count])=>prefix+' '+count+'次').join('、')+'。':'')+(member.evidence||[]).map(item=>item.command+' → 消息 '+item.response_id).join('；')+(member.model_history?.length?' 模型资料修改：'+member.model_history.map(item=>({account_type:'账号类型',extra_names:'额外昵称',account_note:'账号说明'}[item.field]||item.field)+' · 证据 '+item.evidence_message_ids.join('、')).join('；'):'');}
async function memberScope(data,group){
  if(['global','group'].includes(data.scope))return data.scope;
  const settings=await api('/api/config?group='+group);
  return settings.config.member_labels_global_enabled?'global':'group';
}
async function loadMembers(){
  const ticket=++memberSequence,group=selected,data=await api('/api/members?'+new URLSearchParams({group,q:$('member-search').value}));
  const scope=await memberScope(data,group);
  if(ticket!==memberSequence||group!==selected)return;
  memberScopedReady=Boolean(data.scoped_marks);
  $('member-count').textContent=(data.scoped_marks?'按账号选择全局 / 本群':scope==='global'?'全局标记 · 所有群聊与私聊共用':'仅当前群标记')+' · 共 '+data.total+' 位群友，匹配 '+data.matched+' 位'+(data.matched>data.limit?'；最多显示 '+data.limit+' 位，请缩小搜索范围':'');
  const table=node('table'),head=node('tr');for(const title of ['群友','额外昵称','标记','证据 / 说明','操作'])head.append(node('th',title));table.append(head);
  for(const member of data.members){
    const row=node('tr'),person=node('td'),names=node('td',member.aliases?.join('、')||'—'),type=node('td',(member.blocked?'屏蔽 · ':'')+memberLabelText(member.label)+' · '+memberSourceText(member.source)),evidence=node('td',(member.observations?'指令响应 '+member.observations+' 次。':'')+(member.note||'')),actions=node('td'),open=node('button','编辑资料');
    if(member.mark_scope)type.append(node('span',member.mark_scope==='global'?'全局':'本群','scope-badge'+(member.mark_scope==='global'?' override':'')));
    const name=member.card||member.nickname||member.qq_id,avatar=avatarNode('user',member.qq_id,name,'group-avatar',group);avatar.classList.add('member-avatar-button');avatar.setAttribute('role','button');avatar.tabIndex=0;avatar.setAttribute('aria-label','查看并标记群友 '+name);avatar.onclick=()=>openMember(member.qq_id,name);avatar.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();avatar.click();}};
    const cell=node('div',undefined,'member-person');cell.append(avatar,node('span',name+' · '+member.qq_id));person.append(cell);open.type='button';open.onclick=()=>openMember(member.qq_id,name).catch(error=>toast(error.message,true));actions.append(open);row.append(person,names,type,evidence,actions);table.append(row);
  }
  $('member-records').replaceChildren(data.members.length?table:node('div','没有匹配的群友，可在上面填写QQ号手动标记','empty'));
}
async function openMember(account,name){
  const ticket=++memberEditorSequence,group=selected;
  const data=await api('/api/members?'+new URLSearchParams({group,q:account}));
  const scope=await memberScope(data,group);
  if(ticket!==memberEditorSequence||group!==selected)return;
  const member=data.members.find(item=>String(item.qq_id)===String(account))||{qq_id:String(account),nickname:name},person=$('member-dialog-person');memberEditor={group,account:String(account),aliases:[...(member.aliases||[])],blocked:Boolean(member.blocked),blockDirty:false,scope:member.mark_scope||data.default_mark_scope||'global',scopeDirty:false,scopedReady:Boolean(data.scoped_marks)};
  person.replaceChildren(avatarNode('user',account,name,'group-avatar',group),node('strong',member.card||member.nickname||name),node('span',String(account),'code'));
  renderMemberScope();
  $('member-dialog-label').value=member.manual_label||'auto';$('member-dialog-aliases-summary').textContent=collectionSummary(memberEditor.aliases);$('member-dialog-note').value=member.note||'';
  $('member-dialog-blocked').checked=Boolean(member.blocked);$('member-dialog-blocked').onchange=()=>{if(memberEditor){memberEditor.blockDirty=true;if($('member-dialog-blocked').checked&&!memberEditor.scopeDirty){memberEditor.scope='global';renderMemberScope();}}};
  $('member-dialog-evidence').textContent=memberEvidence(member);
  $('member-dialog-result').textContent='';if(!$('member-dialog').open)$('member-dialog').showModal();
}
function renderMemberScope(){
  if(!memberEditor)return;
  for(const button of $('member-dialog-scope').querySelectorAll('[data-member-scope]')){const active=button.dataset.memberScope===memberEditor.scope;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));button.disabled=!memberEditor.scopedReady;}
  $('member-dialog-scope-note').textContent=!memberEditor.scopedReady?'请手动 /stop 并重新启动控制台，加载按账号切换范围的后台后再保存。':(memberEditor.scope==='global'?'保存后，此账号的类型、昵称、说明和屏蔽状态在所有群聊及私聊生效。':'保存后，此账号恢复各会话独立标记，本次修改只写入当前群；其他账号的范围不会改变。')+'切换在保存资料后生效；新屏蔽默认选择全局。';
  if(memberEditor.scopeDirty)$('member-dialog-result').textContent='范围已选择，保存资料后生效';
}
for(const button of $('member-dialog-scope').querySelectorAll('[data-member-scope]'))button.onclick=()=>{if(memberEditor){memberEditor.scope=button.dataset.memberScope;memberEditor.scopeDirty=true;renderMemberScope();}};
$('member-dialog-aliases').onclick=()=>{if(!memberEditor)return;const editor=memberEditor;openCollectionEditor({title:'额外昵称',value:editor.aliases,itemType:'name_list',note:'用＋添加昵称、−删除，最多10个，每个最多40字。先保存昵称草稿，再保存群友资料后生效。',onSave:async aliases=>{if(memberEditor!==editor)throw Error('群友资料窗口已切换，请重新编辑');editor.aliases=[...aliases];$('member-dialog-aliases-summary').textContent=collectionSummary(editor.aliases);$('member-dialog-result').textContent='额外昵称已更新，保存群友资料后生效';}});};
$('member-dialog-close').onclick=()=>{$('member-dialog').close();memberEditor=null;};
$('member-dialog-form').onsubmit=async event=>{event.preventDefault();if(!memberEditor)return;const editor={...memberEditor},button=$('member-dialog-save');button.disabled=true;$('member-dialog-aliases').disabled=true;try{
  if(!editor.scopedReady)throw Error('请手动重启控制台后再保存标记范围');
  const aliases=[...editor.aliases];
  const saved=await act({action:'member_label',group:editor.group,account:editor.account,mark_scope:editor.scope,label:$('member-dialog-label').value,aliases,note:$('member-dialog-note').value,...(editor.blockDirty?{blocked:$('member-dialog-blocked').checked}:{})},button);
  $('member-dialog-blocked').checked=Boolean(saved.member.blocked);if(memberEditor)memberEditor.blockDirty=false;
  if(memberEditor){memberEditor.scope=saved.member.mark_scope||editor.scope;memberEditor.scopeDirty=false;renderMemberScope();}
  $('member-dialog-evidence').textContent=memberEvidence(saved.member);
  $('member-dialog-label').value=saved.member.manual_label||'auto';if(memberEditor){memberEditor.aliases=[...(saved.member.aliases||[])];$('member-dialog-aliases-summary').textContent=collectionSummary(memberEditor.aliases);}$('member-dialog-note').value=saved.member.note||'';
  $('member-dialog-result').textContent='已保存，后续处理按新资料与屏蔽设置生效';if(currentTab==='members'&&selected===editor.group)await loadMembers();
}catch(error){$('member-dialog-result').textContent=error.message;}finally{button.disabled=false;$('member-dialog-aliases').disabled=false;}};
$('member-form').onsubmit=async event=>{event.preventDefault();const group=selected;try{if(!memberScopedReady)throw Error('请手动重启控制台后再保存标记范围');await act({action:'member_label',group,account:$('member-account').value.trim(),mark_scope:$('member-scope').value,label:$('member-label').value,note:$('member-note').value},$('member-save'));if(group===selected)await loadMembers();}catch(error){toast(error.message,true);}};
$('member-refresh').onclick=()=>loadMembers().catch(error=>toast(error.message,true));
$('member-search').oninput=()=>loadMembers().catch(error=>toast(error.message,true));

const eventKey=row=>JSON.stringify([row.time,row.kind,row.message_id,row.role,['received','sent'].includes(row.kind)?'':row.text]);
const contextID=row=>String(row.context_id||row.trace_id||row.request_id||row.message_id||'—');
const validTime=row=>Number.isFinite(Number(row.time))&&Number(row.time)>0;
function eventTime(row){return validTime(row)?new Intl.DateTimeFormat('zh-CN',{...shanghai,hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(row.time*1000)):'—';}
function eventDate(row){return validTime(row)?new Intl.DateTimeFormat('zh-CN',{...shanghai,month:'long',day:'numeric',weekday:'short'}).format(new Date(row.time*1000)):'';}
function eventSummary(row){let text=row.text||'';if(row.kind==='decision'){try{const obj=JSON.parse(text);text=Object.entries(obj).map(([key,value])=>key+'：'+(typeof value==='object'?JSON.stringify(value):value)).join('，');}catch{}}return text.replace(/\s+/g,' ').trim();}
function levelClass(row){return ['ERROR','CRITICAL'].includes(row.level)?'bad':row.level==='WARNING'?'warn':row.kind;}
const recordViews=new Map();let recordViewScope='';
function recordSignature(row){return JSON.stringify(['received','sent'].includes(row.kind)?
  [row.text,row.sender,row.user_id,row.quote,row.blocked,row.bot_command,row.manual,row.historical,row.level,row.forward,row.files,(row.image_info||[]).map(image=>image.key),row.kind==='sent'?snapshot.status.name:'']:[row,snapshot.features]);}
function renderRecords(){
  const level=$('level-filter').value,rows=snapshot.events.filter(row=>level==='all'||(level==='WARNING'?['WARNING','ERROR','CRITICAL'].includes(row.level):['ERROR','CRITICAL'].includes(row.level)));
  const scope=selected+':'+(snapshot.status.chat_image_display||'image'),fingerprint=scope+level+snapshot.status.name+JSON.stringify(rows);
  if(lastEvents===fingerprint)return;lastEvents=fingerprint;
  const container=$('records'),scroll=container.scrollTop,atBottom=container.scrollHeight-scroll-container.clientHeight<80;
  if(recordViewScope!==scope){releaseChatImageLoads(container);recordViews.clear();container.replaceChildren();recordViewScope=scope;}
  if(!rows.some(row=>eventKey(row)===selectedEvent))selectedEvent=null;
  const desired=[],liveKeys=new Set();let day='';
  const reuse=(key,signature,create)=>{liveKeys.add(key);let entry=recordViews.get(key);if(!entry||entry.signature!==signature){if(entry)releaseChatImageLoads(entry.item);entry={signature,item:create()};recordViews.set(key,entry);}desired.push(entry.item);return entry.item;};
  for(const row of rows){
    const date=eventDate(row);if(date&&date!==day){day=date;reuse('day:'+date,date,()=>node('div',date,'day-mark'));}
    const chat=['received','sent'].includes(row.kind),key=eventKey(row);
    const item=reuse('row:'+key,recordSignature(row),()=>chat?messageRow(row):row.kind==='system'?systemRow(row):flowRow(row));
    item.updateRecord?.(row);item.title=eventSummary(row);item.classList.toggle('selected',key===selectedEvent);item.tabIndex=0;item.setAttribute('role','option');item.setAttribute('aria-selected',String(key===selectedEvent));item.dataset.level=row.level||'INFO';
    const choose=()=>{selectedEvent=key;container.querySelectorAll('[role=option]').forEach(other=>{const active=other===item;other.classList.toggle('selected',active);other.setAttribute('aria-selected',String(active));});renderDetail(row);};
    item.onclick=choose;item.onkeydown=event=>{if(event.target===item&&(event.key==='Enter'||event.key===' ')){event.preventDefault();choose();}};
  }
  if(!rows.length)reuse('empty',[$('record-search').value,level,kind].join(':'),()=>node('div',$('record-search').value||level!=='all'||kind!=='all'?'没有符合筛选条件的记录':'这个群还没有记录。群里有新消息后会出现在这里','empty'));
  for(const [key,entry] of recordViews){if(!liveKeys.has(key)){releaseChatImageLoads(entry.item);entry.item.remove();recordViews.delete(key);}}
  // Leave unchanged nodes connected: replacing the list restarts image/GIF loads.
  const keep=new Set(desired);for(const child of [...container.children])if(!keep.has(child)){releaseChatImageLoads(child);child.remove();}
  let cursor=container.firstChild;for(const item of desired){if(item===cursor)cursor=cursor.nextSibling;else container.insertBefore(item,cursor);}
  if($('follow').checked&&atBottom)container.scrollTop=container.scrollHeight;else container.scrollTop=scroll;
  renderDetail(rows.find(row=>eventKey(row)===selectedEvent));$('trace-count').textContent=rows.length;
  const chars=kind==='flow'?(snapshot.status.flow_text_chars??snapshot.status.text_chars):(snapshot.status.chat_text_chars??snapshot.status.text_chars);
  $('record-count').textContent='显示 '+rows.length+' 条 · 本地共保存 '+snapshot.status.records+' 条，'+(kind==='flow'?'执行窗口 ':'对话窗口 ')+windowUsage(chars,snapshot.status.text_limit);
}
const chatImageModes=new Map(),chatImageLoaders=new WeakMap();
const chatImageObserver='IntersectionObserver' in window?new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;chatImageObserver.unobserve(entry.target);const load=chatImageLoaders.get(entry.target);chatImageLoaders.delete(entry.target);if(entry.target.isConnected&&load)load();}},{root:$('records'),rootMargin:'180px'}):null;
function releaseChatImageLoads(area){for(const slot of area.querySelectorAll('.chat-image-slot')){chatImageObserver?.unobserve(slot);chatImageLoaders.delete(slot);}}
function chatImageText(text){return String(text||'').replace(/\[同图[:：][^\]]*\]|\[图片(?:\d*|，(?:未读取内容|读取中|读取失败)|摘要[:：][^\]]*)\]|\[已发送图片或表情\]|\[(?:商城表情|表情包)[:：](?:[^\[\]]|\[[^\]]*\])*\]/g,' ').trim();}
function chatMediaKey(group,row){return group+':'+row.message_id;}
function chatMode(group,row){return chatImageModes.get(chatMediaKey(group,row))||snapshot.status.chat_image_display||'image';}
function renderChatImage(row,image,index,group){
  const slot=node('div',undefined,'chat-image-slot');slot.append(node('span','正在加载图片…','chat-image-placeholder'));
  const failed=error=>{if(!slot.isConnected)return;const fallback=image.summary?.description||image.summary?.visible_text||[...String(row.text||'').matchAll(/\[(?:图片摘要|表情包|商城表情)[:：]([^\]]*)\]/g)][index]?.[1]||'原图暂不可用';const retry=node('button','重试','chat-image-retry');retry.type='button';retry.onclick=event=>{event.stopPropagation();slot.replaceChildren(node('span','正在加载图片…','chat-image-placeholder'));load(true);};slot.replaceChildren(node('span',fallback,'chat-image-fallback'),retry);slot.title=error.message;};
  const load=async(force=false)=>{
    try{const url=await messageImageURL(group,row,image,force);if(!slot.isConnected)return;const img=node('img',undefined,'chat-message-image');img.alt=image.summary?.description||'消息图片 '+(index+1);img.decoding='async';img.onerror=()=>failed(Error('浏览器无法解码原图'));img.src=url;const zoom=node('button','放大原图','chat-image-zoom');zoom.type='button';zoom.onclick=event=>{event.stopPropagation();showOriginal(url,'消息 '+row.message_id+' · 原图 '+(index+1));};slot.replaceChildren(img,zoom);}
    catch(error){failed(error);}
  };
  if(chatImageObserver){chatImageLoaders.set(slot,load);chatImageObserver.observe(slot);}else queueMicrotask(()=>load());
  return slot;
}
function forwardCard(row){
  const data=row.forward,card=node('div',undefined,'forward-card');
  card.append(node('strong',data.title||'聊天记录','forward-title'));
  for(const entry of (data.preview||[]).slice(0,4)){
    const text=(String(entry.sender||'')+'：'+String(entry.text||'')).replace(/\s+/g,' ').trim(),chars=Array.from(text);
    const line=node('div',chars.slice(0,20).join('')+(chars.length>20||entry.truncated?'...':''),'forward-preview');
    line.title=(entry.time||'时间未知')+' '+text;card.append(line);
  }
  card.append(node('div',data.status==='disabled'?'读取聊天记录已关闭':data.status==='unavailable'?'聊天记录暂时无法读取':Number.isInteger(data.count)?'查看 '+data.count+' 条聊天记录':'查看聊天记录','forward-footer'));
  return card;
}
const forwardDetails=new Map();
async function renderForwardDetail(row){
  const group=selected,identity=eventKey(row),cacheKey=group+':'+row.message_id;
  try{
    let text=forwardDetails.get(cacheKey);
    if(!text){const data=await api('/api/forward-message?'+new URLSearchParams({group,message_id:row.message_id}));text=data.text;forwardDetails.set(cacheKey,text);while(forwardDetails.size>32)forwardDetails.delete(forwardDetails.keys().next().value);}
    if(group===selected&&identity===selectedEvent)$('detail-content').textContent=text;
  }catch(error){if(group===selected&&identity===selectedEvent)$('detail-content').textContent=(row.text||'[聊天记录]')+'\n（'+error.message+'）';}
}
function fileCard(file){
  const card=node('div',undefined,'qq-file-card'),icon=node('span','▤','qq-file-icon'),info=node('div',undefined,'qq-file-info');
  const size=Number(file.size)||0;info.append(node('strong',file.name||'未命名文件'),node('small',size?(size<1024?size+' B':size<1048576?(size/1024).toFixed(1)+' KB':(size/1048576).toFixed(2)+' MB'):'文件大小未知'));
  card.append(icon,info);return card;
}
function messageRow(row){
  const group=selected,sent=row.kind==='sent',name=sent?(snapshot.status.name||'Bot'):(row.sender||'群友');let pictures=row.image_info||[];
  const item=node('div',undefined,'msg '+row.kind);item.title=eventSummary(row);
  const body=node('div',undefined,'msg-body'),head=node('div',undefined,'msg-head');head.append(node('strong',name),node('span',eventTime(row)));if(row.blocked)head.append(node('span','屏蔽','member-block-badge'));if(row.bot_command)head.append(node('span','bot指令','tag'));
  const bubble=node('div',undefined,'bubble'),toggle=pictures.length?node('button',undefined,'chat-media-toggle'):null;
  const render=()=>{
    releaseChatImageLoads(bubble);bubble.replaceChildren();if(row.quote)bubble.append(node('span',quoteSummary(row),'bubble-quote'));
    const imageMode=pictures.length&&chatMode(group,row)==='image';bubble.classList.toggle('image-bubble',Boolean(imageMode));
    if(row.files?.length){const words=String(row.text||'').replace(/\[文件：[^\]]*\]/g,'').trim();if(words)bubble.append(node('span',words));for(const file of row.files)bubble.append(fileCard(file));if(imageMode){const gallery=node('div',undefined,'chat-image-gallery');pictures.forEach((image,index)=>gallery.append(renderChatImage(row,image,index,group)));bubble.append(gallery);}}
    else if(row.forward){bubble.classList.add('forward-bubble');bubble.append(forwardCard(row));}
    else if(imageMode){const words=chatImageText(row.text);if(words)bubble.append(node('span',words));const gallery=node('div',undefined,'chat-image-gallery');pictures.forEach((image,index)=>gallery.append(renderChatImage(row,image,index,group)));bubble.append(gallery);}
    else bubble.append(document.createTextNode(row.text||'[空消息]'));
    if(toggle){toggle.textContent=imageMode?'显示文字':'显示图片';toggle.setAttribute('aria-label',(imageMode?'显示文字摘要':'显示图片')+'，消息 '+row.message_id);}
  };
  if(toggle){const change=()=>{chatImageModes.set(chatMediaKey(group,row),chatMode(group,row)==='image'?'text':'image');while(chatImageModes.size>1000)chatImageModes.delete(chatImageModes.keys().next().value);render();};toggle.type='button';toggle.title='双击消息气泡也可以切换；不修改记录或上下文';toggle.onclick=event=>{event.stopPropagation();change();};bubble.title='双击切换图片 / 文字摘要';bubble.ondblclick=event=>{if(event.target.closest('button'))return;event.preventDefault();event.stopPropagation();change();};}
  render();body.append(head,bubble);
  const tags=[];if(pictures.length)tags.push('含 '+pictures.length+' 张图片');if(row.manual)tags.push('手动发送');if(row.historical)tags.push('历史恢复');if(row.level&&row.level!=='INFO')tags.push(row.level);
  if(tags.length||toggle){const line=node('div',undefined,'msg-tags');for(const tag of tags)line.append(node('span',tag));if(toggle)line.append(toggle);body.append(line);}
  item.updateRecord=latest=>{row=latest;pictures=latest.image_info||[];};
  const avatar=avatarNode('user',row.user_id,name,'avatar');
  if(/^[1-9]\d{4,14}$/.test(String(row.user_id||''))){avatar.classList.add('member-avatar-button');avatar.setAttribute('role','button');avatar.tabIndex=0;avatar.setAttribute('aria-label','查看并标记群友 '+name);const open=event=>{event.stopPropagation();openMember(row.user_id,name).catch(error=>toast(error.message,true));};avatar.onclick=open;avatar.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();open(event);}};}
  if(/^-?\d+$/.test(String(row.message_id||''))){
    const tools=node('div',undefined,'msg-manual-actions'),reply=node('button','回复');reply.type='button';reply.onclick=event=>{event.stopPropagation();selectManualReply(row);};tools.append(reply);
    if(/^[1-9]\d{4,14}$/.test(String(row.user_id||''))){const mention=node('button','@');mention.type='button';mention.setAttribute('aria-label','@'+name);mention.onclick=event=>{event.stopPropagation();try{const key=prepareManualTarget();insertManualMention({id:row.user_id,name},{key});}catch(error){toast(error.message,true);}};tools.append(mention);}body.append(tools);
  }
  item.append(avatar,body);return item;
}
function flowRow(row){const item=node('div',undefined,'flow '+row.kind);item.title=eventSummary(row);item.append(node('span',eventTime(row),'flow-time'),node('span',(labels[row.kind]||row.kind)+(row.role?' · '+row.role:''),'kind-chip '+levelClass(row)),node('span',eventSummary(row)||'—','flow-text'));return item;}
function jsonView(target,value){target.replaceChildren();const json=JSON.stringify(value,null,2),regex=/("(?:\\.|[^"\\])*"\s*:?)|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi;let offset=0;for(const match of json.matchAll(regex)){target.append(document.createTextNode(json.slice(offset,match.index)));target.append(node('span',match[0],match[1]?(match[0].endsWith(':')?'json-key':'json-string'):match[2]?'json-literal':'json-number'));offset=match.index+match[0].length;}target.append(document.createTextNode(json.slice(offset)));}
function renderDetail(row){$('detail').hidden=!row;$('detail-empty').hidden=Boolean(row);document.querySelector('.chat-layout').classList.toggle('has-detail',Boolean(row));if(!row)return;$('detail-type').textContent=(labels[row.kind]||row.kind)+(row.role?' · '+row.role:'');$('detail-type').className='kind-chip '+levelClass(row);const props=[['时间',(eventDate(row)+' '+eventTime(row)).trim(),false],['发送者',row.kind==='sent'?(snapshot.status.name||'Bot'):(row.sender||'—'),false],['消息 ID',row.message_id||'—',true],['上下文 ID',contextID(row),true],['引用原 ID',row.reply_id||'—',true],['日志级别',row.level||'INFO',true],['来源',row.historical?'历史恢复':'实时记录',false]];$('detail-properties').replaceChildren();for(const [label,value,code] of props)$('detail-properties').append(node('dt',label),node('dd',String(value),code?'code':''));$('detail-content').textContent=row.text||'（没有文字内容）';if(row.forward)renderForwardDetail(row);renderQuote(row);renderImages(row);jsonView($('detail-json'),row);const valid=['received','sent'].includes(row.kind)&&/^-?\d+$/.test(row.message_id||'');$('delete-event').hidden=!valid;$('detail-reply').hidden=!valid;$('detail-mention').hidden=!valid||!/^[1-9]\d{4,14}$/.test(String(row.user_id||''));
const aiReply=$('detail-ai-reply');
aiReply.hidden=!valid||row.kind!=='received';
aiReply.disabled=!!row.blocked;
aiReply.onclick=()=>act({action:'ai_reply',group:selected,message_id:row.message_id},aiReply).catch(()=>{});
const imageTask=$('detail-image-task');
imageTask.hidden=!valid||row.kind!=='received'||!snapshot.status.image_generation_current_enabled;
imageTask.disabled=!!row.blocked;
imageTask.onclick=()=>act({action:'image_force',group:selected,message_id:row.message_id},imageTask).catch(()=>{});
$('detail-task').hidden=!valid||row.kind!=='received'||!snapshot.status.tasks_current_enabled;$('detail-task').disabled=!!row.blocked;$('detail-task').onclick=()=>act({action:'task_force',group:selected,message_id:row.message_id},$('detail-task')).catch(()=>{});$('detail-reply').onclick=()=>selectManualReply(row);$('detail-mention').onclick=()=>{try{const key=prepareManualTarget();insertManualMention({id:row.user_id,name:row.sender||row.user_id},{key});}catch(error){toast(error.message,true);}};$('copy-id').disabled=contextID(row)==='—';$('copy-id').onclick=async()=>{try{await navigator.clipboard.writeText(contextID(row));toast('已复制 ID：'+contextID(row));}catch{toast('浏览器不允许复制，请在“原始数据”里手动选中 ID',true);}};$('delete-event').onclick=()=>{if(confirm('删除这条本地记录和 Bot 上下文？\nQQ 里的原消息不会撤回；当前群会短暂停止后自动恢复。'))act({action:'delete',message_id:row.message_id},$('delete-event')).catch(()=>{});};}
function closeDetail(){selectedEvent=null;document.querySelectorAll('#records [role=option]').forEach(item=>{item.classList.remove('selected');item.setAttribute('aria-selected','false');});renderDetail(null);}
$('detail-close').onclick=closeDetail;

function chips(value){const box=node('div',undefined,'stat-chips'),entries=Object.entries(value||{});if(!entries.length)return node('span','—');for(const [key,count] of entries)box.append(node('span',key+' '+(typeof count==='object'?JSON.stringify(count):count)));return box;}
function clock(value){if(!value)return '—';const date=typeof value==='number'?new Date(value*1000):new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',shanghai);}
function groupState(s,group){if(s.enabled===false||group?.enabled===false)return ['未启用','warn'];if(group?.stopped||s.stopped)return ['已暂停','warn'];if(s.not_in_group)return ['不在此群','warn'];if(s.error)return ['运行异常','bad'];if(s.waiting_for_qq)return ['等待 QQ 登录','warn'];if(!s.pid&&!s.online)return ['等待启动','warn'];if(s.worker_running===false&&s.pid)return ['后台未运行','warn'];if(s.last_poll&&Date.now()-Date.parse(s.last_poll)>120000)return ['刷新停滞','warn'];if(!s.online)return ['等待 QQ 连接','warn'];if(s.session_paused||group?.session_paused)return ['会话暂停','warn'];if(s.receive_only||group?.receive_only)return ['仅接收','warn'];return ['运行中','ok'];}
const avatarRefreshVersions=new Map();
function renderStatus(){if(isPrivateOpen())return;$('task-tab-button').hidden=!snapshot.status.tasks_enabled;if(currentTab==='tasks'&&!snapshot.status.tasks_enabled){switchTab('logs');return;}const s=snapshot.status,group=snapshot.groups.find(group=>group.id===selected),[stateText,stateTone]=groupState(s,group),global=['system','stickers'].includes(currentTab);
  $('group-title').textContent=global?(currentTab==='system'?'所有群总览':'共享表情库'):group?.name||String(selected);
  $('group-actions').hidden=global;$('group-tabs').hidden=global;document.querySelector('.group-meta').hidden=global;
  $('task-container-restart').hidden=!s.task_container_mode;
  $('group-status').textContent=stateText;$('group-status').className='status-pill '+stateTone;$('group-status').title=s.enabled===false||group?.enabled===false?'前往行为设置 → Bot 身份与接入，启用机器人':s.connection_error||s.error||'';
  $('group-subtitle').textContent='群号 '+selected;$('session-detail').textContent=s.model+' · '+s.effort+(s.fast?' · Fast':'')+(s.no_cooldown?' · 无冷却':'');
  $('pause').hidden=Boolean(group?.stopped)||s.enabled===false||group?.enabled===false||!s.pid;$('restart').textContent=group?.stopped?'恢复运行':s.pid?'重启':'启动';$('restart').disabled=s.enabled===false||group?.enabled===false;$('restart').title=$('restart').disabled?'请先在行为设置中启用机器人':'';
  $('session-stop').textContent=s.session_paused?'恢复当前会话':'终止当前会话';$('session-stop').setAttribute('aria-pressed',String(Boolean(s.session_paused)));
  if(s.avatar_refresh&&avatarRefreshVersions.get(selected)!==s.avatar_refresh){avatarRefreshVersions.set(selected,s.avatar_refresh);refreshVisibleAvatars(true).catch(()=>{});}
  $('receive-only').textContent=s.receive_only?'\u6062\u590dAI\u5904\u7406':'\u4ec5\u63a5\u6536\u6d88\u606f';$('footer-model').textContent=s.model;if($('cooldown')){$('cooldown').checked=!s.no_cooldown;$('cooldown').closest('.edit').querySelector('.switch-label').textContent=s.no_cooldown?'关闭':'开启';}
  if(currentTab!=='status')return;
  const gate=s.participation||{},gateReasons={no_new_messages:'没有待处理的新消息',waiting_messages:'等待更多新消息',frequency_disabled:'参与强度为 0',cooldown:'水群冷却中',planner_backoff:'规划间隔等待',model_budget:'模型额度用尽',reply_budget:'发送额度用尽',direct_call_priority:'先处理点名消息',text_job_running:'上一轮还在处理',no_usable_hook:'没有适合的接话点',message_threshold:'已达到消息门槛',idle_compensation:'群聊空窗补偿',dynamic_score:'动态评分通过'};
  const qqUnchecked=s.enabled===false||group?.enabled===false||group?.stopped||(!s.pid&&!s.online);
  const vitals=[['QQ 连接',qqUnchecked?'未检测':s.online?'在线':s.waiting_for_qq?'等待登录':'连接中',qqUnchecked?'':s.online?'ok':'warn',qqUnchecked?'群后台尚未运行，不能据此判断 OneBot 是否在线':s.waiting_for_qq?'等待 QQ 登录':''],['Bot 后台',stateText,stateTone,s.pid&&!group?.stopped?'进程 '+s.pid:s.error||''],['今日模型调用',String(s.model_calls_today??0),'',s.model],['本轮参与强度',Math.round((gate.frequency??gate.probability??s.rate)*100)+'%','',gateReasons[gate.reason]||gate.reason||'等待新消息']];
  const cleanup=s.image_cleanup||{},cleanupNames={interval:'按分钟间隔',daily:'每日定时',expiry:'每张图片单独到期',windows:'Windows 任务计划程序'};
  const sections=[['连接与运行',[['最后一次轮询',clock(s.last_poll)],['冷却模式',s.no_cooldown?'无冷却':'按设置冷却'],['服务模式',s.fast?'Fast':'Standard'],['思考等级',s.effort]]],
    ['正在聊什么',[['当前话题',s.conversation_state?.topic?.text||'—'],['在等谁回应',s.conversation_state?.awaiting?.user_id||'—'],['进行中的任务',(s.conversation_state?.tasks||[]).map(task=>task.lane+' / '+task.stage).join('，')||'—'],['七天内交流',(s.conversation_state?.exchanges||0)+' 次']]],
    ['自动水群',[['触发方式',gate.mode==='dynamic'?'动态参与评分':'消息门槛 + 空窗补偿'],['待处理 / 门槛',(gate.pending_count??0)+' / '+(gate.threshold??'—')],['本轮判断',gateReasons[gate.reason]||gate.reason||'等待新消息'],['最近结果',chips(s.participation_outcomes)]]],
    ['记录与记忆',[['本页记录',s.records+' 条'],['聊天窗口',windowUsage(s.chat_text_chars??s.text_chars,s.text_limit)],['执行日志窗口',windowUsage(s.flow_text_chars??0,s.text_limit)],['本地记录文字合计',s.text_chars+' 字'],['模型输入上限',s.prompt_limit===0?'不限':s.prompt_limit+' 字'],['图片摘要',s.image_summaries+' 张'],['已观察消息',s.observed_messages+' 条'],['已归档话题',s.memory_stats?.episodes||0],['已修订事实',s.memory_stats?.revised_facts||0],['群记忆',chips(s.memory_stats)]]],
    ['生成图片清理',[['清理方式',cleanupNames[cleanup.mode]||'—'],['图片保留',(cleanup.ttl_hours??2)+' 小时'],['间隔 / 每日时间',(cleanup.interval_minutes??120)+' 分钟 / '+(cleanup.daily_time||'03:00')],['上次检查',clock(cleanup.last_checked)],['下次检查',cleanup.mode==='windows'?'由 Windows 任务触发':clock(cleanup.next_due)],['上次清理',(cleanup.removed??0)+' 张'],['存放位置','群运行目录/temp/ai-images']]]];
  const strip=node('div',undefined,'vitals');for(const [label,value,tone,note] of vitals){const box=node('div',undefined,'vital');box.append(node('div',label,'vital-label'),node('div',value,'vital-value'+(tone?' '+tone:'')));if(note)box.append(node('div',note,'vital-note'));strip.append(box);}
  const grid=node('div',undefined,'status-sections');for(const [title,fields] of sections){const card=node('section',undefined,'status-card');card.append(node('h3',title));for(const [label,value] of fields){const row=node('div',undefined,'status-row'),cell=node('div',undefined,'value');cell.append(value instanceof Node?value:document.createTextNode(String(value)));row.append(node('div',label,'label'),cell);card.append(row);}grid.append(card);}
  $('status').replaceChildren(strip,usageCard(),grid);}
const usageViews=new Map();
const tokenNumber=value=>Number(value||0).toLocaleString('zh-CN');
function cacheUsageView(stats){
  const box=node('div',undefined,'cache-usage'),known=stats?.cached!==null&&stats?.cached!==undefined;
  const head=node('div',undefined,'cache-usage-head');head.append(node('span','缓存命中'),node('strong',known&&stats.cache_hit_percent!==null&&stats.cache_hit_percent!==undefined?stats.cache_hit_percent+'%':'—'));box.append(head);
  if(known&&stats.cache_hit_percent!==null&&stats.cache_hit_percent!==undefined){const bar=node('progress');bar.max=100;bar.value=stats.cache_hit_percent;bar.setAttribute('aria-label','已报告输入中的缓存命中比例');box.append(bar);}
  const metrics=node('div',undefined,'cache-usage-metrics');metrics.append(node('span','命中 '+(known?tokenNumber(stats.cached):'—')+' token'),node('span','未缓存 '+(stats?.uncached===null||stats?.uncached===undefined?'—':tokenNumber(stats.uncached))+' token'));box.append(metrics);
  const calls=stats?.calls||0,reported=stats?.cache_reported_calls||0;
  box.append(node('div',calls?('缓存数据覆盖 '+reported+' / '+calls+' 次'+(reported<calls?' · 部分数据，比例仅基于已报告输入':'')+(known?'':' · 未报告')):'暂无调用数据','cache-usage-note'));return box;
}
function renderUsage(view,data){
  const area=view.output;area.replaceChildren();
  const quotaGrid=node('div',undefined,'usage-quota-grid'),windows=[...(data.windows||[])];
  for(const minutes of [300,10080])if(!windows.some(row=>row.minutes===minutes))windows.push({minutes,missing:true,label:minutes===300?'5小时':'1周'});
  windows.sort((a,b)=>a.minutes-b.minutes);
  for(const row of windows){
    const card=node('article',undefined,'quota-card'),head=node('div',undefined,'quota-head');head.append(node('strong',row.label),node('span',row.missing?'未提供':row.name||row.bucket,'quota-bucket'));card.append(head);
    const metric=node('div',undefined,'quota-metric');metric.append(node('strong',row.missing?'—':String(row.remaining_percent)+'%'),node('span',row.missing?'额度窗口未提供':'剩余额度'));card.append(metric);
    if(row.missing){card.append(node('div',row.minutes===300?'5 hour token -':'1 week token -','quota-foot'));}
    else{
      const progress=node('progress');progress.max=100;progress.value=Math.max(0,Math.min(100,Number(row.used_percent)||0));progress.setAttribute('aria-label',row.label+'已用额度');progress.className=row.remaining_percent<=10?'critical':row.remaining_percent<=30?'warning':'';
      card.append(progress);const foot=node('div',undefined,'quota-foot');foot.append(node('span','已用 '+row.used_percent+'%'),node('span',row.resets_at?'重置 '+clock(row.resets_at)+'（北京时间）':'重置时间未知'));card.append(foot);
      const stats=row.project_tokens_in_window;card.append(node('div',stats?'本项目 · 此窗口 '+tokenNumber(stats.total)+' token':'本项目 · 窗口用量未知','quota-project'));
      if(stats)card.append(cacheUsageView(stats));
    }
    quotaGrid.append(card);
  }
  area.append(quotaGrid);
  const heading=node('div',undefined,'usage-section-title');heading.append(node('h4','项目 token 用量'),node('span',(data.scope||'当前群')+' · 滚动统计'));area.append(heading);
  const statsGrid=node('div',undefined,'usage-token-grid');
  for(const [key,label] of [['rolling_5h','最近 5 小时'],['rolling_week','最近 1 周']]){
    const stats=data[key]||{},card=node('article',undefined,'token-card');card.append(node('div',label,'token-period'));
    const metric=node('div',undefined,'token-total');metric.append(node('strong',tokenNumber(stats.total)),node('span','token'));card.append(metric);
    const breakdown=node('dl',undefined,'token-breakdown');for(const [name,value] of [['输入',stats.input],['输出',stats.output],['已报告调用',stats.calls],['命中缓存调用',stats.cache_hit_calls]])breakdown.append(node('dt',name),node('dd',value===null||value===undefined?'—':tokenNumber(value)));card.append(breakdown,cacheUsageView(stats));statsGrid.append(card);
  }
  area.append(statsGrid);
  const taskHeading=node('div',undefined,'usage-section-title');taskHeading.append(node('h4','任务 token 用量'),node('span','已包含在项目总量 · 分类自更新后记录'));area.append(taskHeading);
  const taskGrid=node('div',undefined,'usage-token-grid');
  for(const [key,label] of [['task_5h','最近 5 小时'],['task_week','最近 1 周']]){
    const stats=data[key]||{},card=node('article',undefined,'token-card');card.append(node('div',label,'token-period'));
    const metric=node('div',undefined,'token-total');metric.append(node('strong',tokenNumber(stats.total)),node('span','token'));card.append(metric);
    const breakdown=node('dl',undefined,'token-breakdown');for(const [name,value] of [['输入',stats.input],['输出',stats.output],['已报告调用',stats.calls],['命中缓存调用',stats.cache_hit_calls]])breakdown.append(node('dt',name),node('dd',value===null||value===undefined?'—':tokenNumber(value)));card.append(breakdown,cacheUsageView(stats));taskGrid.append(card);
  }
  area.append(taskGrid);
  if(data.error)area.append(node('div',data.error,'usage-error'));
  view.updated.textContent=data.updated?'更新于 '+clock(data.updated)+(data.stale?' · 旧缓存':''):'尚未查询账号额度';
  const details=node('details',undefined,'usage-explanation');details.append(node('summary','统计口径与说明'),node('p',data.note||'账号额度为跨客户端共享；项目token仅统计更新启用后的实际报告用量。'));area.append(details);
}
function usageCard(scopeGroup=selected){
  const group=scopeGroup;let view=usageViews.get(group);
  if(!view){
    const box=node('section',undefined,'status-card usage-dashboard'),tools=node('div',undefined,'usage-header'),heading=node('div'),scope=node('select'),button=node('button','查询 / 刷新'),output=node('div',undefined,'usage-content'),updated=node('span','尚未查询','usage-updated');
    heading.append(node('h3','额度与用量'),node('p','账号额度与项目消耗，一目了然','usage-subtitle'));
    scope.setAttribute('aria-label','token统计范围');if(group===0)scope.append(new Option('全部私聊','0'));else scope.append(new Option('当前群',String(group)),new Option('全部群','0'));button.type='button';
    const actions=node('div',undefined,'usage-actions');actions.append(scope,button);tools.append(heading,actions);box.append(tools,updated,output);
    view={box,scope,button,output,updated,busy:false,data:null};usageViews.set(group,view);
    renderUsage(view,{windows:[],scope:'当前群',note:'点击查询，读取当前Codex登录的账号额度和本项目实测用量。未返回的额度以 — 显示。'});
    const load=async(refresh)=>{
      if(view.busy)return;view.busy=true;button.disabled=true;scope.disabled=true;button.textContent='查询中…';box.setAttribute('aria-busy','true');
      try{const result=await api((group===0?'/api/private-usage?':'/api/usage?')+new URLSearchParams({group:scope.value,refresh:refresh?'1':'0'}));view.data=result.data;renderUsage(view,result.data);}
      catch(error){const old=output.querySelector('.usage-error');old?.remove();output.append(node('div',error.message,'usage-error'));}
      finally{view.busy=false;button.disabled=false;scope.disabled=false;button.textContent='查询 / 刷新';box.setAttribute('aria-busy','false');}
    };
    button.onclick=()=>load(true);scope.onchange=()=>{view.data=null;renderUsage(view,{windows:[],scope:scope.value==='0'?'全部群':'当前群',note:'正在切换统计范围'});load(false);};
  }
  return view.box;
}
async function poll(force=false){if(closed||(polling&&!force))return;polling=true;const ticket=++pollSequence;try{const params=new URLSearchParams({kind,q:$('record-search').value});if(selected!==null)params.set('group',selected);const result=await api('/api/snapshot?'+params);if(ticket!==pollSequence)return;backendReady=result.ui_revision===2;showBackendVersion(backendReady);snapshot=result;selected=snapshot.selected;renderGroups();renderRecords();renderStatus();renderComposer();checkManualSend(selected);$('intervention-count').textContent=snapshot.status.intervention_count??0;$('connection').textContent='已连接';setDot('connection-dot',true);}catch(error){if(ticket!==pollSequence)return;if(error.message.includes('白名单')){selected=null;lastEvents='';}else{$('connection').textContent='连接断开';setDot('connection-dot',false,true);toast(error.message,true);}}finally{if(ticket===pollSequence)polling=false;}}

async function loadConfig(){const ticket=++configSequence,scope=activeScope,group=selected;const result=await api('/api/config?'+new URLSearchParams({scope,group}));if(ticket!==configSequence||scope!==activeScope||group!==selected)return;configData=result;renderConfig();applyPromptEditor();document.querySelectorAll('.scope-switch [data-scope]').forEach(button=>button.classList.toggle('active',button.dataset.scope===scope));$('scope-description').textContent=scope==='group'?'只影响群 '+group+'。留空或选“使用全局”的项目继续跟随全局默认':'所有没有单独设置的群都使用这里的值';}
function modelOptions(input,current,field){
  const cfg=configData.config;
  const isModel=['model','decision_model','large_task_model'].includes(field);
  const effortModel={large_task_reasoning_effort:cfg.large_task_model,decision_reasoning_effort:cfg.decision_model}[field];
  const selectedModel=effortModel&&effortModel!=='inherit_reply'?effortModel:cfg.model;
  const options=isModel?configData.models.map(model=>model.name):configData.models.find(model=>model.name===selectedModel)?.efforts||['low','medium','high'];
  if(field==='decision_model')options.unshift('inherit_reply');
  const levels={none:'不思考',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高',ultra:'极高'};
  for(const value of new Set([current,...options])){
    const label=isModel?(value==='inherit_reply'?'沿用回复模型':value):(levels[value]||value)+'（'+value+'）';
    const option=node('option',label);
    option.value=value;
    input.append(option);
  }
  input.value=current;
}
async function saveSetting(key,raw,{scope,group,type,isObject,draftKey,rethrow=false},button){try{let next=raw;
  const current=configData?.config;
  const activeEmbeddingUrl=current?.knowledge_embedding_provider==='ollama'?'knowledge_embedding_ollama_url':'knowledge_embedding_api_url';
  const changesEmbedding=['knowledge_embedding_provider','knowledge_embedding_model',activeEmbeddingUrl].includes(key);
  if(scope==='global'&&changesEmbedding&&current?.knowledge_embedding_model&&next!==current[key]&&
      !confirm('切换 Embedding 后，旧向量可能与新模型不兼容。系统会使用独立索引，请为新配置建立索引。继续切换？')){
    delete configDrafts[draftKey];renderConfig();return false;
  }if(scope==='group'&&(!String(next).trim()||next==='inherit')){await act({action:'config_inherit',scope,group,key},button);}else{if(isObject)next=JSON.parse(next);else if(type==='boolean')next=next==='on';else if(type==='number'){next=Number(next);if(!String(raw).trim()||!Number.isFinite(next))throw Error('请输入有效的数字');}await act({action:'config',scope,group,key,value:next},button);}if(configDrafts[draftKey]===raw)delete configDrafts[draftKey];await loadConfig();return true;}catch(error){if(rethrow)throw error;toast(error.message,true);if(configData)renderConfig();}}
function settingRow(key){const cfg=configData.config,meta=configData.fields[key],scope=activeScope,group=selected,draftKey=scope+':'+(scope==='group'?group:'*')+':'+key;const value=cfg[key],local=scope==='group',overridden=Object.hasOwn(configData.overrides,key),inherited=local&&!overridden,locked=local&&configData.global_only.includes(key),type=typeof value,isObject=type==='object',ctx={scope,group,type,isObject,draftKey};
  const box=node('div',undefined,'setting'+(inherited?' inherited':''));box.id='setting-'+key;const label=node('div'),title=node('div',meta.label,'setting-title');if(local)title.append(node('span',locked?'只能全局设置':overridden?'本群单独设置':'使用全局','scope-badge'+(overridden?' override':'')));label.append(title);if(meta.help){const help=node('div',meta.help,'setting-help');help.title='点击展开 / 收起';help.onclick=()=>help.classList.toggle('open');label.append(help);}label.append(node('div',key,'key'));box.append(label);
  if(isObject&&value!==null){
    const roles=key==='role_models';
    const edit=node('div',undefined,'edit collection-setting'),summary=node('span',roles?roleSettingsSummary(cfg):collectionSummary(value),'collection-summary'),button=node('button','编辑');button.type='button';button.disabled=locked;button.dataset.field=key;button.setAttribute('aria-label','编辑'+meta.label);
    if(roles){
      const overrides=configData.overrides,parent=local?configData.global_config:null,models=configData.models;
      if(local&&decisionDefaults.some(field=>Object.hasOwn(overrides,field))){
        box.classList.remove('inherited');const badge=title.querySelector('.scope-badge');badge.textContent='本群单独设置';badge.classList.add('override');
      }
      button.onclick=()=>openCollectionEditor({title:meta.label,value,key,note:meta.help,
        context:{config:cfg,parent,overrides,models,parentLabel:'使用全局'},
        onSave:async(changes,save)=>{
          if(!Object.keys(changes).length)return;
          await act({action:'decision_settings',scope,group,changes},save);await loadConfig();
        }});
    }else{
    button.onclick=()=>{let initial=value;try{if(Object.hasOwn(configDrafts,draftKey))initial=JSON.parse(configDrafts[draftKey]);}catch(error){toast('草稿格式无效，请重新加载设置',true);return;}
      const draft=configDrafts[draftKey];openCollectionEditor({title:meta.label,value:initial,key,note:(inherited?'当前继承全局；保存后仅为本群单独设置。':'')+(meta.help||''),onSave:async(next,save)=>{await saveSetting(key,JSON.stringify(next),{...ctx,rethrow:true},save);if(configDrafts[draftKey]===draft)delete configDrafts[draftKey];}});
    };
    }
    edit.append(summary,button);
    if(local&&overridden){const reset=node('button',roles?'角色改回全局':'改回全局','inherit-setting');reset.type='button';reset.onclick=async()=>{try{if(roles)await act({action:'decision_settings',scope,group,changes:{role_models:null}},reset);else await act({action:'config_inherit',scope:'group',group,key},reset);delete configDrafts[draftKey];await loadConfig();}catch{}};edit.append(reset);}
    box.append(edit);return box;
  }
  const edit=node('div',undefined,'edit'),save=node('button','保存','save primary');save.type='button';save.disabled=locked;let input,read=()=>input.value;
  const markDirty=()=>{configDrafts[draftKey]=read();edit.classList.add('dirty');};
  const immediate=element=>{element.onchange=async()=>{if(key==='large_task_executor'&&read()==='host'){await loadConfig();openEditor('危险：本机执行没有容器隔离',[{name:'ack',label:'输入“继续”确认',value:'',placeholder:'继续'}],async inputs=>{if(inputs.ack.value!=='继续')throw Error('请明确输入“继续”，或取消');await act({action:'task_sandbox_enable_host',scope:ctx.scope,group:ctx.group,confirmed:true},$('store-save'));await loadConfig();},'任务代码可能读取密钥、修改或删除本机文件、运行程序，权限等同当前Bot账户。临时目录不能阻止越界，到期清理也无法撤销其他位置的修改。仅用于完全可信任务。取消或关闭不会启用。');}else await saveSetting(key,read(),ctx,element);};};
  const inheritOption=text=>{const option=node('option','使用全局（'+text+'）');option.value='inherit';return option;};
  if(type==='boolean'&&!local){input=node('input');input.type='checkbox';input.checked=Boolean(value);const toggle=settingSwitch(node,input);read=()=>input.checked?'on':'off';input.onchange=()=>{toggle.update();saveSetting(key,read(),ctx,input);};edit.append(toggle.state,toggle.track);}
  else if(type==='boolean'||meta.options||['model','reasoning_effort','decision_model','decision_reasoning_effort','large_task_model','large_task_reasoning_effort'].includes(key)){input=node('select');if(type==='boolean'){if(local)input.append(inheritOption(configData.global_config[key]?'开启':'关闭'));for(const choice of ['on','off']){const option=node('option',choice==='on'?'开启':'关闭');option.value=choice;input.append(option);}input.value=inherited?'inherit':value?'on':'off';}else if(meta.options){if(local)input.append(inheritOption(meta.options[configData.global_config[key]]||configData.global_config[key]));for(const [choice,text] of Object.entries(meta.options)){const option=node('option',text);option.value=choice;input.append(option);}input.value=inherited?'inherit':value;}else{modelOptions(input,value,key);if(local){input.prepend(inheritOption(configData.global_config[key]==='inherit_reply'?'沿用回复模型':configData.global_config[key]));if(inherited)input.value='inherit';}}immediate(input);edit.append(input);}
  else if(isObject||key.startsWith('member_prompt_')||key==='member_model_block_rules'||key==='sticker_admission_rule'||key==='large_task_dockerfile'){input=node('textarea');input.spellcheck=false;input.value=inherited?'':isObject?JSON.stringify(value,null,2):value;input.placeholder=isObject?JSON.stringify(configData.global_config[key],null,2):String(configData.global_config[key]||'可留空');input.oninput=markDirty;if(key==='large_task_dockerfile')input.rows=12;edit.append(input,save);}
  else if(type==='number'&&/probability|confidence|strength/.test(key)){const wrap=node('div',undefined,'slider'),range=node('input');range.type='range';range.min='0';range.max='1';range.step='0.01';input=node('input');input.type='number';input.min='0';input.max='1';input.step='any';input.value=inherited?'':value;input.placeholder=key==='webui_key'?'留空则启动时随机生成':String(configData.global_config[key]);range.value=input.value===''?configData.global_config[key]:input.value;range.setAttribute('aria-label',meta.label);range.oninput=()=>{input.value=range.value;markDirty();};input.oninput=()=>{if(input.value!=='')range.value=input.value;markDirty();};wrap.append(range,input);edit.append(wrap,save);}
  else{input=node('input');input.type=key==='webui_key'?'password':type==='number'?'number':'text';input.value=inherited?'':value;input.placeholder=key==='webui_key'?'留空则启动时随机生成':String(configData.global_config[key]);if(type==='number'){input.min='0';const isCharacterLimit=['max_prompt_chars','console_max_text_chars','large_task_script_max_chars'].includes(key);input.step=isCharacterLimit?'1':'any';if(isCharacterLimit)input.max='2147483647';}input.oninput=markDirty;edit.append(input,save);}
  if(Object.hasOwn(configDrafts,draftKey)&&input.type!=='checkbox'&&input.tagName!=='SELECT'){input.value=configDrafts[draftKey];edit.classList.add('dirty');const range=edit.querySelector('input[type=range]');if(range&&input.value!=='')range.value=input.value;}
  if(input.tagName==='INPUT'&&input.type!=='checkbox')input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();save.click();}};
  input.disabled=locked;input.dataset.field=key;input.setAttribute('aria-label',meta.label);save.onclick=()=>saveSetting(key,read(),ctx,save);
  if(key==='nonebot_port'&&!local){
    const getURL=node('button','获取完整客户端 URL'),result=node('input');
    getURL.type='button';result.readOnly=true;result.hidden=true;result.setAttribute('aria-label','完整反向 WebSocket 客户端 URL');
    getURL.onclick=async()=>{
      result.value=nonebotClientURL(configData.config,location.hostname);result.hidden=false;
      try{await navigator.clipboard.writeText(result.value);toast('客户端 URL 已复制');}
      catch{result.focus();result.select();toast('已生成客户端 URL，可选中复制');}
    };
    edit.append(getURL,result);
  }
  if(key==='knowledge_embedding_provider'&&!local){
    const probe=node('button','测试连接'),status=node('span','','hint embedding-test-status');
    probe.type='button';probe.title='用已保存的 Embedding 配置生成测试向量';
    status.setAttribute('role','status');
    probe.onclick=async()=>{
      probe.disabled=true;status.classList.remove('danger-text');status.textContent='正在测试已保存的配置…';
      try{
        const result=await api('/api/embedding-test',{});
        status.classList.toggle('danger-text',!result.ok);status.textContent=result.message;
      }catch(error){
        status.classList.add('danger-text');status.textContent=error.message;
      }finally{probe.disabled=false;}
    };
    edit.append(probe,status);
  }
  if(key==='knowledge_embedding_api_url'&&!local){
    const hasKey=configData.has_embedding_api_key;
    const keyButton=node('button',hasKey?'更新 API Key':'设置 API Key');
    keyButton.type='button';
    keyButton.onclick=()=>editEmbeddingKey(hasKey);
    edit.append(keyButton);
  }
  if(key==='large_task_dockerfile'){const build=node('button','构建镜像');build.type='button';build.onclick=()=>{const group=selected;openEditor('构建任务镜像',[{name:'ack',label:'输入“构建”确认',value:''}],async inputs=>{if(inputs.ack.value!=='构建')throw Error('请输入“构建”，或取消');const result=await act({action:'task_sandbox_build',group,scope:ctx.scope,confirmed:true},build);toast(result.message,!result.ok);},'使用已保存的Dockerfile和镜像名称；请先保存修改。构建可联网执行Dockerfile指令，仅使用可信内容，不传入本机文件或密钥。最长等待10分钟；共享容器需在构建后重建。');};edit.append(build);}
  if(key==='large_task_container_name'){for(const [action,label] of [['task_sandbox_health','检测容器健康'],['task_sandbox_reinstall','重建容器']]){const tool=node('button',label);tool.type='button';tool.onclick=async()=>{const group=selected;if(action==='task_sandbox_reinstall'){openEditor('重建容器',[{name:'ack',label:'输入“重建”确认',value:''}],async inputs=>{if(inputs.ack.value!=='重建')throw Error('请输入“重建”，或取消');const result=await act({action,group,confirmed:true},$('store-save'));toast(result.message,!result.ok);},'共享模式会中断沙箱任务并清空共享目录；独立模式创建检测容器验证后立即删除，不影响其他任务。不删除镜像，不重放原任务。');return;}try{const result=await act({action,group},tool);toast(result.message,!result.ok);}catch(error){toast(error.message,true);}};edit.append(tool);}}
  if(key==='large_task_executor'){const probe=node('button','检测容器连接'),guide=node('a');probe.type='button';guide.hidden=true;guide.target='_blank';guide.rel='noopener noreferrer';probe.onclick=async()=>{probe.disabled=true;guide.hidden=true;try{const result=await act({action:'task_sandbox_probe',group:selected},probe);toast(result.message,!result.ready);if(result.ok&&['/docker-guide.html','/podman-guide.html'].includes(result.guide)){guide.href=result.guide;guide.textContent=result.guide.includes('podman')?'Podman 容器教程':'Docker 容器教程';guide.hidden=false;}}catch(error){toast(error.message,true);}finally{probe.disabled=false;}};edit.append(probe,guide);}
  if(local&&overridden){const reset=node('button','改回全局','inherit-setting');reset.type='button';reset.title='删除这个群的单独设置，恢复使用全局值';reset.onclick=async()=>{try{const raw=configDrafts[draftKey];await act({action:'config_inherit',scope:'group',group,key},reset);if(configDrafts[draftKey]===raw)delete configDrafts[draftKey];await loadConfig();}catch{}};edit.append(reset);}
  box.append(edit);return box;}
function cooldownRow(){const box=node('div',undefined,'setting'),label=node('div');label.append(node('div','回复冷却','setting-title'),node('div','关闭后不等待点名冷却和水群冷却，但仍受每小时/每天额度限制。只作用于当前群的运行状态。','setting-help'));const edit=node('div',undefined,'edit'),sw=node('label',undefined,'switch'),input=node('input'),on=!snapshot?.status.no_cooldown;input.type='checkbox';input.id='cooldown';input.checked=on;input.setAttribute('role','switch');input.setAttribute('aria-label','回复冷却');sw.append(input,node('span'));input.onchange=()=>act({action:'config',key:'cooldown',scope:activeScope,value:input.checked?'on':'off'},input).catch(()=>{input.checked=!input.checked;});edit.append(node('span',on?'开启':'关闭','switch-label'),sw);box.append(label,edit);return box;}
function renderConfig(){
  const term=$('config-search').value.trim().toLowerCase(),area=$('settings'),index=$('settings-index');
  const scroll=area.scrollTop,indexScroll=index.scrollTop;
  const remaining=new Set(Object.keys(configData.config).filter(key=>!decisionDefaults.includes(key)&&!['napcat_mcp_url','napcat_mcp_token'].includes(key)));
  area.replaceChildren();
  index.replaceChildren();

  function drawCategory([id,title,description,...blocks],navigation){
    const section=node('section',undefined,'setting-group');
    section.id='settings-'+id;
    section.append(node('h3',title));
    if(description)section.append(node('p',description));
    let count=0;
    for(const [subtitle,keys] of blocks){
      const visible=keys.trim().split(/\s+/).filter(key=>{
        if(key==='@gpt_speak_mcp')return [title,subtitle,'GPT Speak MCP stdio Codex'].join(' ').toLowerCase().includes(term);
        if(key!=='cooldown'&&!remaining.delete(key))return false;
        const meta=configData.fields[key];
        const labels=key==='cooldown'?'回复冷却 冷却模式':meta.label+' '+meta.help;
        const aliases=key==='role_models'?decisionDefaults.join(' '):'';
        return [title,subtitle,key,labels,aliases].join(' ').toLowerCase().includes(term);
      });
      if(!visible.length)continue;
      if(subtitle)section.append(node('h4',subtitle,'setting-subheading'));
      for(const key of visible){
        if(key==='@gpt_speak_mcp'){
          const info=configData.mcp_integration||{},card=node('div',undefined,'mcp-integration'),header=node('div',undefined,'mcp-integration-header');
          const check=node('button','检查连接');check.type='button';check.setAttribute('aria-label','检查 GPT Speak MCP 连接');check.onclick=()=>checkSpeakMCP(check).catch(error=>toast(error.message,true));
          header.append(node('span','本地 stdio 接入','mcp-transport'),check);
          card.append(header,node('p','Codex 通过 GPT Speak 读取本地记录并提交发送请求，无需配置地址或令牌。','mcp-description'));
          if(info.command||info.entrypoint){
            const details=node('details',undefined,'mcp-connection-details'),paths=node('dl',undefined,'mcp-paths');
            details.append(node('summary','连接详情'));
            for(const [label,value] of [['Python',info.command],['入口',info.entrypoint]])if(value)paths.append(node('dt',label),node('dd',value,'code'));
            details.append(paths);card.append(details);
          }
          card.append(node('p','检查会建立临时会话；Codex 中的现有连接需在 Codex 内重连。','mcp-note'));section.append(card);
        }else section.append(key==='cooldown'?cooldownRow():settingRow(key));
      }
      count+=visible.length;
    }
    if(!count)return;
    area.append(section);
    const jump=node('button');
    jump.type='button';
    jump.title=title;
    jump.append(node('span',title,'settings-index-label'),node('span',String(count),'count'));
    jump.onclick=()=>section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    navigation.append(jump);
  }

  for(const [title,...categories] of settingsGroups){
    const navigation=node('div',undefined,'settings-index-group');
    navigation.append(node('h3',title,'settings-index-heading'));
    for(const category of categories)drawCategory(category,navigation);
    if(navigation.children.length>1)index.append(navigation);
  }
  if(remaining.size)drawCategory(['other','其他','',['',[...remaining].sort().join(' ')]],index);
  if(!area.children.length)area.append(node('div','没有找到包含“'+term+'”的设置','empty'));
  area.scrollTop=scroll;
  index.scrollTop=indexScroll;
}

async function switchTab(tab){if(tab==='members'&&selected)await act({action:'member_directory_refresh',group:selected});closePrivateUI();stashPromptDraft();currentTab=tab;document.querySelectorAll('[data-context-tabs] button').forEach(item=>{const active=item.dataset.tab===tab;item.classList.toggle('active',active);item.setAttribute('aria-selected',String(active));});document.querySelectorAll('.side-nav [data-view]').forEach(item=>item.classList.toggle('active',item.dataset.view===tab));document.querySelectorAll('.tab').forEach(item=>item.classList.toggle('active',item.id==='tab-'+tab));try{if(snapshot)renderStatus();await loadActivePane();}catch(error){toast(error.message,true);}}
async function loadActivePane(){if(currentTab==='tasks')await loadTasks();else if(['settings','persona'].includes(currentTab))await loadConfig();else if(currentTab==='knowledge')await loadKnowledge();else if(currentTab==='database')await loadDatabase();else if(currentTab==='stickers')await loadStickers();else if(currentTab==='system')await loadSystem();else if(currentTab==='members')await loadMembers();}
document.querySelectorAll('[data-context-tabs] [data-tab]').forEach(button=>button.onclick=()=>switchTab(button.dataset.tab));
document.querySelectorAll('.side-nav [data-view]').forEach(button=>button.onclick=()=>switchTab(button.dataset.view));
$('open-about').onclick=()=>$('about-dialog').showModal();
$('about-close').onclick=()=>$('about-dialog').close();
document.querySelectorAll('[data-kind]').forEach(button=>button.onclick=()=>{kind=button.dataset.kind;document.querySelectorAll('[data-kind]').forEach(item=>item.classList.toggle('active',item===button));poll(true);});
$('group-search').oninput=()=>snapshot&&renderGroups();$('record-search').oninput=()=>poll(true);$('level-filter').onchange=()=>snapshot&&renderRecords();$('clear-search').onclick=()=>{$('record-search').value='';poll(true);};
$('config-search').oninput=()=>{
  if(!configData)return;
  $('settings').scrollTop=0;
  $('settings-index').scrollTop=0;
  renderConfig();
};

function draftKey(){return activeScope+':'+(activeScope==='group'?selected:'all')+':'+activePrompt;}
function stashPromptDraft(){const editor=$('persona');if(editor.dataset.draftKey)promptDrafts[editor.dataset.draftKey]=editor.value;}
function promptState(text,dirty=false){$('persona-state').textContent=text;$('persona-state').classList.toggle('dirty',dirty);$('persona-count').textContent=$('persona').value.length+' 字';}
function applyPromptEditor(){if(!configData)return;const editor=$('persona'),key=draftKey(),local=activeScope==='group';editor.value=promptDrafts[key]??(local&&!configData.prompt_overrides[activePrompt]?'':configData[activePrompt]||'');editor.placeholder=local?(configData.global_prompts[activePrompt]||'全局提示词为空'):'在这里输入提示词';editor.dataset.draftKey=key;editor.classList.toggle('inherited-prompt',local&&!editor.value);$('inherit-prompt').hidden=!local||!configData.prompt_overrides[activePrompt];$('prompt-description').textContent=promptInfo[activePrompt]+(local?' 留空表示这个群使用全局版本（灰色文字即全局内容）。':'');document.querySelectorAll('[data-prompt]').forEach(button=>button.classList.toggle('active',button.dataset.prompt===activePrompt));promptState(configData.help,Object.hasOwn(promptDrafts,key)&&promptDrafts[key]!==(configData[activePrompt]||''));}
async function changeScope(scope){stashPromptDraft();activeScope=scope;await loadConfig();}
document.querySelectorAll('.scope-switch [data-scope]').forEach(button=>button.onclick=()=>changeScope(button.dataset.scope).catch(error=>toast(error.message,true)));
$('save-persona').onclick=async()=>{const kind=activePrompt,text=$('persona').value,scope=activeScope,key=draftKey();try{await act(scope==='group'&&!text.trim()?{action:'config_inherit',scope,key:kind}:{action:kind,scope,text},$('save-persona'));if(scope==='group'&&!text.trim())delete promptDrafts[key];else promptDrafts[key]=text;await loadConfig();}catch{}};
$('inherit-prompt').onclick=async()=>{if(!confirm('删除这个群单独的提示词，改回使用全局版本？'))return;try{const key=draftKey();await act({action:'config_inherit',scope:'group',key:activePrompt},$('inherit-prompt'));delete promptDrafts[key];await loadConfig();}catch{}};
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{stashPromptDraft();activePrompt=button.dataset.prompt;applyPromptEditor();});
$('persona').oninput=()=>promptState('有未保存的修改',true);

$('white-form').onsubmit=event=>{event.preventDefault();const id=$('white-id').value.trim();if(!/^\d+$/.test(id))return toast('请输入纯数字的群号',true);act({action:'whitelist',mode:'add',group_id:id},$('white-add')).then(()=>$('white-id').value='').catch(()=>{});};
$('white-remove').onclick=()=>{if(confirm('把这个群移出白名单，并停止这个群的 Bot？'))act({action:'whitelist',mode:'remove',group_id:String(selected)},$('white-remove')).catch(()=>{});};
$('pause').onclick=()=>act({action:'pause'},$('pause')).catch(()=>{});$('restart').onclick=()=>act({action:'restart'},$('restart')).catch(()=>{});
async function checkSpeakMCP(button){
  const info=await api('/api/config');
  if(!info.mcp_integration)throw Error('请先重启 GPT Speak 控制台，加载新的 GPT Speak MCP 检查功能');
  return act({action:'reconnect',mode:'mcp'},button);
}
$('reconnect-nonebot').onclick=()=>act({action:'reconnect',mode:'nonebot'},$('reconnect-nonebot')).catch(()=>{});
$('reconnect-mcp').onclick=()=>checkSpeakMCP($('reconnect-mcp')).catch(error=>toast(error.message,true));
$('task-container-restart').onclick=()=>{
  const mode=snapshot?.status?.task_container_mode;
  if(!mode)return;
  const message=mode==='shared'?'重启共享任务容器？容器内尚未导出的临时文件会清空。':'启动一个临时容器检查环境？下次任务会照常创建独立容器。';
  if(confirm(message))act({action:'task_sandbox_restart',group:selected,confirmed:true},$('task-container-restart')).catch(()=>{});
};

function consoleLog(text){const out=$('command-output');out.textContent+=(out.textContent?'\n\n':'')+text;out.scrollTop=out.scrollHeight;}
$('open-console').onclick=()=>{$('console-dialog').showModal();$('command').focus();};$('console-close').onclick=()=>$('console-dialog').close();
$('command-form').onsubmit=event=>{event.preventDefault();const command=$('command').value.trim();if(!command)return;consoleLog('> '+command);act({action:'command',command},$('command-form').querySelector('button')).then(data=>{consoleLog(data.message||'已完成');$('command').value='';}).catch(error=>consoleLog(error.message));};
$('command').onkeydown=event=>{if(event.key!=='Tab')return;event.preventDefault();const input=$('command'),text=input.value,parts=text.replace(/^\//,'').split(/\s+/);let choices=['status','config','groups','group','whitelist','search','view','delete','help','restart','exit','quit','stop','system','say'];if(parts.length>1){const head=parts[0];choices=head.startsWith('co')?['show','set','get','local','global','inherit',...Object.keys(configData?.config||{}),...Object.keys(titles),'name','aliases','rate','effort','fast','text_limit','prompt']:head.startsWith('wh')?['add','remove','show']:head.startsWith('vi')?['all','chat','flow','system']:head.startsWith('sy')?['add','list','remove','delete','clear','--scope','group','all','--mode','context','once','timed','persistent','--minutes']:[];}const prefix=parts.at(-1);const hits=[...new Set(choices)].filter(choice=>choice.startsWith(prefix));if(hits.length===1)input.value=text.slice(0,text.length-prefix.length)+hits[0]+' ';else if(hits.length)consoleLog('可选：'+hits.join('  '));};
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&selectedEvent&&!document.querySelector('dialog[open]'))closeDetail();if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||document.querySelector('dialog[open]'))return;if(['ArrowLeft','ArrowRight'].includes(event.key)&&snapshot?.groups.length&&!['system','stickers'].includes(currentTab)){event.preventDefault();const groups=snapshot.groups.map(group=>group.id),index=groups.indexOf(selected);selectGroup(groups[(index+(event.key==='ArrowRight'?1:groups.length-1))%groups.length]);}});
$('open-intervention').onclick=()=>{if(!snapshot?.features?.includes('system_interventions'))return toast('请在 CLI 输入 /exit，再手动启动 Debug 以加载系统干预功能',true);$('intervention-dialog').showModal();$('intervention-window').textContent=snapshot.status.text_limit===0?'当前群的对话窗口不限字符，干预不会因字数到期':'当前群的对话窗口：'+snapshot.status.text_limit+' 字符；执行日志不消耗有效期';loadInterventions();};
$('intervention-close').onclick=()=>$('intervention-dialog').close();
$('intervention-lifetime').onchange=()=>{$('intervention-minutes-row').hidden=$('intervention-lifetime').value!=='timed';};
$('intervention-form').onsubmit=async event=>{event.preventDefault();const group=selected,text=$('intervention-text').value;try{await act({action:'system',mode:'add',group,text,target_scope:$('intervention-scope').value,lifetime:$('intervention-lifetime').value,minutes:Number($('intervention-minutes').value)},$('intervention-save'));$('intervention-text').value='';await loadInterventions();}catch{}};
$('intervention-clear').onclick=async()=>{try{await act({action:'system',mode:'clear',target_scope:'group'},$('intervention-clear'));await loadInterventions();}catch{}};
async function loadInterventions(){try{const group=selected,data=await api('/api/interventions?group='+group);if(group!==selected)return;const box=$('intervention-list');box.replaceChildren();for(const entry of data.entries.toReversed()){const row=node('div',undefined,'intervention-record');row.append(node('strong',interventionStates[entry.status]+' · '+interventionModes[entry.mode]),node('p',entry.text),node('small',entry.id+' · '+clock(entry.created)+(entry.mode==='context'?' · 已经过 '+entry.used_chars+' 字符':'')));if(entry.status==='active'){const clear=node('button','清除');clear.type='button';clear.onclick=async()=>{try{await act({action:'system',mode:'remove',id:entry.id,group},clear);await loadInterventions();}catch{}};row.append(clear);}row.append(interventionDelete(entry,group));box.append(row);}if(!data.entries.length)box.append(node('div','还没有系统干预','empty'));}catch(error){toast(error.message,true);}}
initPrivateUI({api,act,node,openEditor,usageCard,imageURL,restore:()=>{if(snapshot){renderGroups();renderStatus();}poll(true);}});
if(!token){$('connection').textContent='未授权';toast('请在 CLI 输入 /webui，用 CLI 显示的链接打开这个页面',true);}else{poll();setInterval(()=>poll(),1500);}

function quoteSummary(row){const q=row.quote;return q?.found?'回复 '+(q.sender||'群友')+'：'+(q.text||'[空消息]'):'回复的消息找不到了';}
function renderQuote(row){const box=$('detail-quote');box.hidden=!row.quote;box.replaceChildren();if(!row.quote)return;box.append(node('h3','回复的消息'));const q=row.quote;if(!q.found){box.append(node('div','本地记录里找不到这条原消息','hint'));}else box.append(node('div',q.sender||'群友','quote-sender'),node('div',q.text||'[空消息]','quote-body'));if(row.reply_id){box.append(node('div','原消息 ID '+row.reply_id,'code quote-source-id'));const button=node('button','在记录里找到原消息','quote-jump');button.type='button';button.onclick=()=>{$('record-search').value=row.reply_id;kind='chat';document.querySelectorAll('[data-kind]').forEach(item=>item.classList.toggle('active',item.dataset.kind==='chat'));poll(true);};box.append(button);}}

let backendReady=null;
let databaseData=null,dbOffset=0,dbSequence=0,stickerData=null,stickerOffset=0,stickerSequence=0,editorSave=null;
const imageURLs=[];
const memoryKindLabels={fact:'事实',preference:'偏好',event:'事件',episode:'话题片段',jargon:'群内用语',expression:'表达方式'};
const columnTitles={id:'编号',kind:'类型',content:'记忆 / 表达内容',subject:'关联群友 ID',keywords:'召回关键词',evidence:'来源证据',stamp:'时间戳',count:'观察次数',speaker:'群友 ID',text:'原始来源文本',learned:'已学习（0 / 1）',name:'显示名字',note_id:'关联记忆 ID',confidence:'置信度',fact_key:'事实类别',uses:'使用次数',positive:'正向反馈',negative:'负向反馈',last_used:'最后使用时间',old_id:'旧事实 ID',new_id:'新事实 ID'};

const originalURLs=new Map();
const emotionOptions=['开心','无语','疑惑','震惊','难过','赞同','安慰','打招呼','撒娇','调侃','其他'];
async function imageURL(path){const response=await fetch(path,{headers:{'X-QQ-Debug-Key':token}});if(!response.ok){let reason='原图暂时不可用';try{reason=(await response.json()).error||reason;}catch{}throw Error(reason);}return URL.createObjectURL(await response.blob());}
const messageImageRequests=new Map(),imageFetchQueue=[];let activeImageFetches=0;
function imageFetchSlot(task){return new Promise((resolve,reject)=>{imageFetchQueue.push({task,resolve,reject});pumpImageFetches();});}
function pumpImageFetches(){while(activeImageFetches<3&&imageFetchQueue.length){const {task,resolve,reject}=imageFetchQueue.shift();activeImageFetches++;Promise.resolve().then(task).then(resolve,reject).finally(()=>{activeImageFetches--;pumpImageFetches();});}}
async function messageImageURL(group,row,image,force=false){
  const key=group+':'+row.message_id+':'+image.key;if(originalURLs.has(key)&&!force)return originalURLs.get(key);
  if(force&&originalURLs.has(key)){URL.revokeObjectURL(originalURLs.get(key));originalURLs.delete(key);}
  const old=messageImageRequests.get(key);if(old?.promise)return old.promise;if(!force&&old?.error&&Date.now()-old.time<30000)throw old.error;
  const promise=imageFetchSlot(()=>imageURL('/api/message-image?'+new URLSearchParams({group,message_id:row.message_id,key:image.key})))
    .then(url=>{originalURLs.set(key,url);messageImageRequests.delete(key);while(originalURLs.size>80){const [first,value]=originalURLs.entries().next().value;originalURLs.delete(first);URL.revokeObjectURL(value);}return url;})
    .catch(error=>{messageImageRequests.set(key,{error,time:Date.now()});while(messageImageRequests.size>200){const first=messageImageRequests.keys().next().value;if(messageImageRequests.get(first).promise)break;messageImageRequests.delete(first);}throw error;});
  messageImageRequests.set(key,{promise});return promise;
}
function showOriginal(url,title){$('image-viewer-title').textContent=title;$('image-viewer-img').src=url;$('image-viewer-img').alt=title;$('image-viewer-img').classList.remove('actual-size');$('image-viewer').showModal();}
$('image-viewer-close').onclick=()=>$('image-viewer').close();
$('image-viewer-img').onclick=()=>$('image-viewer-img').classList.toggle('actual-size');
let editorSession=0,editorBusy=false,activePrefill=null;
function cancelPrefill(job){if(job)api('/api/action',{action:'sticker_prefill',mode:'cancel',group:job.group,id:job.id}).catch(()=>{});}
function resetPrefill(){editorSession++;editorBusy=false;cancelPrefill(activePrefill);activePrefill=null;$('store-ai').hidden=true;$('store-ai-status').hidden=true;$('store-ai-status').classList.remove('error');$('store-save').disabled=false;}
$('store-editor').addEventListener('close',()=>{resetPrefill();editorSave=null;});
async function encodeStickerFile(file){if(!file)throw Error('请先选择图片');if(file.size>2*1024*1024)throw Error('上传图片上限2MB');return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('读取图片失败'));reader.readAsDataURL(file);});}
function enablePrefill(inputs,options){
  const epoch=editorSession,button=$('store-ai'),status=$('store-ai-status');button.hidden=false;button.textContent='AI 识别填入';button.disabled=!snapshot?.features?.includes('sticker_ai_prefill');status.hidden=false;status.textContent=button.disabled?'请 /exit 后手动重开Debug，加载AI填入功能':'只填入建议，不保存、不入库。使用当前群识图模型和调用额度。';
  button.onclick=async()=>{
    if(editorBusy)return;const sourceToken=options.token(),before=Object.fromEntries(['label','description','emotion','use_when'].map(key=>[key,inputs[key].value]));
    const current=()=>epoch===editorSession&&$('store-editor').open;const job={id:crypto.randomUUID().replaceAll('-',''),group:options.group};activePrefill=job;editorBusy=true;button.disabled=true;button.textContent='识别中…';$('store-save').disabled=true;status.classList.remove('error');status.textContent='正在准备图片，结果只会填入表单…';
    try{
      const payload=await options.prepare();if(!current())return;if(options.token()!==sourceToken)throw Error('图片已更换，请重新点击AI识别填入');
      await api('/api/action',{action:'sticker_prefill',group:options.group,request_id:job.id,...payload});
      if(!current()){cancelPrefill(job);return;}status.textContent='AI正在识别，请稍候；完成后仍需手动保存。';
      const deadline=Date.now()+240000;
      while(current()&&Date.now()<deadline){
        const result=await api('/api/sticker-prefill?'+new URLSearchParams({group:job.group,id:job.id}));
        if(!current())return;
        if(options.token()!==sourceToken)throw Error('图片已更换，旧图片识别结果已忽略，请重新点击');
        if(result.status==='done'){
          let preserved=0;for(const key of ['label','description','emotion','use_when']){if(inputs[key].value===before[key])inputs[key].value=result.values[key];else preserved++;}
          status.textContent='已填入AI建议'+(preserved?'，保留了等待期间手动修改的字段':'')+'。请检查后点击保存；目前尚未入库。';return;
        }
        if(['failed','cancelled'].includes(result.status))throw Error(result.error||'AI识别已取消');
        await new Promise(resolve=>setTimeout(resolve,800));
      }
      if(current())throw Error('等待识别超时，请重试');
    }catch(error){cancelPrefill(job);if(current()){status.textContent=error.message;status.classList.add('error');}}
    finally{if(activePrefill===job)activePrefill=null;if(current()){editorBusy=false;button.disabled=false;button.textContent='AI 识别填入';$('store-save').disabled=false;}}
  };
}
function stickerFields(label='',description='',emotion='其他',use_when=''){return [{name:'label',label:'表情名称',value:label,maxLength:80},{name:'description',label:'图片说明（35字）',value:description,maxLength:35},{name:'emotion',label:'情绪分类',value:emotion,options:emotionOptions},{name:'use_when',label:'适用场景（30字）',value:use_when,maxLength:30}];}
function editImageSummary(row,image,group){const summary=image.summary||{},limits=image.summary_limits||{description:120,visible_text:140,uncertainty:70},labels={description:'图片摘要',visible_text:'可见文字',uncertainty:'不确定说明',possible_intent:'可能意图（推测）'};const fields=Object.entries(limits).map(([name,size])=>({name,label:labels[name]+'（'+size+'字）',value:summary[name]||'',long:true,maxLength:size}));openEditor('修改图片摘要',fields,async inputs=>{const edited=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));await act({action:'image',mode:'summary',group,message_id:row.message_id,key:image.key,values:{summary:{...edited,confidence:1},expected_revision:image.summary_revision||''}},$('store-save'));},'只修改本群这张图片的理解。同图后续复用人工摘要，不触发回复，也不会自动收录表情。可能意图只作推测参考。');}
function collectMessageImage(row,image,group){const fields=stickerFields((image.summary?.description||'群聊表情').slice(0,35),(image.summary?.description||'').slice(0,35));const inputs=openEditor('人工确认收录表情包',fields,async inputs=>{const values=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));await act({action:'image',mode:'collect',group,message_id:row.message_id,key:image.key,values},$('store-save'));},'确认后直接加入共享表情库，覆盖自动审核的拒绝结果；保留原图和GIF动画，不发送到群。');enablePrefill(inputs,{group,token:()=>group+':'+row.message_id+':'+image.key,prepare:async()=>({source:'message',message_id:row.message_id,key:image.key})});}
function renderImages(row){const area=$('detail-images');area.replaceChildren();area.hidden=!row.image_info?.length;if(area.hidden)return;area.append(node('h3','图片'));const group=selected;for(const image of row.image_info){const section=node('div',undefined,'image-state');section.append(node('div',(image.summary_available?(image.summary_origin==='operator'?'人工摘要':'已识别内容'):'还没有识别结果')+'，'+(image.sticker_state==='approved'?'已加入表情库':image.sticker_state==='rejected'?'自动审核未通过':'未加入表情库')),node('div','出现 '+image.seen_count+' 次，重复 '+image.repeat_count+' 次，最近 10 条里 '+image.recent_count+' 次，连续 '+image.consecutive_count+' 次'),node('div','识别结果还能用 '+image.remaining_messages+' / '+image.window_messages+' 条消息'));if(image.emotion)section.append(node('div','情绪：'+image.emotion+(image.use_when?'，适合 '+image.use_when:'')));if(image.asset_type)section.append(node('div','类型：'+image.asset_type));section.append(node('div',image.hash,'code'));const actions=node('div',undefined,'image-actions'),view=node('button','查看原图'),edit=node('button','修改摘要'),collect=node('button',image.sticker_state==='approved'?'已收录':'收录表情包');for(const button of [view,edit,collect]){button.type='button';button.disabled=!snapshot.features?.includes('image_management');if(button.disabled)button.title='请 /exit 后重启 Debug 后台';}collect.disabled=collect.disabled||image.sticker_state==='approved';view.onclick=async()=>{view.disabled=true;try{showOriginal(await messageImageURL(group,row,image),'消息 '+row.message_id+' · 原图');}catch(error){toast(error.message,true);}finally{view.disabled=false;}};edit.onclick=()=>editImageSummary(row,image,group);collect.onclick=()=>collectMessageImage(row,image,group);actions.append(view,edit,collect);section.prepend(actions);const local=originalURLs.get(group+':'+row.message_id+':'+image.key);if(local){const thumb=node('img',undefined,'message-image-thumb');thumb.src=local;thumb.alt='图片预览，点击查看原图';thumb.onclick=()=>showOriginal(local,'消息 '+row.message_id+' · 原图');section.prepend(thumb);}area.append(section);}}

async function loadSystem(){const data=await api('/api/system');$('system-summary').textContent=data.online_groups+' / '+data.total_groups+' 个群在线，今天共调用模型 '+data.today_model_calls+' 次，全局默认模型 '+data.global_model;$('global-status').replaceChildren();for(const group of data.groups){const panel=node('article',undefined,'overview-card'),head=node('header'),[text,tone]=groupState(group,group);head.append(avatarNode('group',group.id,group.name,'group-avatar',group.id),node('h3',group.name),node('span',text,'status-pill '+tone));const facts=node('dl');for(const [label,value] of [['群号',group.id],['人格',group.name_persona],['模型',group.model+' · '+group.effort+(group.fast?' · Fast':'')],['今日调用',group.model_calls_today+' 次'],['单独设置',group.override_count+' 项'],['最后轮询',clock(group.last_poll)],['进程',group.pid||'—']])facts.append(node('dt',label),node('dd',String(value)));panel.append(head,facts);if(group.error)panel.append(node('div',group.error,'error-note'));const open=node('button','打开这个群');open.type='button';open.onclick=()=>{selectGroup(group.id);switchTab('logs');};panel.append(open);$('global-status').append(panel);}if(!data.groups.length)$('global-status').append(node('div','还没有授权的群','empty'));}
$('system-refresh').onclick=()=>loadSystem().catch(error=>toast(error.message,true));

async function loadDatabase(){const ticket=++dbSequence,group=selected,table=$('db-table').value||'notes';const data=await api('/api/database?'+new URLSearchParams({group,table,q:$('db-search').value,offset:dbOffset}));if(ticket!==dbSequence||selected!==group)return;if(data.offset>0&&data.offset>=data.total){dbOffset=Math.max(0,Math.floor((data.total-1)/data.limit))*data.limit;return loadDatabase();}databaseData=data;updatePager('db',data);$('db-table').replaceChildren();for(const [key,label] of Object.entries(data.tables)){const option=node('option',label);option.value=key;$('db-table').append(option);}$('db-table').value=data.table;const area=$('db-records');area.replaceChildren();if(!data.rows.length)area.append(node('div',data.columns.length?($('db-search').value?'没有匹配的记录':'这张表还是空的'):'这个群还没有建立记忆数据库，聊过一段时间后会自动生成','empty'));else{const tableNode=node('table'),head=node('thead'),header=node('tr'),keys=data.columns.map(item=>item.name);for(const key of keys)header.append(node('th',columnTitles[key]||key));header.append(node('th','操作'));head.append(header);tableNode.append(head);const body=node('tbody');for(const row of data.rows){const tr=node('tr');for(const key of keys){const text=row[key]===null?'—':data.table==='notes'&&key==='kind'?(memoryKindLabels[row[key]]||String(row[key])):String(row[key]);const td=node('td',text.length>160?text.slice(0,160)+'…':text,/id|stamp|confidence|count|uses|learned/.test(key)?'code':'');td.title=text;tr.append(td);}const tools=node('td',undefined,'row-tools'),edit=node('button','编辑'),remove=node('button','删除','danger-text');edit.onclick=()=>openDatabaseEditor(row);remove.onclick=async()=>{if(!confirm('删除这条记忆以及依赖它的记录？\n删除前会自动备份，QQ 消息不受影响。'))return;try{await act({action:'database',mode:'delete',group,table:data.table,row_id:row._rowid,expected:row},remove);await loadDatabase();}catch{}};tools.append(edit,remove);tr.append(tools);body.append(tr);}tableNode.append(body);area.append(tableNode);}$('db-count').textContent='共 '+data.total+' 条 · 第 '+(Math.floor(data.offset/50)+1)+' / '+Math.max(1,Math.ceil(data.total/50))+' 页';$('db-prev').disabled=data.offset===0;$('db-next').disabled=data.offset+data.rows.length>=data.total;$('db-add').disabled=!data.columns.length;}

function openEditor(title,fields,save,note){
  resetPrefill();$('store-title').textContent=title;$('store-note').textContent=note||'保存前会自动备份';$('store-fields').replaceChildren();const inputs={};
  for(const field of fields){
    const row=node('div',undefined,'store-field'),label=node('label',field.label),id='store-'+field.name;label.htmlFor=id;
    if(field.collection){const draft=collectionDraftInput(field,node);draft.button.id=id;row.append(label,draft.el);$('store-fields').append(row);inputs[field.name]=draft.control;continue;}
    let input;if(field.options){input=node('select');for(const choice of field.options){const option=node('option',typeof choice==='object'?choice.label:choice);option.value=typeof choice==='object'?choice.value:choice;input.append(option);}}else input=node(field.long?'textarea':'input');
    input.id=id;input.name=field.name;input.value=field.value??'';input.disabled=Boolean(field.disabled);if(field.maxLength)input.maxLength=field.maxLength;if(field.number&&input.tagName==='INPUT'){input.type='number';input.step='any';}if(field.file){input.type='file';input.accept='image/png,image/jpeg,image/gif,image/webp';}if(field.placeholder)input.placeholder=field.placeholder;row.append(label,input);$('store-fields').append(row);inputs[field.name]=input;
  }
  editorSave=()=>save(inputs);$('store-editor').showModal();return inputs;
}

function openDatabaseEditor(row){if(!databaseData)return;const table=databaseData.table,group=selected;const defaults={kind:'fact',keywords:'[]',evidence:'[]',count:1,learned:0,stamp:Math.floor(Date.now()/1000),confidence:.8,uses:0,positive:0,negative:0,last_used:0};const fields=databaseData.columns.map(column=>({name:column.name,label:columnTitles[column.name]||column.name,value:row?row[column.name]:defaults[column.name]??'',collection:memoryCollection(column.name,row?row[column.name]:defaults[column.name]),disabled:Boolean(row&&column.pk),long:['content','text','keywords','evidence'].includes(column.name),number:/INT|REAL/.test(column.type),placeholder:!row&&column.name==='id'?'留空自动生成随机编号':null,options:table==='notes'&&column.name==='kind'?Object.entries(memoryKindLabels).map(([value,label])=>({value,label})):null}));openEditor(row?'编辑记录 #'+row._rowid:'新增记录',fields,async inputs=>{const values={};for(const field of fields){if(field.disabled)continue;const raw=inputs[field.name].value;if(!row&&!raw&&field.name==='id')continue;const value=field.number&&raw!==''?Number(raw):raw;if(row&&value===row[field.name])continue;values[field.name]=value;}await act({action:'database',group,table,mode:row?'update':'create',row_id:row?._rowid,expected:row||null,values},$('store-save'));await loadDatabase();});}
$('store-close').onclick=()=>{$('store-editor').close();editorSave=null;};$('store-form').onsubmit=async event=>{event.preventDefault();if(editorBusy||!editorSave)return;const save=editorSave;try{await save();$('store-editor').close();editorSave=null;}catch(error){toast(error.message,true);}};
$('db-table').onchange=()=>{dbOffset=0;loadDatabase().catch(error=>toast(error.message,true));};$('db-refresh').onclick=()=>loadDatabase().catch(error=>toast(error.message,true));$('db-search').oninput=()=>{dbOffset=0;loadDatabase().catch(error=>toast(error.message,true));};$('db-add').onclick=()=>openDatabaseEditor(null);$('db-prev').onclick=()=>{dbOffset=Math.max(0,dbOffset-50);loadDatabase();};$('db-next').onclick=()=>{dbOffset+=50;loadDatabase();};

const stickerStates={approved:'已入库',pending:'待审核',rejected:'未通过',unreviewed:'未审核',excluded:'永久排除'};
async function loadStickers(){const ticket=++stickerSequence;const data=await api('/api/stickers?'+new URLSearchParams({q:$('sticker-search').value,offset:stickerOffset,state:$('sticker-state').value,kind:$('sticker-kind').value,emotion:$('sticker-emotion').value}));if(ticket!==stickerSequence)return;if(data.offset>0&&data.offset>=data.total){stickerOffset=Math.max(0,Math.floor((data.total-1)/data.limit))*data.limit;return loadStickers();}stickerData=data;updatePager('sticker',data);$('stickers-enabled').checked=data.stickers_enabled!==false;$('stickers-enabled-state').textContent=data.stickers_enabled===false?'关闭':'开启';$('sticker-add').disabled=data.stickers_enabled===false;
const emotions=$('sticker-emotion'),emotionSignature=JSON.stringify(data.emotions||[]);
if(emotions.dataset.options!==emotionSignature){const value=emotions.value;emotions.replaceChildren(new Option('全部',''),...(data.emotions||[]).map(value=>new Option(value,value)));emotions.value=value;emotions.dataset.options=emotionSignature;}
for(const url of imageURLs)URL.revokeObjectURL(url);imageURLs.length=0;const area=$('sticker-records');area.replaceChildren();for(const row of data.rows){const card=node('article',undefined,'sticker-entry'),preview=node('div',undefined,'sticker-preview');if((row.kind==='sticker'&&row.state==='approved')||row.state==='pending'){const img=node('img');img.alt=row.title;img.loading='lazy';preview.append(img);fetch('/api/sticker-image?'+new URLSearchParams({id:row.id}),{headers:{'X-QQ-Debug-Key':token}}).then(async response=>{if(!response.ok)throw Error('图片不可用');const blob=await response.blob();if(ticket!==stickerSequence)return;const url=URL.createObjectURL(blob);imageURLs.push(url);img.src=url;}).catch(()=>{preview.replaceChildren(node('span','图片文件丢失','hint'));});}else preview.append(node('div',row.kind==='market_face'?row.title:row.state==='excluded'?'已永久排除，不会再收录':row.state==='rejected'?'审核未通过，原图没有保存':'需要重新审核','market-name'));const meta=node('div',undefined,'sticker-meta');meta.append(node('span',stickerStates[row.state]||row.state,'sticker-state '+row.state),document.createTextNode((row.kind==='market_face'?'QQ 商城表情':row.emotion||row.asset_type||'图片')+' · 用过 '+(row.uses||0)+' 次'));card.append(preview,node('div',row.title,'sticker-title'),meta);if(row.use_when)card.append(node('div','适合：'+row.use_when,'sticker-meta'));const code=node('div',row.id,'key');code.title=row.content_hash||row.id;card.append(code);const tools=node('div',undefined,'sticker-tools');if((row.kind==='sticker'&&row.state==='approved')||row.state==='pending'){const view=node('button','查看原图');view.type='button';view.onclick=async()=>{try{const url=await imageURL('/api/sticker-image?'+new URLSearchParams({id:row.id}));imageURLs.push(url);showOriginal(url,row.title);}catch(error){toast(error.message,true);}};tools.append(view);}if(row.state!=='excluded'&&['sticker','market_face'].includes(row.kind)){const edit=node('button','编辑');edit.type='button';edit.onclick=()=>openStickerEditor(row);tools.append(edit);}if(!['rejected','excluded'].includes(row.state)){const remove=node('button',row.state==='pending'?'取消收录':'删除','danger-text');remove.type='button';remove.onclick=async()=>{if(!confirm('从共享表情库删除这一项？所有授权群都会同步生效。'))return;try{await act({action:'sticker',mode:'delete',id:row.id},remove);await loadStickers();}catch{}};tools.append(remove);}if(row.state!=='excluded'){const exclude=node('button','永久排除','danger-text');exclude.type='button';exclude.onclick=async()=>{if(!confirm('永久排除这张表情？\n会删除本地表情文件，所有群都不再收录或发送同一哈希的表情，人工上传也不会重新加入。'))return;try{await act({action:'sticker',mode:'exclude',id:row.id},exclude);await loadStickers();}catch{}};tools.append(exclude);}else{
  const remove=node('button','删除排除记录','danger-text');remove.type='button';
  remove.onclick=async()=>{try{await act({action:'sticker',mode:'unexclude',id:row.id},remove);await loadStickers();}catch{}};
  tools.append(remove);
}card.append(tools);area.append(card);}if(!data.rows.length)area.append(node('div',($('sticker-search').value||$('sticker-state').value!=='all'||$('sticker-kind').value!=='all'||$('sticker-emotion').value)?'没有符合筛选条件的表情':'表情库还是空的。Bot 会自动收录群里反复出现的表情，也可以点右上角上传','empty'));$('sticker-count').textContent='共 '+data.total+' 项 · 第 '+(Math.floor(data.offset/24)+1)+' / '+Math.max(1,Math.ceil(data.total/24))+' 页';$('sticker-prev').disabled=!data.offset;$('sticker-next').disabled=data.offset+data.rows.length>=data.total;}

function openStickerEditor(row){const fields=row.kind==='market_face'?[{name:'emotion',label:'情绪分类',value:row.emotion||'其他',options:stickerData.emotions},{name:'use_when',label:'适用场景',value:row.use_when},{name:'package_name',label:'QQ 表情包名称',value:row.package_name,placeholder:'QQ 没有提供名称，可以手动补写'},{name:'emoji_name',label:'单个表情名称',value:row.emoji_name},{name:'label',label:'显示说明',value:row.label}]:[{name:'label',label:'显示名称',value:row.label},{name:'description',label:'图片说明',value:row.description},{name:'emotion',label:'情绪分类',value:row.emotion||'其他',options:stickerData.emotions},{name:'use_when',label:'适用场景',value:row.use_when}];openEditor('编辑表情',fields,async inputs=>{const values=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));await act({action:'sticker',mode:'update',id:row.id,values},$('store-save'));await loadStickers();},'只修改名称和用途，审核结果和 QQ 原始发送信息保持不变');}
$('stickers-enabled').onchange=async()=>{const input=$('stickers-enabled');try{await act({action:'config',scope:'global',key:'stickers_enabled',value:input.checked},input);await loadStickers();}catch{input.checked=stickerData?.stickers_enabled!==false;}};
$('sticker-add').onclick=()=>{if(!snapshot?.features?.includes('image_management'))return toast('请 /exit 或 /stop 后重新启动 Debug 加载人工收录',true);const group=selected;const fields=[{name:'file',label:'图片文件（≤2MB）',file:true},...stickerFields(),{name:'admission',label:'收录方式',value:'人工确认直接入库',options:['人工确认直接入库','交给自动审核']}];const inputs=openEditor('手动录入表情包',fields,async inputs=>{const file=inputs.file.files[0];if(!file)throw Error('请选择图片');if(file.size>2*1024*1024)throw Error('图片上限2MB');const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const values=Object.fromEntries(['label','description','emotion','use_when'].map(key=>[key,inputs[key].value]));values.image_base64=encoded;values.admission=inputs.admission.value==='人工确认直接入库'?'manual':'review';await act({action:'sticker',mode:'create',values},$('store-save'));await loadStickers();},'人工确认会直接加入共享表情库并保留原图；选择自动审核时，通过后才可发送。');enablePrefill(inputs,{group,token:()=>inputs.file.files[0],prepare:async()=>({source:'upload',image_base64:await encodeStickerFile(inputs.file.files[0])})});};
function updatePager(prefix,data){const input=$(prefix+'-page'),pages=Math.max(1,Math.ceil(data.total/data.limit));input.max=String(pages);if(document.activeElement!==input)input.value=String(Math.floor(data.offset/data.limit)+1);input.disabled=data.total===0;$(prefix+'-jump').disabled=data.total===0;}
function jumpPage(prefix){const data=prefix==='db'?databaseData:stickerData;if(!data)return;const input=$(prefix+'-page'),page=Number(input.value),pages=Math.max(1,Math.ceil(data.total/data.limit));if(!Number.isInteger(page)||page<1||page>pages){toast('页码请输入1～'+pages+'的整数',true);input.focus();return;}const offset=(page-1)*data.limit;if(offset===data.offset)return;if(prefix==='db'){dbOffset=offset;loadDatabase().catch(error=>toast(error.message,true));}else{stickerOffset=offset;loadStickers().catch(error=>toast(error.message,true));}}
for(const prefix of ['db','sticker']){$(prefix+'-jump').onclick=()=>jumpPage(prefix);$(prefix+'-page').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();jumpPage(prefix);}};}
$('sticker-refresh').onclick=()=>loadStickers().catch(error=>toast(error.message,true));$('sticker-search').oninput=()=>{stickerOffset=0;loadStickers().catch(error=>toast(error.message,true));};$('sticker-prev').onclick=()=>{stickerOffset=Math.max(0,stickerOffset-24);loadStickers();};$('sticker-next').onclick=()=>{stickerOffset+=24;loadStickers();};
setInterval(()=>{if(closed||document.hidden||document.querySelector('dialog[open]'))return;if(currentTab==='system')loadSystem().catch(()=>{});else if(currentTab==='stickers')loadStickers().catch(()=>{});},10000);

function showBackendVersion(ready){let banner=$('backend-version');if(!banner){banner=node('div',undefined,'backend-version');banner.id='backend-version';banner.setAttribute('role','status');document.body.append(banner);}banner.hidden=ready;if(!ready)banner.textContent='网页文件已经更新，但调试服务还是旧进程：请在 CLI 输入 /exit，然后重新打开 Start-QQ-Group-Bot-Debug。';document.querySelectorAll('.side-nav [data-view],[data-context-tabs] button').forEach(button=>button.disabled=!ready&&(button.dataset.view||button.dataset.tab)!=='logs');}

window.addEventListener('pagehide',()=>{for(const url of originalURLs.values())URL.revokeObjectURL(url);originalURLs.clear();});

for(const id of ['sticker-state','sticker-kind','sticker-emotion'])$(id).onchange=()=>{stickerOffset=0;loadStickers().catch(error=>toast(error.message,true));};

function editEmbeddingKey(hasKey){
  const fields=[{name:'embedding-key',label:'全局 Embedding API Key',value:'',maxLength:512}];
  const inputs=openEditor('Embedding API Key',fields,async fields=>{
    if(hasKey&&!confirm('更换或清除 API Key 可能改变 Embedding 服务返回的向量。兼容 API 模式下，需要为新配置建立索引。继续保存？'))throw Error('已取消更改');
    await act({action:'knowledge_vector',mode:'key',key:fields['embedding-key'].value},$('store-save'));
    fields['embedding-key'].value='';
    await loadConfig();
  },'仅 OpenAI 兼容 Embedding 服务使用。输入新密钥替换，留空保存可清除。');
  inputs['embedding-key'].type='password';
  inputs['embedding-key'].autocomplete='new-password';
}

let knowledgeOffset=0,knowledgeSequence=0,knowledgeData=null,knowledgeGroup=null,refreshKnowledgeTools;
async function loadKnowledge(){
  const group=selected;if(knowledgeGroup!==group){knowledgeOffset=0;knowledgeGroup=group;}
  const ticket=++knowledgeSequence;
  const toolbar=$('knowledge-tools');
  if(toolbar.dataset.group!==String(group)){
    toolbar.dataset.group=String(group);toolbar.replaceChildren();
    refreshKnowledgeTools=knowledgeTools(toolbar,{api,act,node,group});
  }else await refreshKnowledgeTools?.();
  if(ticket!==knowledgeSequence||selected!==group)return;
  const data=await api('/api/knowledge?'+new URLSearchParams({group,q:$('knowledge-search').value,scope:$('knowledge-scope').value,offset:knowledgeOffset}));
  if(ticket!==knowledgeSequence||selected!==group)return;
  if(data.offset>0&&data.offset>=data.total){knowledgeOffset=Math.max(0,Math.floor((data.total-1)/20)*20);return loadKnowledge();}
  knowledgeData=data;updatePager('knowledge',data);
  const area=$('knowledge-records');area.replaceChildren();
  if(!data.rows.length)area.append(node('div','暂无知识。可以新增一条，或导入 TXT、Markdown、YAML、JSON、INI 等文本文件，不限文件大小和正文字数。','empty'));
  else{
    const table=node('table'),head=node('tr');for(const title of ['知识与摘要','范围 / 状态','来源 / 字数','操作'])head.append(node('th',title));table.append(head);
    for(const row of data.rows){
      const tr=node('tr'),content=node('td'),title=node('strong',row.title),preview=node('p',row.preview,'knowledge-preview');content.append(title,preview);
      const state=node('td');state.append(node('span',row.scope==='shared'?'所有群共享':'当前群','scope-badge'),node('p',row.enabled?'已启用':'已停用'));
      const source=node('td');source.append(node('div',row.source||'手动录入'),node('p',row.chars.toLocaleString()+' 字','code'));
      const tools=node('td',undefined,'row-tools'),edit=node('button','查看 / 编辑'),remove=node('button','删除','danger-text');
      edit.onclick=async()=>{try{const full=await api('/api/knowledge?'+new URLSearchParams({group,id:row.id}));if(selected===group)openKnowledge(full);}catch(error){toast(error.message,true);}};
      remove.onclick=async()=>{if(!confirm('删除知识“'+row.title+'”？\n删除前会备份。共享知识删除后所有群都不再检索到。'))return;try{await act({action:'knowledge',mode:'delete',group,id:row.id,expected:row.version},remove);await loadKnowledge();}catch{}};
      tools.append(edit,remove);tr.append(content,state,source,tools);table.append(tr);
    }area.append(table);
  }
  $('knowledge-count').textContent='共 '+data.total+' 条 · 第 '+(Math.floor(data.offset/20)+1)+' / '+Math.max(1,Math.ceil(data.total/20))+' 页';
  $('knowledge-prev').disabled=data.offset===0;$('knowledge-next').disabled=data.offset+data.rows.length>=data.total;
}
function openKnowledge(row=null){
  const group=selected,fields=[{name:'title',label:'标题',value:row?.title||'',maxLength:150},
    {name:'scope',label:'使用范围',value:row?.scope||'group',options:[{value:'group',label:'仅当前群'},{value:'shared',label:'所有群共享'}]},
    {name:'enabled',label:'状态',value:row?.enabled===false?'off':'on',options:[{value:'on',label:'启用：允许模型检索'},{value:'off',label:'停用：保留内容，不供模型检索'}]},
    {name:'source',label:'来源 / 文件名',value:row?.source||'',maxLength:200,placeholder:'可选，例如文档名称或资料出处'},
    {name:'content',label:'知识正文',value:row?.content||'',long:true,placeholder:'粘贴资料或输入知识内容，不限字数'}];
  const inputs=openEditor(row?.id?'查看 / 编辑知识':'新增知识',fields,async inputs=>{
    const values=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));values.enabled=values.enabled==='on';
    await act({action:'knowledge',mode:'save',group,id:row?.id||'',expected:row?.version,values},$('store-save'));await loadKnowledge();
  },'保存前自动备份；按段建立本地索引，下次回复生效。共享资料对所有授权群可见，知识内容只作为参考。');
  inputs.content.classList.add('knowledge-content');
}
$('knowledge-add').onclick=()=>openKnowledge();
$('knowledge-refresh').onclick=()=>loadKnowledge().catch(error=>toast(error.message,true));
for(const id of ['knowledge-search','knowledge-scope'])$(id)[id==='knowledge-search'?'oninput':'onchange']=()=>{knowledgeOffset=0;loadKnowledge().catch(error=>toast(error.message,true));};
$('knowledge-prev').onclick=()=>{knowledgeOffset=Math.max(0,knowledgeOffset-20);loadKnowledge().catch(error=>toast(error.message,true));};
$('knowledge-next').onclick=()=>{knowledgeOffset+=20;loadKnowledge().catch(error=>toast(error.message,true));};
$('knowledge-jump').onclick=()=>{if(!knowledgeData)return;const page=Number($('knowledge-page').value),pages=Math.max(1,Math.ceil(knowledgeData.total/20));if(!Number.isInteger(page)||page<1||page>pages)return toast('页码请输入1～'+pages+'的整数',true);knowledgeOffset=(page-1)*20;loadKnowledge().catch(error=>toast(error.message,true));};
$('knowledge-page').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();$('knowledge-jump').click();}};
$('knowledge-import').onclick=()=>$('knowledge-file').click();
async function readKnowledgeText(file){
  const bytes=new Uint8Array(await file.arrayBuffer());let content;
  if(bytes[0]===0xff&&bytes[1]===0xfe)content=new TextDecoder('utf-16le',{fatal:true}).decode(bytes);
  else if(bytes[0]===0xfe&&bytes[1]===0xff)content=new TextDecoder('utf-16be',{fatal:true}).decode(bytes);
  else{
    try{content=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}
    catch{try{content=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}catch{throw Error('无法读取文本编码，请另存为 UTF-8 后导入');}}
  }
  if(!content.trim())throw Error('文件内容不能为空');
  if(content.includes('\u0000'))throw Error('仅支持文本文件，不能导入二进制内容');
  return content;
}
$('knowledge-file').onchange=async()=>{
  const file=$('knowledge-file').files[0],group=selected;$('knowledge-file').value='';if(!file)return;
  try{
    const content=await readKnowledgeText(file);
    if(selected!==group)return toast('当前群已切换，请重新导入',true);
    openKnowledge({title:(file.name.replace(/\.[^.]+$/,'')||file.name).slice(0,150),content,source:file.name.slice(0,200)});
  }catch(error){toast(error.message,true);}
};

$('session-stop').onclick=async()=>{try{await act({action:'session',mode:snapshot?.status?.session_paused?'resume':'stop',group:selected},$('session-stop'));}catch{}};
$('receive-only').onclick=()=>act({action:'receive_only',enabled:!snapshot?.status?.receive_only,group:selected},$('receive-only')).catch(()=>{});
$('group-directory-refresh').onclick=()=>act({action:'group_directory_refresh'},$('group-directory-refresh')).catch(()=>{});
const recentHistoryButton=node('button','拉取近期聊天记录');recentHistoryButton.type='button';recentHistoryButton.title='手动补入最近50条，按消息ID去重，仅作为历史背景，不触发AI回复';$('group-directory-refresh').after(recentHistoryButton);
recentHistoryButton.onclick=()=>act({action:'recent_history_fetch',group:selected},recentHistoryButton).catch(()=>{});
$('avatar-refresh').onclick=async()=>{const button=$('avatar-refresh');button.disabled=true;try{const failed=await refreshVisibleAvatars(true);toast(failed?'部分头像未能更新，已保留原头像；请稍后重试':'头像已刷新',Boolean(failed));}catch(error){toast(error.message,true);}finally{button.disabled=false;}};
$('settings-export').onclick=async()=>{
  const button=$('settings-export');button.disabled=true;
  try{
    const bundle=await api('/api/settings-bundle'),url=URL.createObjectURL(new Blob([JSON.stringify(bundle,null,2)],{type:'application/json'}));
    const link=node('a');link.href=url;link.download='qq-bot-settings-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    toast('完整设置已导出，文件包含访问密钥和连接凭据，请妥善保管');
  }catch(error){toast(error.message,true);}finally{button.disabled=false;}
};
$('settings-import').onclick=()=>$('settings-file').click();
$('settings-file').onchange=async()=>{
  const file=$('settings-file').files[0];$('settings-file').value='';if(!file)return;
  try{
    if(file.size>2800000)throw Error('设置文件最多2.8MB');
    const bundle=JSON.parse(await file.text()),data=await api('/api/action',{action:'settings_bundle',mode:'preview',bundle}),p=data.preview;
    const summary='全局变化：'+(p.global_changes.join('、')||'无')+'\n启用群聊：'+p.enabled_groups.join('、')+'\n\n'+p.groups.map(row=>'群 '+row.group+'：'+row.overrides+' 项单独设置，'+row.members+' 个群友标记').join('\n')+'\n\n将覆盖设置包范围内的配置和人设，恢复群设置的继承关系。本机主群编号与程序路径保留。';
    openEditor('导入全部设置 · 预览',[{name:'summary',label:'导入范围',value:summary,long:true,disabled:true},
      {name:'connection',label:'OneBot连接凭据',value:'off',options:[{value:'off',label:'保留本机连接地址和令牌'},{value:'on',label:'恢复设置包中的连接地址和令牌'}]}],async inputs=>{
        if(!confirm('确认应用这些设置？应用前会自动备份。启用群聊和运行设置可能随导入改变；主群、连接和WebUI设置需重启控制台生效。'))throw Error('已取消导入');
        await act({action:'settings_bundle',mode:'apply',bundle,expected:p.expected,connection:inputs.connection.value==='on'},$('store-save'));
        configData=null;for(const key of Object.keys(configDrafts))delete configDrafts[key];for(const key of Object.keys(promptDrafts))delete promptDrafts[key];await loadConfig();
      },'仅预览，点击保存并确认才导入。导入前自动备份；不导入聊天记录、记忆或表情资源。');
  }catch(error){toast(error.message,true);}
};
$('session-clear').onclick=async()=>{if(!confirm('终止当前会话并清空当前群的短期对话上下文？聊天存档、群记忆和知识库保留。'))return;try{await act({action:'session',mode:'clear',group:selected},$('session-clear'));}catch{}};

const taskNames={preparing:'准备资料',generating:'生成中',continuing:'续写中',ready:'待上传',local_ready:'可本地下载',uploading:'上传中',uploaded:'已上传',failed:'失败',cancelled:'已取消',rejected:'未执行',upload_uncertain:'上传未确认',interrupted:'已中断'};
function taskBytes(value){
  if(!Number.isFinite(value)||value<0)return '—';
  const units=['Byte','KiB','MiB','GiB','TiB'];let unit=0;
  while(value>=1024&&unit<units.length-1){value/=1024;unit++;}
  return (unit?value.toLocaleString('zh-CN',{maximumFractionDigits:2}):value.toLocaleString('zh-CN'))+' '+units[unit];
}
function taskFileSize(item,tokenText='token 未单独计量'){return (!item.binary&&Number.isFinite(item.characters)?Number(item.characters).toLocaleString()+' 字 / ':'')+taskBytes(item.bytes)+(item.binary?' · 二进制':'')+' · '+tokenText;}
function taskStepTokens(usage,shared=false){return usage?'本步 '+Number(usage.input_tokens+usage.output_tokens).toLocaleString()+' token'+(shared?'（文件共用）':''):'本步 token 未报告';}
function taskUsage(usage){return usage?'模型输入 '+Number(usage.input_tokens).toLocaleString()+' / 输出 '+Number(usage.output_tokens).toLocaleString()+' token · 缓存 '+(usage.cached_input_tokens==null?'未报告':Number(usage.cached_input_tokens).toLocaleString()):'模型 token 未报告';}
let taskBrowseScope='group',taskSelection='',taskScope=null,taskTicket=0,taskDetailSignature='';
async function loadTasks(){
  if(currentTab!=='tasks'||isPrivateOpen())return;const group=selected,ticket=++taskTicket;
  const data=await api('/api/tasks?'+new URLSearchParams({group,scope:taskBrowseScope}));if(ticket!==taskTicket||selected!==group||currentTab!=='tasks')return;
  if(taskScope!==group){taskScope=group;taskSelection='';taskDetailSignature='';}
  $('tasks-quota').textContent='今日全局已接收 '+(data.daily_used||0)+' / '+(data.daily_limit||'无限制');
  const list=$('task-list');list.replaceChildren();if(!data.rows.some(row=>row.group_id+':'+row.id===taskSelection))taskSelection=data.rows[0]?data.rows[0].group_id+':'+data.rows[0].id:'';
  for(const row of data.rows){
    const button=node('button',undefined,'task-list-item'+(row.group_id+':'+row.id===taskSelection?' active':''));button.type='button';
    const head=node('div',undefined,'task-item-head'),badge=node('span',taskNames[row.status]||row.status,'task-state');badge.dataset.status=row.status;
    head.append(node('strong',row.filename||'文件任务'),badge);
    button.append(head,node('span',(taskBrowseScope==='all'?row.group_name+' · ':'')+(row.sender||'群聊任务'),'task-item-sender'),node('small',(row.step||0)+' 步 · '+(row.sandbox||row.host_execution?row.filename?Number(row.bytes||0).toLocaleString()+' 字节':'成品待生成':(row.characters||0).toLocaleString()+' 字符')+' · '+new Date(row.created*1000).toLocaleTimeString('zh-CN',{hour12:false})));button.onclick=()=>{taskSelection=row.group_id+':'+row.id;loadTasks().catch(error=>toast(error.message,true));};list.append(button);
  }
  if(!taskSelection){list.append(node('div','暂无保存的任务','empty'));$('task-detail').replaceChildren();return;}
  const row=await api('/api/tasks?'+new URLSearchParams({group:taskSelection.split(':')[0],id:taskSelection.split(':')[1]}));if(ticket!==taskTicket||selected!==group||currentTab!=='tasks')return;
  const signature=JSON.stringify([group,row.id,row.updated]);if(signature===taskDetailSignature)return;taskDetailSignature=signature;
  renderTaskDetail(row);
}
function renderTaskDetail(row){
  const detail=$('task-detail'),scroll=detail.scrollTop,oldCode=detail.querySelector('.task-code'),referencesOpen=detail.querySelector('.task-references')?.open||false,codeScroll=oldCode?.scrollTop||0,follow=oldCode?oldCode.scrollHeight-oldCode.scrollTop-oldCode.clientHeight<40:true;
  detail.replaceChildren();
  const header=node('header',undefined,'task-run-header'),title=node('div'),badge=node('span',taskNames[row.status]||row.status,'task-state');badge.dataset.status=row.status;
  const eyebrow=node('div',undefined,'task-eyebrow');eyebrow.append(node('span','文件任务'),badge);
  title.append(eyebrow,node('h2',row.filename||(['failed','cancelled','interrupted','rejected'].includes(row.status)?'任务未完成':'正在制作文件')),node('p',(row.group_name||row.group_id)+' · '+(row.sender||'')+' · '+(row.model||'')+' · '+new Date(row.created*1000).toLocaleString('zh-CN',{hour12:false}),'hint'));
  const executing=Boolean(row.sandbox||row.host_execution),pending=['preparing','generating','continuing'].includes(row.status);
  if(row.can_cancel){const controls=node('section',undefined,'task-controls'),cancel=node('button',row.cancel_requested?'正在中断任务':'强制中断任务','danger');cancel.type='button';cancel.disabled=Boolean(row.cancel_requested);cancel.onclick=async()=>{try{const result=await act({action:'task_cancel',group:row.group_id,id:row.id},cancel);toast(result.message);taskDetailSignature='';await loadTasks();}catch{}};controls.append(cancel,node('span','仅停止此任务，保留可恢复文件','hint'));detail.append(controls);}
  if(row.can_resume||row.can_send){const controls=node('section',undefined,'task-controls');if(row.can_resume){const resume=node('button','继续任务','primary');resume.type='button';resume.onclick=async()=>{try{const result=await act({action:'task_resume',group:row.group_id,id:row.id},resume);toast(result.message);await loadTasks();}catch{}};controls.append(resume);}if(row.can_send){const send=node('button','发送到 QQ 群');send.type='button';send.disabled=['sending','sent','uncertain'].includes(row.manual_delivery?.status);send.onclick=()=>openEditor('发送任务文件',[{name:'ack',label:'输入“发送”确认',value:''}],async inputs=>{if(inputs.ack.value!=='发送')throw Error('请输入“发送”，或取消');const result=await act({action:'task_send',group:row.group_id,id:row.id,confirmed:true},send);toast(result.message);},'发送到当前任务来源群 '+row.group_id+'。'+(row.partial?'这是未完成草稿，可能无法正常使用，AI会依据真实状态说明。':'')+'发送结果不确定时不会自动重发。');controls.append(send);}if(row.resume_child){const child=node('button','查看继续任务');child.onclick=()=>{taskSelection=row.group_id+':'+row.resume_child;loadTasks().catch(error=>toast(error.message,true));};controls.append(child);}detail.append(controls);}
  if(row.partial)detail.append(node('div','未完成草稿：可下载、继续处理或手动发送，文件可能不完整','task-error'));
  const reported=(row.model_calls||[]).filter(call=>call.usage),totalTokens=reported.reduce((sum,call)=>sum+call.usage.input_tokens+call.usage.output_tokens,0);
  const metrics=node('div',undefined,'task-run-metrics');for(const [value,label] of [[row.step||0,executing?'模型调用步数':'生成批次'],[row.filename?taskBytes(row.bytes):'—',row.partial?'草稿体积':'成品体积'],[reported.length?totalTokens.toLocaleString():'—','已报告 token（输入+输出）']]){const box=node('div');box.append(node('strong',String(value)),node('span',label));metrics.append(box);}header.append(title,metrics);detail.prepend(header);
  if(row.can_resume&&!row.artifact&&!row.artifact_base64){const collect=node('button','读取保留草稿');collect.type='button';collect.onclick=async()=>{try{const result=await act({action:'task_collect',group:row.group_id,id:row.id},collect);toast(result.message);taskDetailSignature='';await loadTasks();}catch{}};detail.querySelector('.task-controls')?.append(collect);}
  if(row.task_state){const state=node('section',undefined,'task-request');state.append(node('h3','执行计划与实际进展'));if(row.task_state.phase)state.append(node('p','当前阶段：'+row.task_state.phase));if(row.task_state.next_step)state.append(node('p','下一步：'+row.task_state.next_step));if(row.task_state.evidence)state.append(node('p',row.task_state.evidence));if(row.task_state.correction)state.append(node('p',row.task_state.correction));for(const text of row.task_state.plan||[])state.append(node('p','计划：'+text));for(const text of row.task_state.completed||[])state.append(node('p','模型报告已完成：'+text));detail.append(state);}
  if(row.error)detail.append(node('div',row.error,'task-error'));
  if(row.host_execution)detail.append(node('div','危险：此任务在本机执行，无容器隔离','task-error'));
  if(row.sandbox_warning)detail.append(node('div',row.sandbox_warning,'task-error'));
  if(row.files?.length){const manifest=node('section',undefined,'task-request');manifest.append(node('h3',row.partial?'草稿文件':'交付文件'));for(const item of row.files){const line=node('div',undefined,'task-file-metric');line.append(node('strong',item.name),node('span',taskFileSize(item,reported.length?'本任务已报告 '+totalTokens.toLocaleString()+' token'+(row.files.length>1?'（文件共用）':''):'本任务 token 未报告')));manifest.append(line);}detail.append(manifest);}
  const request=node('section',undefined,'task-request');request.append(node('h3','本次需求'),node('p',row.request||'正在准备任务资料'));
  const refs=[];if(row.person_references?.length)refs.push(['人物参考资料',JSON.stringify(row.person_references,null,2)]);
  for(const file of row.source_files||[])refs.push([file.name+(file.truncated?'（片段）':''),file.content||'']);
  if(refs.length){const references=node('details',undefined,'task-references');references.open=referencesOpen;references.append(node('summary','参考资料 · '+refs.length+' 项'));for(const [name,value] of refs)references.append(node('h4',name),node('pre',value));request.append(references);}detail.append(request);
  if(row.input_files?.length){const attachments=node('section',undefined,'task-request task-inputs');attachments.append(node('h3','输入附件 · '+row.input_files.length+' 个'));for(const item of row.input_files){const file=node('div',undefined,'task-input-file'),info=node('div');info.append(node('strong',item.name),node('span',taskFileSize(item)));file.append(info,node('span',item.status==='staged'?'已送入执行环境':'已保存输入副本','task-input-state'));attachments.append(file);}detail.append(attachments);}
  const output=node('section',undefined,'task-output'),bar=node('div',undefined,'task-output-bar'),file=node('div',undefined,'task-output-name');file.append(node('span','生成文件','task-section-label'),node('strong',row.filename||'文件名待生成'));bar.append(file);
  if(row.artifact||row.artifact_base64){const actions=node('div',undefined,'task-output-actions'),copy=node('button','复制内容'),download=node('button',row.partial?'下载草稿':'下载文件');copy.type=download.type='button';copy.onclick=()=>navigator.clipboard.writeText(row.artifact).then(()=>toast('已复制')).catch(()=>toast('复制失败',true));download.onclick=()=>{const bytes=row.artifact_base64?Uint8Array.from(atob(row.artifact_base64),c=>c.charCodeAt(0)):row.artifact;const url=URL.createObjectURL(new Blob([bytes],{type:row.binary?'application/octet-stream':'text/plain;charset=utf-8'})),a=node('a');a.href=url;a.download=row.filename||'draft.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};if(!row.binary)actions.append(copy);actions.append(download);bar.append(actions);}output.append(bar);
  if(row.stage){const stage=node('div',undefined,'task-stage');stage.setAttribute('role','status');stage.append(node('span','当前阶段','task-section-label'),node('strong',row.stage));output.append(stage);}
  if(row.execution?.length){const log=node('details',undefined,'task-execution');log.open=detail.dataset.executionOpen==='true';log.ontoggle=()=>{detail.dataset.executionOpen=String(log.open);};log.append(node('summary','执行记录 · '+row.execution.length+' 次脚本执行'));for(const item of row.execution){const step=node('div',undefined,'task-execution-step');step.append(node('strong','第 '+item.step+' 步 · '+(item.exit_code===0?'脚本执行成功':'返回错误（'+item.exit_code+'）')));if(item.summary)step.append(node('p',item.summary,'task-step-summary'));step.append(node('p',taskUsage(item.usage),'task-step-stats'));if(item.files?.length){const files=node('div',undefined,'task-step-files');for(const file of item.files){const line=node('div',undefined,'task-file-metric');line.append(node('strong',file.name),node('span',taskFileSize(file,taskStepTokens(item.usage,item.files.length>1))));files.append(line);}step.append(files);}else if(Array.isArray(item.files))step.append(node('p','本步交付目录为空','task-step-stats'));const text=Array.from(item.output||'');step.append(node('p','执行日志：'+(item.original_output_chars??text.length).toLocaleString()+' 字'+(Number.isFinite(item.original_output_bytes)?' / '+taskBytes(item.original_output_bytes):'')+((item.truncated||text.length>500)?' · 仅显示前 500 字':''),'task-step-stats'));if(text.length)step.append(node('pre',text.slice(0,500).join('')+(text.length>500?'…':'')));if(item.completion_issue)step.append(node('p',item.completion_issue,'task-step-stats'));log.append(step);}output.append(log);}
  if(row.binary)output.append(node('div','这是二进制文件，不显示文本内容；可下载原文件。','task-output-empty'));
  if(row.artifact){const characters=Array.from(row.artifact),preview=characters.slice(0,500).join('')+(characters.length>500?'…':'');const code=node('pre',preview,'task-code');code.setAttribute('aria-label','生成文件内容');output.append(code);}else if(!row.binary){const empty=node('div',undefined,'task-output-empty'),pending=['preparing','generating','continuing'].includes(row.status);empty.append(node('span',pending?'':'—',pending?'task-spinner':'task-empty-icon'),node('strong',pending?'正在处理任务':'暂无生成内容'),node('span',pending?'沙箱处理或模型返回后自动更新，可继续浏览其他页面':'请查看上方的任务状态'));output.append(empty);}
  const reasons={model_reports_unfinished:'模型仍在续写',complete:'文件完整',empty_content:'返回内容为空',missing_completion_flag:'缺少完成标记',content_too_long:'本批内容超过上限',content_not_text:'未返回文本',incomplete_html_document:'HTML文档尚未结束',unclosed_html_structure:'HTML结构未闭合'};
  const foot=node('div',undefined,'task-output-foot');foot.append(node('span',row.validation?'第 '+row.validation.step+' 批 · '+(reasons[row.validation.issue]||row.validation.issue):row.sandbox?'沙箱实际生成的文件':row.host_execution?'本机实际生成的文件（危险模式）':'模型返回后保存到文件'),node('span','消息 '+row.message_id));output.append(foot);detail.append(output);detail.scrollTop=scroll;
  const code=detail.querySelector('.task-code');if(code)code.scrollTop=follow?code.scrollHeight:codeScroll;
}

$('tasks-refresh').onclick=()=>loadTasks().catch(error=>toast(error.message,true));
setInterval(()=>{if(currentTab==='tasks'&&!isPrivateOpen())loadTasks().catch(()=>{});},3000);

document.querySelectorAll('[data-task-scope]').forEach(button=>button.onclick=()=>{taskBrowseScope=button.dataset.taskScope;taskSelection='';taskDetailSignature='';document.querySelectorAll('[data-task-scope]').forEach(item=>item.classList.toggle('active',item===button));loadTasks().catch(error=>toast(error.message,true));});
