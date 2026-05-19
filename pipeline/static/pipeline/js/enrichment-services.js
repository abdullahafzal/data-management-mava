(function () {
  'use strict';

  const recEl = document.getElementById('enrichment-recommended-ids');
  if (recEl && !window.__ENRICHMENT_RECOMMENDED__) {
    try {
      window.__ENRICHMENT_RECOMMENDED__ = JSON.parse(recEl.textContent);
    } catch (e) {
      window.__ENRICHMENT_RECOMMENDED__ = [];
    }
  }

  function updateCard(card, checked) {
    card.classList.toggle('selected', checked);
    const input = card.querySelector('.service-card-input');
    if (input) input.checked = checked;
  }

  function updateCount(root) {
    const countEl = root.querySelector('[data-selected-count]');
    if (!countEl) return;
    const n = root.querySelectorAll('.service-card-input:checked').length;
    countEl.textContent = n === 0 ? 'None selected' : n + ' selected';
  }

  function initRoot(root) {
    if (root._enrichmentInit) return;
    root._enrichmentInit = true;

    root.querySelectorAll('[data-service-card]').forEach((card) => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('[data-select-recommended], [data-clear-services]')) return;
        const input = card.querySelector('.service-card-input');
        if (!input) return;
        if (e.target === input) return;
        e.preventDefault();
        updateCard(card, !input.checked);
        updateCount(root);
      });
      const input = card.querySelector('.service-card-input');
      if (input) {
        input.addEventListener('change', () => {
          updateCard(card, input.checked);
          updateCount(root);
        });
      }
    });

    const recommendedBtn = root.querySelector('[data-select-recommended]');
    if (recommendedBtn) {
      recommendedBtn.addEventListener('click', () => {
        const ids = (window.__ENRICHMENT_RECOMMENDED__ || []).map(String);
        root.querySelectorAll('[data-service-card]').forEach((card) => {
          const id = card.getAttribute('data-service-id');
          updateCard(card, ids.includes(id));
        });
        updateCount(root);
      });
    }

    const clearBtn = root.querySelector('[data-clear-services]');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        root.querySelectorAll('[data-service-card]').forEach((card) => {
          updateCard(card, false);
        });
        updateCount(root);
      });
    }

    updateCount(root);

    const form = root.closest('form');
    if (form) {
      form.addEventListener('submit', () => {
        root.querySelectorAll('.service-card-input').forEach((input) => {
          input.disabled = !input.checked;
        });
      });
    }
  }

  function init() {
    document.querySelectorAll('[data-enrichment-services]').forEach(initRoot);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
