// Shared collection editor for global, group and private configuration.
export const decisionDefaults = ['decision_model','decision_reasoning_effort','decision_fast_mode'];
const roleNames = {context:'上下文',emotion:'情绪',vision:'视觉'};
const effortOptions = {none:'不思考',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高',ultra:'极高'};

export function roleSettingsSummary(config){
  const roles = Object.keys(config.role_models || {});
  const model = config.decision_model === 'inherit_reply' ? '沿用回复模型' : config.decision_model;
  return [model, config.decision_fast_mode ? '决策 Fast 开启' : '决策 Fast 关闭', `${roles.length} 个角色单独设置`].join(' · ');
}

export function collectionSummary(value){
  const items=Array.isArray(value)?value:Object.keys(value||{});
  if(!items.length)return '未添加项目';
  const preview=items.slice(0,3).map(item=>typeof item==='object'?'一项配置':String(item)).join('、');
  return items.length+' 项 · '+preview+(items.length>3?'…':'');
}

export function memoryCollection(name,raw){
  if(name==='keywords')return {array:true,itemTemplate:''};
  if(name==='evidence')return {array:true,itemTemplate:{id:'',speaker:'',text:''}};
  if(['content','text','traits'].includes(name)){
    try{const value=typeof raw==='string'?JSON.parse(raw):raw;if(value!==null&&typeof value==='object')return {array:Array.isArray(value)};}catch{}
  }
  return null;
}

export function collectionDraftInput(field,make){
  const el=make('div',undefined,'collection-setting memory-collection-control'),summary=make('span',undefined,'collection-summary'),button=make('button','编辑'),error=make('span','','collection-error');
  const control={value:field.value??''};button.type='button';button.disabled=!!field.disabled;button.setAttribute('aria-label','编辑'+field.label);
  const parse=()=>{const raw=control.value;const value=typeof raw==='string'?JSON.parse(raw.trim()||'[]'):raw;if(value===null||typeof value!=='object'||field.collection.array&&!Array.isArray(value))throw Error('此字段应为'+(field.collection.array?'数组':'数组或对象'));return value;};
  const draw=()=>{try{summary.textContent=collectionSummary(parse());}catch{summary.textContent='原数据格式有误';}};draw();
  button.onclick=()=>{try{error.textContent='';openCollectionEditor({title:field.label,value:parse(),itemTemplate:field.collection.itemTemplate,note:'用＋添加、−删除。先保存此字段的草稿，再保存记忆记录后生效；取消不修改原记录。',onSave:async next=>{if(!el.isConnected||!el.closest('dialog')?.open)throw Error('记忆编辑窗口已关闭，请重新打开');if(field.collection.array&&!Array.isArray(next))throw Error('此字段须为数组');control.value=JSON.stringify(next);draw();}});}catch(reason){error.textContent=reason.message;}};
  el.append(summary,button,error);return {el,control,button};
}

export function openCollectionEditor({title,value,key='',itemType,itemTemplate,context={},note='',onSave}){
  const make=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const dialog=make('dialog',undefined,'collection-editor'),form=make('form'),head=make('div',undefined,'dialog-head');
  dialog.setAttribute('aria-label',title);
  const heading=make('h2',title),close=make('button','×','icon-button');close.type='button';close.setAttribute('aria-label','关闭编辑窗口');head.append(heading,close);
  const body=make('div',undefined,'collection-body'),description=make('p',note||'逐项编辑，保存后生效；关闭窗口不会应用更改。','page-note');
  body.append(description);
  let pending=false;
  const roleMap=key==='role_models',qqList=itemType==='qq_list'||/^private_user_(white|black)list$/.test(key),numericList=key==='chat_group_whitelist',nameList=itemType==='name_list';
  const labels={...roleNames,model:'模型',reasoning_effort:'思考等级',fast_mode:'Fast'};
  const maxItems=numericList?30:key==='member_auto_bot_command_prefixes'?32:qqList?1000:nameList?10:roleMap?3:key==='owner_command_aliases'?100:Infinity;
  const modelNames = [...(context.models ?? []).map(model=>model.name),
    context.config?.model, context.config?.decision_model, context.parent?.decision_model];
  const modelChoices = Object.fromEntries(modelNames.filter(name=>name && name!=='inherit_reply').map(name=>[name,name]));
  function selectField(label,initial,choices){
    const input=make('select');input.setAttribute('aria-label',label);
    const options=Object.entries(choices);
    if(!Object.hasOwn(choices,initial))options.unshift([initial,initial]);
    for(const [value,text] of options){const option=make('option',text);option.value=value;input.append(option);}
    input.value=initial;
    return input;
  }
  function scalar(current,path,{kind,optional=false}={}){
    const input=make('input');input.setAttribute('aria-label',path);
    const boolean=typeof current==='boolean',number=(kind==='integer'||typeof current==='number')&&!(qqList&&kind==='root-item');
    input.type=boolean?'checkbox':number?'number':'text';
    if(boolean)input.checked=current;else input.value=current??'';
    if(number)input.step=kind==='integer'?'1':'any';
    if(qqList&&kind==='root-item'){input.inputMode='numeric';input.maxLength=15;input.placeholder='QQ号';}
    else if(numericList&&kind==='integer'){input.min='1';input.placeholder='群号';}
    else input.placeholder=key==='owner_command_aliases'?'例如 session clear':'输入内容';
    if(key==='bot_aliases')input.maxLength=80;
    if(nameList){input.maxLength=40;input.placeholder='昵称';}
    if(key==='owner_command_aliases')input.maxLength=300;
    if(key==='member_auto_bot_command_prefixes')input.maxLength=12;
    return {el:input,focus:input,read(){
      if(boolean)return input.checked;
      if(number){if(!input.value.trim()||!Number.isFinite(Number(input.value))||(kind==='integer'&&!Number.isSafeInteger(Number(input.value))))throw Error(path+'：请输入有效数字');return Number(input.value);}
      const text=input.value;
      if(kind==='root-item'&&!text.trim())throw Error('请填写项目内容，或点击“−”删除空行');
      if(qqList&&kind==='root-item'&&!/^[1-9]\d{4,14}$/.test(text.trim()))throw Error('QQ号须为5～15位正整数');
      if(current===null&&text==='')return null;
      return optional||kind==='root-item'?text.trim():text;
    }};
  }
  function roleValue(current,path){
    const area=make('div',undefined,'collection-role-fields'),fields=[];
    const standard=['model','reasoning_effort','fast_mode'];
    for(const name of [...standard,...Object.keys(current).filter(name=>!standard.includes(name))]){
      const label=make('label',labels[name]||name),initial=current[name]??'';
      let control;
      if(name==='model'){
        const input=selectField(path+' · 模型',initial,{'':'沿用决策模型',...modelChoices});
        control={el:input,read:()=>input.value};
      }else if(name==='reasoning_effort'){
        const input=selectField(path+' · 思考等级',initial,{'':'沿用决策设置',...effortOptions});
        control={el:input,read:()=>input.value};
      }else if(name==='fast_mode'){
        const input=selectField(path+' · Fast',typeof initial==='boolean'?String(initial):'',{'':'沿用决策设置',true:'开启',false:'关闭'});
        control={el:input,read:()=>input.value===''?undefined:input.value==='true'};
      }else control=initial!==null&&typeof initial==='object'?collection(initial,path+' · '+name,2):scalar(initial,path+' · '+(labels[name]||name),{optional:true});
      label.append(control.el);area.append(label);fields.push([name,control]);
    }
    return {el:area,fields,read(){const result=Object.create(null);for(const [name,control] of fields){const next=control.read();if(!standard.includes(name)||(next!==''&&next!==undefined))result[name]=next;}return result;}};
  }
  function collection(current,path,depth=0){
    if(depth>6)throw Error('配置嵌套过深，请先缩减到6层以内');
    const list=Array.isArray(current),area=make('div',undefined,'collection-fields'),rows=make('div',undefined,'collection-rows'),empty=make('p','还没有项目，点击“＋ 添加”开始。','collection-empty');
    const add=make('button','＋ 添加','collection-add');add.type='button';
    const entries=[];
    const sync=()=>{empty.hidden=entries.length>0;add.disabled=pending||(depth===0&&entries.length>=maxItems);entries.forEach((entry,index)=>{if(entry.index)entry.index.textContent=String(index+1);entry.remove.setAttribute('aria-label','删除第 '+(index+1)+' 项');});};
    function append(name,initial,focus=false){
      const isRole=!list&&roleMap&&depth===0&&initial!==null&&typeof initial==='object'&&!Array.isArray(initial);
      const row=make('div',undefined,'collection-row '+(isRole?'collection-role-row':list?'collection-list-row':'collection-map-row')),remove=make('button','−','collection-remove');remove.type='button';
      let nameInput,index;
      if(list){index=make('span',String(entries.length+1),'collection-index');row.append(index);}
      else{
        nameInput=make(isRole?'select':'input');
        if(isRole){for(const role of new Set(['','context','emotion','vision',...(name?[name]:[])])){const option=make('option',role?(labels[role]||role):'选择角色');option.value=role;nameInput.append(option);}}
        nameInput.value=name;nameInput.placeholder=key==='owner_command_aliases'?'命令别名':'名称';if(key==='owner_command_aliases'&&depth===0)nameInput.maxLength=40;nameInput.setAttribute('aria-label',isRole?'角色':path+' · 名称');row.append(nameInput);if(!isRole)row.append(make('span','：','collection-colon'));
      }
      const nested=initial!==null&&typeof initial==='object';
      const control=isRole?roleValue(initial,path+' · '+(labels[name]||name||'角色')):nested?collection(initial,path+' · '+(name||entries.length+1),depth+1):scalar(initial,path+' · 第 '+(entries.length+1)+' 项',{kind:list&&depth===0?(numericList?'integer':'root-item'):undefined});
      if(isRole){
        row.append(...control.fields.slice(0,3).map(([,field])=>field.el),remove);
        if(control.fields.length>3){const extra=make('details',undefined,'collection-role-extra');extra.append(make('summary','其他字段'));for(const [name,field] of control.fields.slice(3)){const label=make('label',labels[name]||name);label.append(field.el);extra.append(label);}row.append(extra);}
      }else{const valueBox=make('div',undefined,'collection-value');valueBox.append(control.el);row.append(valueBox,remove);}
      rows.append(row);
      const entry={row,remove,index,nameInput,control};entries.push(entry);
      remove.onclick=()=>{if(pending)return;const index=entries.indexOf(entry);entries.splice(index,1);row.remove();sync();(entries[index]?.nameInput||entries[index]?.control.focus||add).focus();};
      sync();if(focus)(nameInput||control.focus||add).focus();
    }
    const initialEntries=list?current.map(value=>['',value]):Object.entries(current);
    for(const [name,initial] of initialEntries)append(name,initial);
    add.onclick=()=>{if(!pending){const role=!list&&roleMap&&depth===0;const name=role?['context','emotion','vision'].find(name=>!entries.some(entry=>entry.nameInput.value===name))||'':'';const initial=role?{model:'',reasoning_effort:''}:list&&depth===0&&itemTemplate!==undefined?JSON.parse(JSON.stringify(itemTemplate)):'';append(name,initial,true);}};
    if(roleMap&&depth===0){const header=make('div',undefined,'collection-role-heading');header.append(make('span','角色'),make('span','模型'),make('span','思考等级'),make('span','Fast'),make('span',''));area.append(header);}
    area.append(rows,empty,add);sync();
    return {el:area,fillRoles(defaults){
      for(const name of Object.keys(roleNames)){
        let entry=entries.find(entry=>entry.nameInput.value===name);
        if(!entry&&entries.length<maxItems){append(name,{});entry=entries.at(-1);}
        if(!entry)continue;
        for(const [field,control] of entry.control.fields.slice(0,3))control.el.value=String(defaults[field]);
      }
    },read(){
      if(list)return entries.map(entry=>entry.control.read());
      const result=Object.create(null);
      for(const entry of entries){const name=entry.nameInput.value.trim();if(!name)throw Error('请填写名称，或点击“−”删除空行');if(Object.hasOwn(result,name))throw Error('名称重复：'+name);if(roleMap&&depth===0&&!['context','emotion','vision'].includes(name))throw Error('角色须为 context、emotion 或 vision');if(key==='owner_command_aliases'&&depth===0&&!/^[^\s/]{1,40}$/.test(name))throw Error('命令别名须为1～40字，不含空白或/');result[name]=entry.control.read();if(key==='owner_command_aliases'&&depth===0&&(typeof result[name]!=='string'||!result[name].trim()))throw Error('请填写“'+name+'”对应的CLI命令');}
      return result;
    }};
  }
  const editor=collection(value,title);
  let readChanges=()=>editor.read();
  if(roleMap&&context.config){
    const {config,parent,overrides={},parentLabel='使用上层设置'}=context;
    const quick=make('section',undefined,'role-quick-fill');
    quick.append(make('h3','快速填充'),make('p','这里的值用于通用决策，也供没有单独指定的角色继承。点击下方按钮可将当前值填入三个角色。'));
    const fields=make('div',undefined,'role-quick-fields');
    const fastOptions={true:'开启',false:'关闭'};
    const controls={};
    const inherited='__inherit__';
    for(const [key,label,choices] of [
      ['decision_model','决策模型',{inherit_reply:'沿用回复模型',...modelChoices}],
      ['decision_reasoning_effort','决策思考等级',effortOptions],
      ['decision_fast_mode','决策 Fast',fastOptions],
    ]){
      const effective=String(config[key]);
      const initial=parent&&!Object.hasOwn(overrides,key)?inherited:effective;
      const options={...choices,[effective]:choices[effective]||effective};
      if(parent)options[inherited]=`${parentLabel}（${choices[parent[key]]||parent[key]}）`;
      const input=selectField(label,initial,options);
      controls[key]={input,initial,read:()=>input.value};
      const field=make('label',label);field.append(input);fields.append(field);
    }
    const effective=key=>{const value=controls[key].read();return value===inherited?String(parent[key]):value;};
    const updateEffort=()=>{
      const followsReply=effective('decision_model')==='inherit_reply';
      const input=controls.decision_reasoning_effort.input;
      input.disabled=followsReply;
      input.title=followsReply?`当前沿用回复思考等级（${effortOptions[config.reasoning_effort]||config.reasoning_effort}）`:'';
    };
    controls.decision_model.input.addEventListener('input',updateEffort);updateEffort();
    const fill=make('button','填入全部角色');fill.type='button';
    fill.onclick=()=>{
      const model=effective('decision_model');
      const followsReply=model==='inherit_reply';
      editor.fillRoles({model:followsReply?config.model:model,
        reasoning_effort:followsReply?config.reasoning_effort:effective('decision_reasoning_effort'),
        fast_mode:effective('decision_fast_mode')});
    };
    quick.append(fields,fill);body.append(quick);
    let originalRoles;
    try{originalRoles=JSON.stringify(editor.read());}catch{}
    readChanges=()=>{
      const changes={};
      for(const [key,{input,initial,read}] of Object.entries(controls)){
        const next=read();
        if(!input.disabled&&next!==initial)changes[key]=next===inherited?null:key==='decision_fast_mode'?next==='true':next;
      }
      const roles=editor.read();
      if(JSON.stringify(roles)!==originalRoles)changes.role_models=roles;
      return changes;
    };
  }
  body.append(editor.el);
  const error=make('p','', 'collection-error');error.setAttribute('role','alert');body.append(error);
  const foot=make('div',undefined,'dialog-foot'),hint=make('span','关闭或取消不保存'),actions=make('div',undefined,'store-actions'),cancel=make('button','取消'),save=make('button','保存','primary');cancel.type='button';save.type='submit';actions.append(cancel,save);foot.append(hint,actions);
  form.append(head,body,foot);dialog.append(form);document.body.append(dialog);
  close.onclick=cancel.onclick=()=>{if(!pending)dialog.close();};
  dialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  form.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.tagName==='INPUT')event.preventDefault();});
  form.onsubmit=async event=>{event.preventDefault();if(pending)return;
    try{
      const next=readChanges();pending=true;error.textContent='';
      const controls=[...form.querySelectorAll('input,select,textarea,button')],disabled=controls.map(input=>input.disabled);for(const input of controls)input.disabled=true;
      save.textContent='保存中…';
      try{const accepted=await onSave(next,save);if(accepted!==false)dialog.close();}
      finally{controls.forEach((input,index)=>input.disabled=disabled[index]);save.textContent='保存';pending=false;}
    }catch(reason){pending=false;error.textContent=reason.message||'保存失败，请检查输入';}
  };
  dialog.showModal();return dialog;
}

let helpers,scope='contact';
const drafts=new Map();
const n=(tag,text,cls)=>helpers.node(tag,text,cls);

export function initPrivateSettings(options){helpers=options;}

export function settingSwitch(create,input){
  const track=create('label',undefined,'switch');
  const state=create('span',undefined,'switch-label');
  const update=()=>{state.textContent=input.checked?'开启':'关闭';};
  input.setAttribute('role','switch');
  track.append(input,create('span'));
  update();
  return {track,state,update};
}

export function renderPrivateSettings(data,peer){
  const area=document.getElementById('private-settings-pane');
  if(!data.settings_fields)return;
  if(!peer)scope='default';
  const local=scope==='contact',values=local?data.contact_settings:data.settings_defaults,
    overrides=local?(data.profile.settings||{}):data.settings_default_overrides,
    parent=local?data.settings_defaults:data.settings_base;
  const signature=JSON.stringify([peer,scope,values,overrides,parent]);if(area.dataset.signature===signature)return;
  const scroll=area.querySelector('.private-settings-list')?.scrollTop||0,term=area.querySelector('[type=search]')?.value||'';
  area.dataset.signature=signature;area.replaceChildren();
  const header=n('div',undefined,'private-settings-header'),switcher=n('div',undefined,'scope-switch');
  for(const [value,label] of [['default','私聊默认'],['contact','只改此好友']]){
    const button=n('button',label,scope===value?'active':'');button.type='button';button.disabled=value==='contact'&&!peer;button.dataset.privateScope=value;
    button.onclick=()=>{scope=value;area.dataset.signature='';renderPrivateSettings(data,peer);};switcher.append(button);
  }
  const search=n('input');search.type='search';search.placeholder='搜索私聊设置';search.setAttribute('aria-label','搜索私聊设置');search.value=term;
  header.append(switcher,n('p',local?'只影响当前好友，未单独设置的项目继承私聊默认。':'所有未单独设置的好友使用这里的值；保存不修改群聊设置。','private-settings-description'),search);
  const layout=n('div',undefined,'settings-layout private-settings-layout'),index=n('nav',undefined,'settings-index'),list=n('div',undefined,'settings private-settings-list');index.setAttribute('aria-label','私聊设置分类');
  layout.append(index,list);area.append(header,layout);

  function row(key,meta){
    const roles=key==='role_models';
    const overridden=Object.hasOwn(overrides,key)||(roles&&decisionDefaults.some(field=>Object.hasOwn(overrides,field)));
    const locked=local&&meta.shared,identity=scope+':'+(local?peer:'*')+':'+key;
    const box=n('div',undefined,'setting'+(!overridden?' inherited':'')),label=n('div'),heading=n('div',meta.label,'setting-title');
    heading.append(n('span',locked?'全部私聊共享':overridden?'单独设置':local?'继承私聊默认':'使用原默认','scope-badge'+(overridden?' override':'')));
    label.append(heading,n('p',meta.help,'setting-help'));
    if(['qq_list','name_list','mapping'].includes(meta.type)){
      const edit=n('div',undefined,'edit collection-setting'),summary=n('span',roles?roleSettingsSummary(values):collectionSummary(values[key]||[]),'collection-summary'),button=n('button','编辑');button.type='button';button.disabled=locked;button.dataset.privateSetting=key;button.setAttribute('aria-label','编辑'+meta.label);
      const targetScope=local?'contact':'default';
      const saveRoles=async(changes,button)=>{
        if(!Object.keys(changes).length)return;
        await helpers.act({action:'private_decision_settings',peer,scope:targetScope,changes},button);
        area.dataset.signature='';await helpers.refresh();
      };
      button.onclick=async()=>{
        let context={};
        if(roles){
          button.disabled=true;
          try{
            const {models}=await helpers.api('/api/models');
            if(!button.isConnected||area.closest('[hidden]'))return;
            context={config:values,parent,overrides,models,parentLabel:local?'继承私聊默认':'使用原默认'};
          }catch(error){summary.textContent=error.message;return;}
          finally{button.disabled=locked;}
        }
        openCollectionEditor({title:meta.label,value:values[key]||[],key:meta.runtime,itemType:meta.type,note:meta.help,context,
          onSave:roles?saveRoles:async(value,save)=>{await helpers.act({action:'private_setting',peer,scope:targetScope,key,value,inherit:false},save);drafts.delete(identity);area.dataset.signature='';await helpers.refresh();}});
      };
      edit.append(summary,button);
      if(Object.hasOwn(overrides,key)&&!locked){const reset=n('button',(roles?'角色':'')+(local?'恢复私聊默认':'恢复原默认'));reset.type='button';reset.onclick=async()=>{try{if(roles)await saveRoles({role_models:null},reset);else{await helpers.act({action:'private_setting',peer,scope:targetScope,key,inherit:true},reset);drafts.delete(identity);area.dataset.signature='';await helpers.refresh();}}catch(error){summary.textContent=error.message;}};edit.append(reset);}
      if(locked){const go=n('button','到私聊默认修改');go.type='button';go.onclick=()=>{scope='default';area.dataset.signature='';renderPrivateSettings(data,peer);};edit.append(go);}
      box.append(label,edit);return box;
    }
    const edit=n('div',undefined,'edit'),input=n(meta.options?'select':['qq_list','name_list','textarea'].includes(meta.type)?'textarea':'input'),save=n('button','保存','primary');save.type='button';
    let toggle;
    input.dataset.privateSetting=key;input.setAttribute('aria-label',meta.label);
    let read;
    if(meta.type==='boolean'){input.type='checkbox';input.checked=drafts.has(identity)?drafts.get(identity):!!values[key];read=()=>input.checked;toggle=settingSwitch(n,input);}
    else if(meta.options){const choices=Array.isArray(meta.options)?Object.fromEntries(meta.options.map(value=>[value,value])):meta.options;for(const value of new Set([values[key],...Object.keys(choices)])){const option=n('option',choices[value]||value);option.value=value;input.append(option);}input.value=drafts.get(identity)??values[key];read=()=>input.value;}
    else if(['qq_list','name_list','textarea'].includes(meta.type)){const listType=['qq_list','name_list'].includes(meta.type);input.rows=4;input.maxLength=meta.type==='qq_list'?17000:meta.type==='name_list'?420:1000;input.value=drafts.get(identity)??(listType?(values[key]||[]).join('\n'):values[key]);input.placeholder=meta.type==='qq_list'?'每行一个QQ号；逗号分隔也可以':meta.type==='name_list'?'每行一个称呼；逗号分隔也可以':'';read=()=>input.value;}
    else{input.type=['integer','number'].includes(meta.type)?'number':'text';input.value=drafts.get(identity)??values[key];input.placeholder=String(parent[key]??'');if(meta.min!==null)input.min=meta.min;if(meta.max!==null)input.max=meta.max;input.step=meta.type==='integer'?'1':'any';read=()=>input.type==='number'?Number(input.value):input.value;}
    input.disabled=locked;save.disabled=locked;
    const status=n('span','','private-setting-result');
    async function persist(inherit=false){
      try{
        if(!inherit&&input.type==='number'&&(!input.value.trim()||!input.checkValidity()))throw Error('请输入范围内的有效数字');
        const value=['qq_list','name_list'].includes(meta.type)?read().split(meta.type==='qq_list'?/[\s,，、;；]+/:/[,，、;；\n]+/).map(value=>value.trim()).filter(Boolean):read();
        await helpers.act({action:'private_setting',peer,scope,key,value,inherit},toggle?input:save);drafts.delete(identity);area.dataset.signature='';await helpers.refresh();
      }catch(error){status.textContent=error.message;if(toggle){drafts.delete(identity);input.checked=!!values[key];toggle.update();}}
    }
    input.oninput=()=>drafts.set(identity,read());input.onchange=()=>{drafts.set(identity,read());toggle?.update();if(toggle||meta.options)persist();};
    if(toggle)edit.append(toggle.state,toggle.track);
    else if(!meta.options){save.onclick=()=>persist();input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();persist();}};edit.append(input,save);}else edit.append(input);
    if(overridden&&!locked){const reset=n('button',local?'恢复私聊默认':'恢复原默认');reset.type='button';reset.onclick=()=>persist(true);edit.append(reset);}
    if(locked){const go=n('button','到私聊默认修改');go.type='button';go.onclick=()=>{scope='default';area.dataset.signature='';renderPrivateSettings(data,peer);};edit.append(go);}
    edit.append(status);box.append(label,edit);return box;
  }
  function draw(){
    const query=search.value.trim().toLowerCase();index.replaceChildren();list.replaceChildren();
    for(const [category,title] of data.settings_categories){
      const fields=Object.entries(data.settings_fields).filter(([key,meta])=>meta.category===category&&!decisionDefaults.includes(key)&&(!query||(key+' '+meta.label+' '+meta.help+(key==='role_models'?' '+decisionDefaults.join(' '):'')).toLowerCase().includes(query)));
      if(!fields.length)continue;
      const section=n('section',undefined,'setting-group');section.append(n('h3',title));for(const [key,meta] of fields)section.append(row(key,meta));list.append(section);
      const button=n('button');button.type='button';button.append(n('span',title,'settings-index-label'),n('span',String(fields.length),'count'));
      button.onclick=()=>{section.scrollIntoView({block:'start',behavior:'auto'});};index.append(button);
    }
    if(!list.children.length)list.append(n('div','没有匹配的私聊设置','empty'));
  }
  search.oninput=draw;draw();list.scrollTop=scroll;
}
