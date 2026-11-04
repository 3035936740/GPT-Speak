export function knowledgeTools(container,{api,act,node,group,peer=''}){
  const scope=peer?{peer}:{group};
  const bar=node('div',undefined,'page-bar knowledge-tools');
  const mode=node('select'),summary=node('span','','hint');
  mode.setAttribute('aria-label','知识库检索模式');
  mode.append(new Option('关键词索引','keyword'),new Option('向量 RAG','vector'),new Option('关键词＋向量 RAG','hybrid'));
  const build=node('button','建立／更新索引'),preview=node('button','测试检索');
  for(const button of [build,preview])button.type='button';
  bar.append(mode,summary,node('span',undefined,'spacer'),build,preview);
  container.append(bar);

  let revision=0,timer;
  async function refresh(){
    clearTimeout(timer);
    const ticket=++revision;
    try{
      const data=await api('/api/knowledge-index?'+new URLSearchParams(scope));
      if(ticket!==revision||!bar.isConnected)return;
      mode.value=data.mode;mode.disabled=false;
      build.hidden=data.mode==='keyword';build.disabled=data.state==='indexing';
      if(data.mode==='keyword')summary.textContent='按关键词和精确条目匹配';
      else{
        const progress=data.indexed.toLocaleString()+' / '+data.total.toLocaleString()+' 个片段';
        const state=data.state==='error'?'索引失败，请检查 Embedding 设置或模型是否已下载':
          data.state==='indexing'?'正在建立索引':data.indexed&&data.indexed===data.total?'索引就绪':'待建立索引';
        summary.textContent=state+' · '+progress+' · '+data.model;
        if(data.other_index&&data.indexed<data.total)summary.textContent+=' · 发现其他 Embedding 配置的索引，请为当前配置建立索引';
      }
      if(data.state==='indexing'&&data.mode!=='keyword'&&!bar.closest('[hidden]'))timer=setTimeout(refresh,2000);
    }catch(error){if(ticket===revision)summary.textContent=error.message;}
  }
  mode.disabled=true;build.hidden=true;
  mode.onchange=async()=>{
    const value=mode.value;
    mode.disabled=true;
    const action=peer?{action:'private_setting',peer,scope:'contact',key:'knowledge_retrieval_mode',value}:
      {action:'config',group,scope:'group',key:'knowledge_retrieval_mode',value};
    try{await act(action,mode);}catch(error){summary.textContent=error.message;}
    await refresh();
  };
  build.onclick=async()=>{
    try{await act({action:'knowledge_vector',mode:'build',...scope},build);await refresh();}
    catch(error){summary.textContent=error.message;}
  };
  preview.onclick=()=>{
    const dialog=node('dialog',undefined,'collection-editor'),form=node('form');
    dialog.setAttribute('aria-label','知识库检索预览');
    const head=node('div',undefined,'dialog-head'),close=node('button','关闭');close.type='button';
    head.append(node('h2','测试检索'),close);
    const body=node('div',undefined,'collection-body'),row=node('div',undefined,'page-bar');
    const input=node('input'),submit=node('button','检索','primary'),results=node('div');
    input.required=true;input.maxLength=1000;input.placeholder='输入一个问题';input.setAttribute('aria-label','测试问题');
    submit.type='submit';row.append(input,submit);body.append(row,results);form.append(head,body);dialog.append(form);
    close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    form.onsubmit=async event=>{
      event.preventDefault();if(submit.disabled)return;
      submit.disabled=true;results.textContent='检索中…';
      try{
        const data=await api('/api/knowledge-preview?'+new URLSearchParams({...scope,q:input.value}));
        results.replaceChildren();
        if(!data.rows.length)results.textContent='没有找到相关资料';
        for(const item of data.rows){
          const location=item.record_path??(item.part?'片段 '+item.part:'');
          const details=[item.source||'手动录入',location,item.similarity!=null?'向量相似度 '+item.similarity:'关键词'].filter(Boolean);
          const excerpt=node('article',undefined,'knowledge-result');
          excerpt.append(node('strong',item.title),node('div',details.join(' · '),'hint'),node('p',item.content));
          results.append(excerpt);
        }
      }catch(error){results.textContent=error.message;}
      finally{submit.disabled=false;}
    };
    document.body.append(dialog);dialog.showModal();
  };
  refresh();
  return refresh;
}
