(() => {
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{});
  let installPrompt;
  const buttons = () => [...document.querySelectorAll('[data-install]')];
  window.addEventListener('beforeinstallprompt',e=>{
    e.preventDefault(); installPrompt=e;
    buttons().forEach(b=>{b.hidden=false;});
  });
  document.addEventListener('click',async e=>{
    const btn=e.target.closest('[data-install]');
    if(!btn || !installPrompt)return;
    await installPrompt.prompt(); await installPrompt.userChoice;
    installPrompt=null; buttons().forEach(b=>{b.hidden=true;});
  });
  window.addEventListener('appinstalled',()=>{installPrompt=null;buttons().forEach(b=>{b.hidden=true;});});
})();
