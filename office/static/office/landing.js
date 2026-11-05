(() => {
  'use strict';
  const toggle = document.querySelector('[data-menu-toggle]');
  const navigation = document.getElementById('site-navigation');
  const setOpen = (open, returnFocus = false) => {
    if (!toggle || !navigation) return;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Fechar navegação' : 'Abrir navegação');
    navigation.classList.toggle('is-open', open);
    if (returnFocus) toggle.focus();
  };
  if (toggle && navigation) {
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
  }

  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const mobile = window.matchMedia('(max-width: 660px)');
  const hero = document.querySelector('[data-effects-hero]');
  const canvas = hero?.querySelector('.hero-particles');
  const context = canvas?.getContext('2d', { alpha: true });
  let heroVisible = true;
  let frame = 0;
  let pointerFrame = 0;
  let lastPaint = 0;
  let width = 0;
  let height = 0;
  let particles = [];
  const revealAnimations = new Set();
  const interactiveElements = document.querySelectorAll('.button, .team-card, .step');
  const canAnimate = () => !motion.matches && !document.hidden && heroVisible;

  // Content is visible by default. Reveal only adds a short, optional animation.
  const revealTargets = document.querySelectorAll('.section-heading, .step, .team-card, .custom-visual, .custom-copy, .routine-banner, .faq-heading, .faq-list, .closing-section');
  if ('IntersectionObserver' in window) {
    const revealObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        revealObserver.unobserve(entry.target);
        if (motion.matches || document.hidden || !entry.target.animate) return;
        const siblings = entry.target.parentElement?.children;
        const index = siblings ? Array.prototype.indexOf.call(siblings, entry.target) : 0;
        const animation = entry.target.animate([
          { opacity: 0.12, transform: 'translate3d(0, 23px, 0)' },
          { opacity: 1, transform: 'translate3d(0, 0, 0)' }
        ], { duration: 720, delay: Math.min(index * 65, 220), easing: 'cubic-bezier(.18,.7,.2,1)', fill: 'none' });
        revealAnimations.add(animation);
        animation.onfinish = () => revealAnimations.delete(animation);
        animation.oncancel = () => revealAnimations.delete(animation);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -15px 0px' });
    revealTargets.forEach(element => revealObserver.observe(element));

    const effectObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        entry.target.classList.toggle('effects-offscreen', !entry.isIntersecting);
        if (entry.target === hero) {
          heroVisible = entry.isIntersecting;
          updateLoop();
        }
      });
    }, { rootMargin: '45px' });
    document.querySelectorAll('.hero, .areas-strip, .process-section, .team-section, .custom-section, .routine-section, .closing-section').forEach(element => effectObserver.observe(element));
  }

  function resizeCanvas() {
    if (!hero || !canvas || !context) return;
    const bounds = hero.getBoundingClientRect();
    width = Math.max(1, Math.round(bounds.width));
    height = Math.max(1, Math.round(bounds.height));
    const ratio = Math.min(window.devicePixelRatio || 1, mobile.matches ? 1.25 : 1.5);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const count = mobile.matches ? 11 : 28;
    particles = Array.from({ length: count }, (_, index) => ({
      x: Math.random() * width,
      y: Math.random() * height,
      speed: 3 + Math.random() * 6,
      radius: index % 5 === 0 ? 1.7 : 0.7 + Math.random() * 0.65,
      phase: Math.random() * Math.PI * 2,
      cyan: index % 3 === 0
    }));
    lastPaint = 0;
    if (motion.matches) context.clearRect(0, 0, width, height);
  }

  function paint(timestamp) {
    frame = 0;
    if (!canAnimate() || !context) return;
    // Thirty frames per second is enough for a slow ambient field.
    if (timestamp - lastPaint >= 32) {
      const elapsed = lastPaint ? Math.min((timestamp - lastPaint) / 1000, 0.07) : 0.033;
      lastPaint = timestamp;
      context.clearRect(0, 0, width, height);
      particles.forEach((particle, index) => {
        particle.y -= particle.speed * elapsed;
        particle.x += Math.sin(timestamp / 5000 + particle.phase) * elapsed * 1.7;
        if (particle.y < -6) particle.y = height + 6;
        if (particle.x < -5) particle.x = width + 5;
        if (particle.x > width + 6) particle.x = -5;
        const alpha = 0.25 + (Math.sin(timestamp / 2000 + particle.phase) + 1) * 0.15;
        const rgb = particle.cyan ? '91,211,237' : '175,135,242';
        context.beginPath();
        context.arc(particle.x, particle.y, particle.radius * 3.5, 0, Math.PI * 2);
        context.fillStyle = `rgba(${rgb},${alpha * 0.07})`;
        context.fill();
        context.beginPath();
        context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
        context.fillStyle = `rgba(${rgb},${alpha})`;
        context.fill();
        if (!mobile.matches && index % 2 === 0) {
          for (let otherIndex = index + 1; otherIndex < Math.min(index + 5, particles.length); otherIndex++) {
            const other = particles[otherIndex];
            const dx = particle.x - other.x;
            const dy = particle.y - other.y;
            const distanceSquared = dx * dx + dy * dy;
            if (distanceSquared < 10000) {
              context.beginPath();
              context.moveTo(particle.x, particle.y);
              context.lineTo(other.x, other.y);
              context.strokeStyle = `rgba(${rgb},${(1 - distanceSquared / 10000) * 0.07})`;
              context.lineWidth = 0.6;
              context.stroke();
            }
          }
        }
      });
    }
    frame = requestAnimationFrame(paint);
  }

  function updateLoop() {
    document.documentElement.classList.toggle('page-hidden', document.hidden);
    if (!canAnimate()) {
      if (frame) cancelAnimationFrame(frame);
      if (pointerFrame) cancelAnimationFrame(pointerFrame);
      frame = 0;
      pointerFrame = 0;
      lastPaint = 0;
      return;
    }
    if (context && !frame) frame = requestAnimationFrame(paint);
  }

  function resetParallax() {
    if (!hero) return;
    hero.style.setProperty('--scene-x', '0px');
    hero.style.setProperty('--scene-y', '0px');
  }
  if (hero) {
    hero.addEventListener('pointermove', event => {
      if (!finePointer.matches || !canAnimate() || pointerFrame) return;
      const bounds = hero.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width - 0.5;
      const y = (event.clientY - bounds.top) / bounds.height - 0.5;
      pointerFrame = requestAnimationFrame(() => {
        pointerFrame = 0;
        if (!canAnimate()) return;
        hero.style.setProperty('--scene-x', `${Math.max(-10, Math.min(10, x * 18)).toFixed(2)}px`);
        hero.style.setProperty('--scene-y', `${Math.max(-7, Math.min(7, y * 12)).toFixed(2)}px`);
      });
    }, { passive: true });
    hero.addEventListener('pointerleave', resetParallax, { passive: true });
  }

  interactiveElements.forEach(element => {
    element.addEventListener('pointermove', event => {
      if (!finePointer.matches || motion.matches || document.hidden) return;
      const bounds = element.getBoundingClientRect();
      const x = event.clientX - bounds.left;
      const y = event.clientY - bounds.top;
      if (element.matches('.button') && !element.disabled) {
        element.style.setProperty('--magnetic-x', `${((x / bounds.width - 0.5) * 5).toFixed(2)}px`);
        element.style.setProperty('--magnetic-y', `${((y / bounds.height - 0.5) * 4).toFixed(2)}px`);
      } else {
        element.style.setProperty('--glow-x', `${x.toFixed(1)}px`);
        element.style.setProperty('--glow-y', `${y.toFixed(1)}px`);
      }
    }, { passive: true });
    element.addEventListener('pointerleave', () => {
      element.style.setProperty('--magnetic-x', '0px');
      element.style.setProperty('--magnetic-y', '0px');
    }, { passive: true });
  });

  document.addEventListener('visibilitychange', () => {
    revealAnimations.forEach(animation => document.hidden ? animation.pause() : animation.play());
    updateLoop();
  });
  motion.addEventListener('change', () => {
    revealAnimations.forEach(animation => animation.cancel());
    resetParallax();
    interactiveElements.forEach(element => {
      element.style.setProperty('--magnetic-x', '0px');
      element.style.setProperty('--magnetic-y', '0px');
    });
    resizeCanvas();
    updateLoop();
  });
  if (hero && 'ResizeObserver' in window) new ResizeObserver(resizeCanvas).observe(hero);
  else window.addEventListener('resize', resizeCanvas, { passive: true });
  mobile.addEventListener('change', resizeCanvas);
  resizeCanvas();
  updateLoop();
})();
