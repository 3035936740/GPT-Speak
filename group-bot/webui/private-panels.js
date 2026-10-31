import {initPrivateSettings,renderPrivateSettings,openCollectionEditor,collectionSummary} from '/private-settings.js';
const $=id=>document.getElementById(id);
let helpers=null,current=null,active='logs';
const drafts=new Map();
const names={status:'运行状态',settings:'行为设置',persona:'人设与规则',memory:'私聊记忆',knowledge:'知识库',member:'联系人标记'};
function n(tag,text,cls){return helpers.node(tag,text,cls);}
function panel(name){return $('private-'+name+'-pane');}
function title(area,name,note){area.append(n('h2',name));if(note)area.append(n('p',note,'private-pane-note'));}

function form(area,peer,section,fields,values,note){
  const key=peer+':'+section,stored=drafts.get(key)||values,box=n('form',undefined,'private-editor'),inputs={};
  if(note)box.append(n('p',note,'hint'));
  for(const field of fields){
    const label=n('label',undefined,'private-field');label.append(n('span',field.label));
    if(field.type==='array'){
      const control={value:[...(stored[field.key]||[])]},edit=n('div',undefined,'collection-setting member-aliases-control'),summary=n('span',collectionSummary(control.value),'collection-summary'),button=n('button','编辑');button.type='button';button.dataset.field=field.key;button.setAttribute('aria-label','编辑'+field.label);
      button.onclick=()=>openCollectionEditor({title:field.label,value:control.value,itemType:'name_list',note:'用＋添加、−删除；最多10个昵称，每个最多40字。先保存昵称草稿，再保存联系人资料后生效。',onSave:async value=>{if(!box.isConnected)throw Error('联系人资料已更新，请关闭后重新编辑');control.value=[...value];summary.textContent=collectionSummary(control.value);remember();status.textContent='额外昵称已更新，保存联系人资料后生效';}});
      edit.append(summary,button);label.append(edit);box.append(label);inputs[field.key]=control;continue;
    }
    const input=n(field.options?'select':field.type==='textarea'?'textarea':'input');input.setAttribute('aria-label',field.label);input.dataset.field=field.key;
    if(field.options){for(const [value,text] of Object.entries(field.options)){const option=n('option',text);option.value=value;input.append(option);}}
    if(field.type==='checkbox'){input.type='checkbox';input.checked=!!stored[field.key];label.classList.add('private-check-field');}
    else{if(!field.options&&field.type!=='textarea')input.type=field.type||'text';input.value=stored[field.key]??'';if(field.placeholder)input.placeholder=field.placeholder;
      if(field.maxLength)input.maxLength=field.maxLength;if(field.min!==undefined)input.min=field.min;if(field.max!==undefined)input.max=field.max;if(field.type==='textarea')input.rows=field.rows||5;}
    label.append(input);box.append(label);inputs[field.key]=input;
  }
  const read=()=>Object.fromEntries(fields.map(f=>[f.key,f.type==='checkbox'?inputs[f.key].checked:f.type==='number'?Number(inputs[f.key].value):inputs[f.key].value]));
  const remember=()=>drafts.set(key,{...stored,...read()});
  box.oninput=remember;box.onchange=remember;
  const footer=n('div',undefined,'private-editor-footer'),status=n('span','','hint'),save=n('button','保存','primary');save.type='submit';footer.append(status,save);box.append(footer);
  box.onsubmit=async event=>{event.preventDefault();let values=read();
    if(section==='knowledge'){values.id=stored.id||'';}
    try{if(section==='member'&&!current?.data.scoped_marks)throw Error('请手动重启控制台后再保存标记范围');await helpers.act({action:'private_profile',peer,section,values},save);drafts.delete(key);area.dataset.signature='';await helpers.refresh();}
    catch(error){status.textContent=error.message;}
  };
  area.append(box);return {inputs,box};
}

function status(data,peer){
  const area=panel('status');area.replaceChildren();title(area,'运行状态','当前好友的私聊状态，与群聊后台分开。');
  const entries=[['私信服务',data.status.online?'运行中':'未连接'],['当前好友自动回复',data.enabled&&data.contact_settings.reply_enabled&&data.can_chat?'开启':'关闭'],
    ['回复模型',(data.runtime.model||'—')+' · '+(data.runtime.reasoning_effort||'')],['私聊消息',String(data.runtime.stored_messages||0)+' 条'],
    ['回复长度上限',String(data.contact_settings.max_reply_chars||0)+' 字'],['同一好友冷却',String(data.contact_settings.reply_cooldown_seconds??0)+' 秒'],
    ['群友印象',data.contact_settings.use_group_impressions?'参考本人群聊印象':'不参考'],['共享私聊额度',String(data.runtime.max_replies_per_hour||0)+' 条 / 小时；'+String(data.runtime.max_model_calls_per_day||0)+' 次模型调用 / 日']];
  entries.push(['私聊识图',data.contact_settings.vision_enabled?'开启':'关闭']);
  entries.push(['名单状态',data.can_chat?'允许接入':data.access_reason||'未获准']);
  const grid=n('div',undefined,'private-status-grid');for(const [label,value] of entries){const card=n('article',undefined,'private-status-card');card.append(n('span',label,'hint'),n('strong',value));grid.append(card);}area.append(grid);
  if(data.status.error||data.status.recent_errors?.[peer])area.append(n('p','最近异常：'+(data.status.recent_errors?.[peer]||data.status.error),'private-panel-error'));
  const states=data.runtime.delivery_states||{},labels={sent:'已发送',failed:'处理失败',uncertain:'发送结果未确认',silent:'未回复',cancelled:'已取消',claimed:'处理中',sending:'发送中'};
  const summary=n('div',undefined,'private-history-summary');summary.append(n('h3','自动回复记录'));for(const [key,count] of Object.entries(states))summary.append(n('p',(labels[key]||key)+'：'+count));if(!Object.keys(states).length)summary.append(n('p','收到新私信后会记录回复状态。','hint'));area.append(summary);
}

function persona(data,peer){
  const area=panel('persona'),signature=JSON.stringify([peer,data.profile.persona,data.profile.protocol,data.system_instruction]);if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  title(area,'人设与规则','当前好友专用。留空继承全局人设或默认私聊规则，保存不会向QQ发消息。');
  form(area,peer,'prompts',[{key:'persona',label:'私聊人设',type:'textarea',rows:8,maxLength:6000,placeholder:data.default_persona},
    {key:'protocol',label:'私聊回复规则',type:'textarea',rows:5,maxLength:6000,placeholder:'留空使用默认私聊规则：一对一自然接话、只引用本人印象、会话相互独立。'}],
    {persona:data.profile.persona,protocol:data.profile.protocol});
  if(data.system_instruction){const note=n('div',undefined,'private-system-record');note.append(n('h3','当前系统提示词'),n('p',data.system_instruction));area.append(note);}
}

function memory(data,peer){
  const area=panel('memory'),signature=JSON.stringify([peer,data.messages,data.impression.observations]);if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  title(area,'私聊记忆','只读取当前好友的私信。近期'+data.contact_settings.context_messages+'条用于回复上下文（可在行为设置关闭参考）；本地最多保留100条、30天。');
  area.append(n('h3','从群聊形成的本人印象'));
  for(const observation of data.impression.observations||[]){const card=n('article',undefined,'impression-card');card.append(n('p','观察到 '+observation.message_count+' 条本人发言','hint'));
    for(const interest of observation.interests||[])card.append(n('p',interest.content));if(observation.speech_style)card.append(n('p','常用句子约 '+observation.speech_style.median_message_length+' 字'));area.append(card);}
  area.append(n('h3','独立私聊记录'));
  if(!data.messages.length)area.append(n('p','还没有私信记忆，好友发来新消息后逐渐积累。','hint'));
  for(const message of data.messages){const row=n('article',undefined,'private-memory-record');row.append(n('div',(message.role==='assistant'?data.bot_name:data.name)+' · '+new Date(message.stamp*1000).toLocaleString('zh-CN',{hour12:false}),'hint'),n('p',message.text));area.append(row);}
}

function knowledge(data,peer){
  const area=panel('knowledge'),signature=JSON.stringify([peer,data.knowledge]);if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  title(area,'私聊知识库','仅供当前好友的私聊回复检索；不会作为其他好友或群聊的知识。');
  form(area,peer,'knowledge',[{key:'title',label:'知识标题',maxLength:80},{key:'content',label:'知识正文',type:'textarea',rows:5,maxLength:4000}],{title:'',content:''});
  const list=n('div',undefined,'private-knowledge-list');
  for(const entry of data.knowledge){const card=n('article',undefined,'private-knowledge-card'),tools=n('div',undefined,'row-tools'),edit=n('button','编辑'),remove=n('button','删除','danger-text');edit.type=remove.type='button';
    edit.onclick=()=>{drafts.set(peer+':knowledge',entry);area.dataset.signature='';knowledge(data,peer);};
    remove.onclick=async()=>{if(!confirm('删除这条私聊知识？删除前会保留备份。'))return;try{await helpers.act({action:'private_profile',peer,section:'knowledge',values:{id:entry.id,delete:true}},remove);area.dataset.signature='';await helpers.refresh();}catch(error){remove.textContent=error.message;}};
    tools.append(edit,remove);card.append(n('h3',entry.title),n('p',entry.content),tools);list.append(card);}
  if(!data.knowledge.length)list.append(n('p','还没有专属知识，可以在上面添加。','hint'));area.append(list);
}

function member(data,peer){
  const area=panel('member'),signature=JSON.stringify([peer,data.profile.note,data.profile.aliases,data.profile.blocked,data.profile.account_type,data.profile.mark_scope,data.scoped_marks]);if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  title(area,'联系人标记','按当前账号选择全局或仅当前私聊，保存后生效；新屏蔽默认选择全局。');
  const editor=form(area,peer,'member',[{key:'mark_scope',label:'标记范围',options:{global:'全局（所有群聊与私聊）',private:'仅当前私聊'}},{key:'aliases',label:'额外昵称',type:'array'},{key:'account_type',label:'账号类型',options:{'':'自动判断',human:'真人',AI:'AI',bot:'bot',owner:'bot主'}},{key:'note',label:'账号说明',type:'textarea',rows:4,maxLength:200},
    {key:'blocked',label:'屏蔽此账号，范围跟随上面的选择',type:'checkbox'}],{mark_scope:data.profile.mark_scope||'global',account_type:data.profile.account_type||'',aliases:[...(data.profile.aliases||[])],note:data.profile.note,blocked:data.profile.blocked});
  let scopeEdited=false;editor.inputs.mark_scope.addEventListener('change',()=>{scopeEdited=true;});editor.inputs.blocked.addEventListener('change',()=>{if(editor.inputs.blocked.checked&&!scopeEdited){editor.inputs.mark_scope.value='global';editor.box.dispatchEvent(new Event('input',{bubbles:true}));}});
}

export function renderPrivatePanels(data,peer){
  current={data,peer};
  for(const name of Object.keys(names)){
    if(!peer&&name!=='settings'){const area=panel(name);area.dataset.signature='';area.replaceChildren(n('div','选择左侧私信联系人后查看'+names[name],'empty'));}
  }
  renderPrivateSettings(data,peer);
  if(peer&&data.profile&&data.contact_settings){status(data,peer);persona(data,peer);memory(data,peer);knowledge(data,peer);member(data,peer);}
}

export function initPrivatePanels(options){
  helpers=options;
  initPrivateSettings(options);
  document.querySelectorAll('[data-private-tab]').forEach(button=>button.onclick=()=>{
    active=button.dataset.privateTab;document.querySelectorAll('[data-private-tab]').forEach(item=>{item.classList.toggle('active',item===button);item.setAttribute('aria-selected',String(item===button));});
    document.querySelectorAll('[data-private-pane]').forEach(item=>item.hidden=item.dataset.privatePane!==active);
    if(current)renderPrivatePanels(current.data,current.peer);
  });
}
