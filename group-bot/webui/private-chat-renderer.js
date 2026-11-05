const avatars=new Map(),records=new Map(),imageModes=new Map();
const blobs=new Map();
function forgetImage(url){const old=blobs.get(url);blobs.delete(url);old?.then(value=>URL.revokeObjectURL(value)).catch(()=>{});}
function authenticatedImage(url){if(!blobs.has(url)){if(blobs.size>=400)forgetImage(blobs.keys().next().value);blobs.set(url,helpers.imageURL(url).catch(error=>{blobs.delete(url);throw error;}));}return blobs.get(url);}
function loadImage(img,url){img.dataset.privateSource=url;authenticatedImage(url).then(value=>img.src=value).catch(()=>img.onerror?.());}
export async function refreshPrivateAvatarImages(){const images=[...document.querySelectorAll('img[data-private-source*="private-avatar"]')];for(const url of new Set(images.map(img=>img.dataset.privateSource)))forgetImage(url);for(const img of images)loadImage(img,img.dataset.privateSource);}
window.addEventListener('pagehide',()=>{for(const value of blobs.values())value.then(url=>URL.revokeObjectURL(url)).catch(()=>{});blobs.clear();});
let helpers,selectedEvent='',peerNow='';
export function initPrivateRenderer(options){helpers=options;}
const n=(...args)=>helpers.node(...args);
function reconcile(area,nodes){const wanted=new Set(nodes);for(const child of [...area.children])if(!wanted.has(child))child.remove();nodes.forEach((el,i)=>{if(area.children[i]!==el)area.insertBefore(el,area.children[i]||null);});}
export function privateAvatar(peer,id,name,enabled=true){
  const key=peer+':'+id+':'+enabled;let el=avatars.get(key);
  if(!el){el=n('span',undefined,'private-avatar');const fallback=n('span',String(name||'?').trim().charAt(0));el.append(fallback);
    if(enabled&&id){const img=n('img');img.alt='';img.loading='lazy';img.onload=()=>{img.hidden=false;fallback.hidden=true;};img.onerror=()=>img.hidden=true;loadImage(img,'/api/private-avatar?'+new URLSearchParams({peer,id}));el.append(img);}avatars.set(key,el);}
  avatars.delete(key);return el;
}
export function showPrivateImage(url){const dialog=n('dialog',undefined,'private-image-viewer'),close=n('button','关闭','icon-button'),img=n('img');loadImage(img,url);img.alt='私聊图片原图';close.type='button';close.onclick=()=>dialog.close();dialog.append(close,img);dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();}
export function renderPrivateContacts(area,data,peer,term,onSelect){
  const existing=new Map([...area.children].map(el=>[el.dataset.peer,el])),nodes=[];
  for(const row of data.contacts){if(!(row.name+' '+row.id).toLowerCase().includes(term))continue;
    let button=existing.get(row.id);if(!button){button=n('button',undefined,'group');button.type='button';button.dataset.peer=row.id;button.onclick=()=>onSelect(row.id);}
    const signature=JSON.stringify([row.name,data.settings_defaults?.show_avatars]);
    if(button.dataset.signature!==signature){button.dataset.signature=signature;const avatar=privateAvatar(row.id,row.id,row.name,data.settings_defaults?.show_avatars!==false);avatar.classList.add('group-avatar');const text=n('span');text.append(n('span',row.name,'group-name'),n('span',row.last_text,'group-sub'));button.replaceChildren(avatar,text);}
    button.classList.toggle('active',peer===row.id);button.setAttribute('aria-current',String(peer===row.id));button.title=row.name+' · '+row.id;nodes.push(button);
    button.querySelector('.group-sub').textContent=row.unavailable_reason||row.last_text;
  }
  reconcile(area,nodes);if(!nodes.length)area.append(n('div',term?'没有匹配的联系人':'暂无授权群好友，点击“刷新好友列表”重新获取','empty'));
}
function gallery(event){const box=n('div',undefined,'private-gallery');for(const image of event.media?.images||[]){const img=n('img');img.loading='lazy';img.alt='私聊图片';img.onclick=e=>{e.stopPropagation();showPrivateImage(image.url);};img.onerror=()=>{img.hidden=true;const retry=n('button','图片加载失败，重试','link-button');retry.type='button';retry.onclick=e=>{e.stopPropagation();retry.remove();img.hidden=false;blobs.delete(image.url);loadImage(img,image.url);};box.append(retry);};loadImage(img,image.url);box.append(img);}return box;}
export function renderPrivateRecords(area,data,peer,events,onReply){
  if(peerNow!==peer){peerNow=peer;selectedEvent='';records.clear();}
  const bottom=area.scrollHeight-area.scrollTop-area.clientHeight<70,nodes=[];
  const details=document.getElementById('private-record-detail');
  function select(event,element){selectedEvent=String(event.id);area.querySelectorAll('.selected').forEach(el=>el.classList.remove('selected'));element.classList.add('selected');renderPrivateDetail(details,data,peer,event,onReply);}
  for(const event of events){const key=peer+':'+event.id,signature=JSON.stringify([event,data.profile?.blocked,data.contact_settings?.chat_image_display,data.contact_settings?.show_avatars]);let entry=records.get(key);
    if(!entry||entry.signature!==signature){
      const stamp=new Date(event.stamp*1000).toLocaleTimeString('zh-CN',{hour12:false}),own=event.kind==='sent';let el;
      if(['received','sent'].includes(event.kind)){
        el=n('article',undefined,'private-message '+(own?'own':'incoming'));const body=n('div',undefined,'private-message-body'),avatar=n('button',undefined,'private-avatar-button');avatar.type='button';avatar.title=own?'当前机器人':'查看联系人资料与标记';avatar.append(privateAvatar(peer,own?data.self_id:peer,own?data.bot_name:data.name,data.contact_settings?.show_avatars!==false));avatar.onclick=e=>{e.stopPropagation();if(!own)helpers.member();};
        body.append(n('div',(own?data.bot_name:data.name||peer)+'  '+stamp+(!own&&data.profile?.blocked?'  屏蔽':''),'private-message-meta'));
        const bubble=n('div',undefined,'private-bubble'),text=n('div',event.text,'private-message-text');
        if(event.media?.reply_id){const quote=(data.events||[]).find(row=>String(row.details?.message_id)===event.media.reply_id);bubble.append(n('blockquote','回复 '+(quote?.text||'消息 '+event.media.reply_id),'quote'));}
        bubble.append(text);const hasImages=!!event.media?.images?.length;
        if(hasImages){const images=gallery(event),mode=imageModes.get(key)||data.contact_settings?.chat_image_display||'image';images.hidden=mode!=='image';text.hidden=mode==='image'&&/^\s*\[图片\]\s*$/.test(event.text);bubble.append(images);const toggle=n('button',mode==='image'?'显示文字':'显示图片','link-button');toggle.type='button';const flip=()=>{const image=images.hidden;images.hidden=!image;text.hidden=image&&/^\s*\[图片\]\s*$/.test(event.text);toggle.textContent=image?'显示文字':'显示图片';imageModes.set(key,image?'image':'text');};toggle.onclick=e=>{e.stopPropagation();flip();};bubble.ondblclick=e=>{if(e.target.tagName!=='IMG')flip();};body.append(bubble,toggle);}else body.append(bubble);
        for(const file of event.media?.files||[])bubble.append(n('div','📄 '+file.name+' · '+file.size.toLocaleString()+' 字节','private-file-card'));
        const actions=n('div',undefined,'private-message-actions'),reply=n('button','回复','link-button');reply.type='button';reply.onclick=e=>{e.stopPropagation();onReply(event);};actions.append(reply);if(event.media?.manual)actions.append(n('span','手动发送','hint'));body.append(actions);el.append(avatar,body);
      }else{el=n('article',undefined,event.kind==='system'?'private-system-record':'private-flow');el.append(n('span',stamp,'flow-time'),n('span',({system:'系统提示词',stage:'执行阶段',decision:'决策',trigger:'触发'})[event.kind]||event.kind,'kind-chip'),n('span',event.text,'flow-text'));el.dataset.level=event.level;}
      el.tabIndex=0;el.onclick=()=>select(event,el);el.onkeydown=e=>{if(e.key==='Enter')select(event,el);};el.classList.toggle('selected',selectedEvent===String(event.id));entry={el,signature};records.set(key,entry);
    }
    nodes.push(entry.el);if(selectedEvent===String(event.id))renderPrivateDetail(details,data,peer,event,onReply);
  }
  reconcile(area,nodes);if(!nodes.length)area.append(n('div',peer?'暂无匹配记录':'选择左侧联系人查看私信','empty'));
  if(!selectedEvent&&details)details.replaceChildren(n('div','点一条消息查看完整内容、图片、引用和消息ID','empty'));
  if(document.getElementById('private-follow').checked&&(bottom||area.dataset.peer!==peer))area.scrollTop=area.scrollHeight;area.dataset.peer=peer;
}
function renderPrivateDetail(area,data,peer,event,onReply){
  if(!area)return;const signature=JSON.stringify([peer,event]);if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  area.append(n('h3',event.kind==='sent'?'已发送消息':event.kind==='received'?'收到消息':'执行记录'),n('p',event.text,'private-detail-text'));
  if(event.media?.images?.length)area.append(gallery(event));
  const list=n('dl',undefined,'private-detail-fields');for(const [label,value] of [['时间',new Date(event.stamp*1000).toLocaleString('zh-CN',{hour12:false})],['发送者',event.user_id||'—'],['消息 ID',event.details?.message_id||'—'],['引用 ID',event.media?.reply_id||'—'],['日志级别',event.level||'INFO']])list.append(n('dt',label),n('dd',value));area.append(list);
  const tools=n('div',undefined,'row-tools');if(['sent','received'].includes(event.kind)){const reply=n('button','引用回复');reply.type='button';reply.onclick=()=>onReply(event);tools.append(reply);}const raw=n('details'),summary=n('summary','原始数据');raw.append(summary,n('pre',JSON.stringify(event,null,2)));area.append(tools,raw);
}
