(function (window) {
  'use strict';

  function getCsrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  window.PickerAutocomplete = class PickerAutocomplete {
    constructor({ inputEl, dropdownEl, fetchSuggestions, onSelect, onSave }) {
      this.inputEl = inputEl;
      this.dropdownEl = dropdownEl;
      this.fetchSuggestions = fetchSuggestions;
      this.onSelect = onSelect;
      this.onSave = onSave;
      this.items = [];
      this.activeIndex = -1;
      this.debounceTimer = null;
      this.isOpen = false;
      this.bind();
    }

    bind() {
      this.inputEl.addEventListener('input', () => {
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => this.load(), 180);
      });
      this.inputEl.addEventListener('focus', () => {
        this.load();
      });
      this.inputEl.addEventListener('keydown', (e) => {
        if (!this.isOpen) return;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.activeIndex = Math.min(this.activeIndex + 1, this.items.length - 1);
          this.highlight();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.activeIndex = Math.max(this.activeIndex - 1, 0);
          this.highlight();
        } else if (e.key === 'Escape') {
          this.close();
        } else if (e.key === 'Enter' && this.activeIndex >= 0) {
          e.preventDefault();
          e.stopPropagation();
          this.choose(this.items[this.activeIndex]);
        }
      });
      document.addEventListener('click', (e) => {
        if (!this.dropdownEl.contains(e.target) && e.target !== this.inputEl) {
          this.close();
        }
      });
    }

    async load() {
      const q = this.inputEl.value.trim();
      this.items = await this.fetchSuggestions(q);
      this.activeIndex = this.items.length ? 0 : -1;
      this.render();
      this.open();
    }

    render() {
      this.dropdownEl.innerHTML = '';
      if (!this.items.length) {
        this.dropdownEl.innerHTML =
          '<div class="picker-dropdown-empty">No matches — press Enter to add</div>';
        return;
      }
      this.items.forEach((item, idx) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'picker-dropdown-item';
        btn.dataset.index = String(idx);
        const badge =
          item.is_new
            ? '<span class="picker-dropdown-badge new">Add new</span>'
            : item.source === 'saved'
              ? '<span class="picker-dropdown-badge saved">Saved</span>'
              : '';
        btn.innerHTML =
          '<span class="picker-dropdown-label">' +
          this.escape(item.label) +
          '</span>' +
          badge;
        btn.addEventListener('mousedown', (e) => {
          e.preventDefault();
          this.choose(item);
        });
        this.dropdownEl.appendChild(btn);
      });
      this.highlight();
    }

    escape(text) {
      const d = document.createElement('div');
      d.textContent = text;
      return d.innerHTML;
    }

    highlight() {
      this.dropdownEl.querySelectorAll('.picker-dropdown-item').forEach((el, i) => {
        el.classList.toggle('active', i === this.activeIndex);
      });
    }

    choose(item) {
      if (!item) return;
      this.onSelect(item);
      if (this.onSave) this.onSave(item);
      this.inputEl.value = '';
      this.close();
    }

    open() {
      this.isOpen = true;
      this.dropdownEl.hidden = false;
    }

    close() {
      this.isOpen = false;
      this.dropdownEl.hidden = true;
    }
  };

  window.pickerRecord = async function (url, data) {
    if (!url) return;
    const body = new URLSearchParams(data);
    try {
      await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': getCsrfToken(),
        },
        body: body.toString(),
        credentials: 'same-origin',
      });
    } catch (e) {
      /* non-blocking */
    }
  };

  window.pickerFetch = async function (url, params) {
    if (!url) return [];
    const qs = new URLSearchParams(params).toString();
    const res = await fetch(url + (qs ? '?' + qs : ''), {
      credentials: 'same-origin',
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.results || [];
  };
})(window);
