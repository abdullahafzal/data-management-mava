document.querySelectorAll('[data-quick-filters]').forEach((root) => {
  root.querySelectorAll('[data-quick-filter-pill]').forEach((pill) => {
    const input = pill.querySelector('input[type="checkbox"]');
    if (!input) return;
    input.addEventListener('change', () => {
      pill.classList.toggle('active', input.checked);
    });
  });
});
