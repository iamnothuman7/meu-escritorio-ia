(() => {
  'use strict';
  const form = document.querySelector('[data-auth-form]');
  const password = document.getElementById('password');
  const toggle = document.querySelector('[data-password-toggle]');

  if (password && toggle) {
    toggle.hidden = false;
    toggle.addEventListener('click', () => {
      const show = password.type === 'password';
      password.type = show ? 'text' : 'password';
      toggle.setAttribute('aria-label', show ? 'Ocultar senha' : 'Mostrar senha');
      toggle.setAttribute('aria-pressed', String(show));
      toggle.querySelector('[data-password-show]').hidden = show;
      toggle.querySelector('[data-password-hide]').hidden = !show;
    });
  }

  if (form) {
    const submit = form.querySelector('[data-auth-submit]');
    const label = submit?.querySelector('[data-submit-label]');
    const spinner = submit?.querySelector('.button-spinner');
    const arrow = submit?.querySelector('.button-arrow');
    const status = form.querySelector('[data-auth-status]');
    const initialLabel = label?.textContent;
    let submitting = false;
    form.addEventListener('submit', (event) => {
      if (submitting) { event.preventDefault(); return; }
      if (!form.checkValidity()) { event.preventDefault(); form.reportValidity(); return; }
      submitting = true;
      form.setAttribute('aria-busy', 'true');
      if (submit) submit.disabled = true;
      if (label) label.textContent = 'Aguarde um instante';
      if (spinner) spinner.hidden = false;
      if (arrow) arrow.hidden = true;
      if (status) status.textContent = 'Enviando seus dados. Aguarde a resposta.';
      // Keep native POST navigation, CSRF and the server's validation response.
    });
    window.addEventListener('pageshow', () => {
      submitting = false;
      form.removeAttribute('aria-busy');
      if (submit) submit.disabled = false;
      if (label) label.textContent = initialLabel;
      if (spinner) spinner.hidden = true;
      if (arrow) arrow.hidden = false;
      if (status) status.textContent = '';
    });
  }
  const syncVisibility = () => document.body.classList.toggle('is-page-hidden', document.hidden);
  document.addEventListener('visibilitychange', syncVisibility);
  syncVisibility();
})();
