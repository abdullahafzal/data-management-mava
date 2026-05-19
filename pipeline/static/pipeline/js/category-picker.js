(function () {
  'use strict';

  function parseValue(raw) {
    if (!raw || !String(raw).trim()) return [];
    return String(raw)
      .split(/[,;\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function formatValue(tags) {
    return tags.join(', ');
  }

  class CategoryPicker {
    constructor(root) {
      this.root = root;
      this.tagsEl = root.querySelector('[data-tags]');
      this.inputEl = root.querySelector('[data-tag-input]');
      this.hiddenEl = root.querySelector('[data-value]');
      this.dropdownEl = root.querySelector('[data-dropdown]');
      this.suggestUrl = root.getAttribute('data-suggest-url') || '';
      this.recordUrl = root.getAttribute('data-record-url') || '';
      this.tags = parseValue(this.hiddenEl.value);
      this.bind();
      this.renderTags();
      this.setupAutocomplete();
    }

    setupAutocomplete() {
      if (!window.PickerAutocomplete || !this.dropdownEl) return;
      this.autocomplete = new window.PickerAutocomplete({
        inputEl: this.inputEl,
        dropdownEl: this.dropdownEl,
        fetchSuggestions: (q) =>
          window.pickerFetch(this.suggestUrl, { q: q }),
        onSelect: (item) => this.addTag(item.label),
        onSave: (item) =>
          window.pickerRecord(this.recordUrl, { name: item.label }),
      });
    }

    bind() {
      this.inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (!this.autocomplete || !this.autocomplete.isOpen)) {
          e.preventDefault();
          this.addFromInput();
        } else if (e.key === ',' ) {
          e.preventDefault();
          this.addFromInput();
        } else if (e.key === 'Backspace' && !this.inputEl.value && this.tags.length) {
          this.tags.pop();
          this.renderTags();
          this.syncHidden();
        }
      });

      this.root.querySelectorAll('[data-try-category]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const val = btn.getAttribute('data-try-category');
          if (val) this.addTag(val, true);
          this.inputEl.focus();
        });
      });
    }

    addTag(label, save) {
      const t = (label || '').trim();
      if (!t || this.tags.includes(t)) return;
      this.tags.push(t);
      this.renderTags();
      this.syncHidden();
      if (save) window.pickerRecord(this.recordUrl, { name: t });
    }

    addFromInput() {
      const raw = this.inputEl.value.trim().replace(/,$/, '');
      if (!raw) return;
      raw.split(/[,;]+/).forEach((part) => this.addTag(part.trim(), true));
      this.inputEl.value = '';
      if (this.autocomplete) this.autocomplete.close();
    }

    removeTag(tag) {
      this.tags = this.tags.filter((t) => t !== tag);
      this.renderTags();
      this.syncHidden();
    }

    renderTags() {
      this.tagsEl.innerHTML = '';
      this.tags.forEach((tag) => {
        const pill = document.createElement('span');
        pill.className = 'location-pill';
        pill.innerHTML =
          '<span class="location-pill-label"></span>' +
          '<button type="button" class="location-pill-remove" aria-label="Remove">&times;</button>';
        pill.querySelector('.location-pill-label').textContent = tag;
        pill.querySelector('.location-pill-remove').addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.removeTag(tag);
        });
        this.tagsEl.appendChild(pill);
      });
    }

    syncHidden() {
      this.hiddenEl.value = formatValue(this.tags);
    }
  }

  function init() {
    document.querySelectorAll('[data-category-picker]').forEach((root) => {
      if (!root._categoryPicker) {
        root._categoryPicker = new CategoryPicker(root);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
