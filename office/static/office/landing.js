(() => {
  'use strict';

  const demo = document.querySelector('[data-flow-demo]');
  const tabList = demo?.querySelector('[role="tablist"]');
  if (!tabList) return;

  const tabs = Array.from(tabList.querySelectorAll('[data-flow-tab]'));
  const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
  if (!tabs.length || panels.some(panel => !panel)) return;

  function activateTab(index, moveFocus = false) {
    tabs.forEach((tab, position) => {
      const selected = position === index;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels[position].hidden = !selected;
    });
    if (moveFocus) tabs[index].focus();
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(index));
    tab.addEventListener('keydown', event => {
      let nextIndex;
      switch (event.key) {
        case 'ArrowRight':
          nextIndex = (index + 1) % tabs.length;
          break;
        case 'ArrowLeft':
          nextIndex = (index + tabs.length - 1) % tabs.length;
          break;
        case 'Home':
          nextIndex = 0;
          break;
        case 'End':
          nextIndex = tabs.length - 1;
          break;
        default:
          return;
      }
      event.preventDefault();
      activateTab(nextIndex, true);
    });
  });

  activateTab(0);
  tabList.hidden = false;
})();
