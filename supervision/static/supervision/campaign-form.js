(function () {
  'use strict';

  function setFileState(state, iconClass, text) {
    state.replaceChildren();
    const icon = document.createElement('i');
    icon.className = iconClass;
    icon.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.textContent = text;
    state.append(icon, label);
  }

  function initCampaignForm() {
    const form = document.querySelector('[data-campaign-form]');
    if (!form) return;

    const cards = Array.from(form.querySelectorAll('[data-file-card]'));
    const counter = document.querySelector('[data-ready-count]');
    const progress = document.querySelector('[data-progress-bar]');
    const message = document.querySelector('[data-ready-message]');
    if (!counter || !progress || !message) return;

    function refreshProgress() {
      const ready = cards.filter(function (card) {
        const input = card.querySelector('input[type="file"]');
        return input && input.files.length > 0;
      }).length;
      counter.textContent = String(ready);
      progress.style.width = ((ready / 3) * 100) + '%';
      message.textContent = ready === 3
        ? 'Tout est prêt pour la comparaison.'
        : 'Encore ' + (3 - ready) + ' fichier' + (3 - ready > 1 ? 's' : '') + ' à sélectionner.';
      message.classList.toggle('is-ready', ready === 3);
    }

    cards.forEach(function (card) {
      const input = card.querySelector('input[type="file"]');
      const state = card.querySelector('[data-file-state]');
      if (!input || !state) return;

      input.addEventListener('change', function () {
        card.classList.remove('is-error', 'is-ready');
        state.classList.remove('is-error', 'is-ready');

        if (input.files.length) {
          card.classList.add('is-ready');
          state.classList.add('is-ready');
          setFileState(state, 'bi bi-check-circle-fill', input.files[0].name);
        } else {
          setFileState(state, 'bi bi-circle', 'À sélectionner');
        }
        refreshProgress();
      });
    });

    refreshProgress();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCampaignForm, {once: true});
  } else {
    initCampaignForm();
  }
})();
