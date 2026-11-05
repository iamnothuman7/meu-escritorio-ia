(() => {
  'use strict';
  const all = (selector,root=document) => [...root.querySelectorAll(selector)];
  let busy=0, toastTimer;
  const notice = message => {
    const toast=document.getElementById('app-toast'); if(!toast)return;
    toast.textContent=message;toast.hidden=false;
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toast.hidden=true;},6500);
  };
  const initialize = () => {
    all('[data-request-id]').forEach(input=>{if(!input.value)input.value=crypto.randomUUID();});
    document.body.classList.toggle('modal-open',Boolean(document.querySelector('dialog[open]')));
    document.body.classList.add('app-ready');
  };
  document.addEventListener('click',event=>{
    const open=event.target.closest('[data-open]');
    if(open){
      const dialog=document.getElementById(open.dataset.open);if(!dialog)return;
      const agent=dialog.querySelector('[name=agent]');
      if(agent&&open.dataset.agent)agent.value=open.dataset.agent;
      if(agent&&open.dataset.agentSlug){const option=all('option',agent).find(o=>o.dataset.slug===open.dataset.agentSlug);if(option)agent.value=option.value;}
      if(open.dataset.kind){const kind=dialog.querySelector('[name=kind]');if(kind)kind.value=open.dataset.kind;}
      if(!dialog.open)dialog.showModal();document.body.classList.add('modal-open');return;
    }
    const close=event.target.closest('[data-close]');
    if(close){close.closest('dialog')?.close();return;}
    if(event.target.tagName==='DIALOG'){
      const bounds=event.target.getBoundingClientRect();
      if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)event.target.close();
    }
  });
  document.addEventListener('close',()=>document.body.classList.toggle('modal-open',Boolean(document.querySelector('dialog[open]'))),true);
  document.addEventListener('change',event=>{if(event.target.matches('[data-autosubmit]'))event.target.form.requestSubmit();});
  document.addEventListener('input',event=>{
    const form=event.target.closest('form');
    if(form&&form.method.toLowerCase()==='post')form.dataset.dirty='true';
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){
      const input=document.querySelector('.topbar [name=q]');if(input){event.preventDefault();input.focus();}
    }
  });
  document.addEventListener('htmx:beforeRequest',event=>{
    const source=event.detail.elt;
    if(source?.matches('[data-live-refresh]')){
      const region=document.querySelector(source.getAttribute('hx-target'));
      if(document.hidden||document.querySelector('dialog[open]')||region?.querySelector('form[data-dirty="true"]')||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))event.preventDefault();
      return;
    }
    busy++;document.body.classList.add('is-requesting');
    const form=source?.closest('form');
    if(form){form.setAttribute('aria-busy','true');all('button',form).forEach(b=>{if(!('wasDisabled' in b.dataset))b.dataset.wasDisabled=String(b.disabled);b.disabled=true;});}
  });
  document.addEventListener('htmx:afterRequest',event=>{
    if(event.detail.elt?.matches('[data-live-refresh]'))return;
    busy=Math.max(0,busy-1);if(!busy)document.body.classList.remove('is-requesting');
    const form=event.detail.elt?.closest('form');
    if(form?.isConnected){form.removeAttribute('aria-busy');all('button[data-was-disabled]',form).forEach(b=>{b.disabled=b.dataset.wasDisabled==='true';delete b.dataset.wasDisabled;});}
  });
  document.addEventListener('htmx:afterSwap',event=>{
    initialize();
    if(event.detail.target?.id==='app-shell'){
      document.querySelectorAll('dialog[open]').forEach(d=>d.close());
      document.body.classList.remove('modal-open');
    }
  });
  document.addEventListener('htmx:responseError',event=>{
    const status=event.detail.xhr?.status;
    notice(status===403?'Sua sessão ou autorização precisa ser conferida. Entre novamente se necessário.':status===409?'Este registro mudou. Recarregue os dados antes de tentar novamente.':'Não foi possível concluir a solicitação. Seus dados continuam no formulário.');
  });
  document.addEventListener('htmx:sendError',()=>notice('Conexão interrompida. Confira a internet e tente novamente.'));
  document.addEventListener('htmx:timeout',()=>notice('A resposta demorou mais que o esperado. Confira o estado do registro antes de repetir.'));
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initialize,{once:true});else initialize();
})();
