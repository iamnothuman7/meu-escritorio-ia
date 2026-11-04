(() => {
  'use strict';
  const toggle = document.querySelector('[data-menu-toggle]');
  const navigation = document.getElementById('site-navigation');
  if (!toggle || !navigation) return;
  const setOpen = (open, returnFocus = false) => {
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Fechar navegação' : 'Abrir navegação');
    navigation.classList.toggle('is-open', open);
    if (returnFocus) toggle.focus();
  };
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  navigation.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setOpen(false)));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') setOpen(false, true);
  });
  document.addEventListener('click', event => {
    if (!navigation.contains(event.target) && !toggle.contains(event.target)) setOpen(false);
  });
  const desktop = window.matchMedia('(min-width: 661px)');
  desktop.addEventListener('change', event => { if (event.matches) setOpen(false); });
})();
