(() => {
  const all = (s, root = document) => [...root.querySelectorAll(s)];
  all('[data-request-id]').forEach(el => { el.value = crypto.randomUUID(); });
  all('[data-open]').forEach(btn => btn.addEventListener('click', () => {
    const dialog = document.getElementById(btn.dataset.open);
    if (!dialog) return;
    if (btn.dataset.agent) dialog.querySelector('[name=agent]').value = btn.dataset.agent;
    if (btn.dataset.agentSlug) {
      const choice = all('[name=agent] option',dialog).find(o=>o.dataset.slug===btn.dataset.agentSlug);
      if(choice) dialog.querySelector('[name=agent]').value=choice.value;
    }
    if (btn.dataset.kind) dialog.querySelector('[name=kind]').value = btn.dataset.kind;
    dialog.showModal();
    document.body.classList.add('modal-open');
  }));
  all('dialog').forEach(dialog => {
    all('[data-close]', dialog).forEach(btn => btn.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('close', () => document.body.classList.remove('modal-open'));
    dialog.addEventListener('click', e => { if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();} });
  });
  all('[data-autosubmit]').forEach(el => el.addEventListener('change', () => el.form.requestSubmit()));
  document.addEventListener('keydown', e => {
    if(e.key==='/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) {
      const search=document.querySelector('.topbar [name=q]'); if(search){e.preventDefault();search.focus();}
    }
  });
  if(document.querySelector('[data-poll]')) setTimeout(()=>{if(!document.hidden && !document.querySelector('dialog[open]') && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) location.reload();},8000);
  // Persist only the motion preference on this browser, never business data or credentials.
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) document.body.classList.add('reduced-motion');
})();
