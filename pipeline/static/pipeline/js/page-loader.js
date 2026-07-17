(function () {
  const loader = document.getElementById('page-loader');
  if (!loader) return;

  function showLoader() {
    loader.classList.remove('is-hidden');
    loader.setAttribute('aria-busy', 'true');
  }

  function hideLoader() {
    loader.classList.add('is-hidden');
    loader.setAttribute('aria-busy', 'false');
  }

  function scrollToHash() {
    const id = window.location.hash.slice(1);
    if (!id) return;
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function shouldShowLoaderForLink(anchor) {
    if (!anchor.href) return false;
    if (anchor.target === '_blank') return false;
    if (anchor.hasAttribute('download')) return false;
    if (anchor.dataset.noLoader !== undefined) return false;
    if (anchor.getAttribute('href')?.startsWith('#')) return false;
    if (anchor.dataset.bsToggle || anchor.dataset.bsDismiss) return false;
    try {
      const url = new URL(anchor.href, window.location.origin);
      if (url.origin !== window.location.origin) return false;
      if (url.pathname.includes('/download')) return false;
      return true;
    } catch {
      return false;
    }
  }

  document.addEventListener('click', (event) => {
    const anchor = event.target.closest('a[href]');
    if (anchor && shouldShowLoaderForLink(anchor)) {
      showLoader();
    }
  });

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (form.tagName !== 'FORM') return;
    if (form.dataset.noLoader !== undefined) return;
    if (form.target === '_blank') return;
    // Defer so confirm()/preventDefault handlers can cancel without stuck loader
    window.setTimeout(() => {
      if (event.defaultPrevented) {
        hideLoader();
        return;
      }
      showLoader();
    }, 0);
  });

  window.addEventListener('pageshow', () => {
    hideLoader();
    scrollToHash();
  });

  window.addEventListener('load', () => {
    hideLoader();
    scrollToHash();
  });
})();
