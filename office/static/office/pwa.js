(() => {
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{});
  let installPrompt;
  const buttons = () => [...document.querySelectorAll('[data-install]')];
  const syncInstallButtons = () => buttons().forEach(b=>{b.hidden=!installPrompt;});
  window.addEventListener('beforeinstallprompt',e=>{
    e.preventDefault(); installPrompt=e;
    syncInstallButtons();
  });
  document.addEventListener('click',async e=>{
    const btn=e.target.closest('[data-install]');
    if(!btn || !installPrompt)return;
    await installPrompt.prompt(); await installPrompt.userChoice;
    installPrompt=null; buttons().forEach(b=>{b.hidden=true;});
  });
  window.addEventListener('appinstalled',()=>{installPrompt=null;buttons().forEach(b=>{b.hidden=true;});});
  document.addEventListener('htmx:afterSwap',syncInstallButtons);
})();
