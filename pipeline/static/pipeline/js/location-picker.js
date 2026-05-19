(function () {
  'use strict';

  const regionsEl = document.getElementById('location-regions-data');
  if (regionsEl && !window.__LOCATION_REGIONS__) {
    try {
      window.__LOCATION_REGIONS__ = JSON.parse(regionsEl.textContent);
    } catch (e) {
      window.__LOCATION_REGIONS__ = {};
    }
  }

  const REGIONS = window.__LOCATION_REGIONS__ || {};

  function normalizeCode(value) {
    return String(value || '').trim().toUpperCase();
  }

  function formatValue(country, tags, customText) {
    if (customText && customText.trim()) {
      return 'custom:' + customText.trim();
    }
    const codes = tags.map((t) => t.code).filter(Boolean);
    if (!codes.length) return '';
    return (country || 'US').toUpperCase() + '|' + codes.join(',');
  }

  function parseValue(raw) {
    const v = (raw || '').trim();
    if (!v) return { country: 'US', tags: [], custom: false, customText: '' };
    if (v.toLowerCase().startsWith('custom:')) {
      return {
        country: 'US',
        tags: [],
        custom: true,
        customText: v.slice(7).trim(),
      };
    }
    if (v.includes('|')) {
      const [country, rest] = v.split('|');
      const codes = rest.split(',').map((c) => normalizeCode(c)).filter(Boolean);
      const map = REGIONS[(country || 'US').toUpperCase()] || REGIONS.US || [];
      const byCode = Object.fromEntries(map.map((r) => [r.code, r.label]));
      return {
        country: (country || 'US').toUpperCase(),
        tags: codes.map((code) => ({ code, label: byCode[code] || code })),
        custom: false,
        customText: '',
      };
    }
    return { country: 'US', tags: [], custom: true, customText: v };
  }

  class LocationPicker {
    constructor(root) {
      this.root = root;
      this.countryEl = root.querySelector('[data-country]');
      this.tagsEl = root.querySelector('[data-tags]');
      this.inputEl = root.querySelector('[data-tag-input]');
      this.hiddenEl = root.querySelector('[data-value]');
      this.customCheck = root.querySelector('[data-custom]');
      this.customPanel = root.querySelector('[data-custom-panel]');
      this.customInput = root.querySelector('[data-custom-input]');
      this.dropdownEl = root.querySelector('[data-dropdown]');
      this.tagsWrap = root.querySelector('[data-tags-wrap]');
      this.tryBtn = root.querySelector('[data-try-country]');
      this.suggestUrl = root.getAttribute('data-suggest-url') || '';
      this.recordUrl = root.getAttribute('data-record-url') || '';

      const initial = parseValue(this.hiddenEl.value);
      this.tags = initial.tags;
      this.customCheck.checked = initial.custom;
      if (initial.country) this.countryEl.value = initial.country;
      if (initial.customText) this.customInput.value = initial.customText;

      this.bind();
      this.syncMode();
      this.renderTags();
      this.syncHidden();
      this.setupAutocomplete();
      this.setupCustomAutocomplete();
    }

    setupAutocomplete() {
      if (!window.PickerAutocomplete || !this.dropdownEl) return;
      this.autocomplete = new window.PickerAutocomplete({
        inputEl: this.inputEl,
        dropdownEl: this.dropdownEl,
        fetchSuggestions: (q) =>
          window.pickerFetch(this.suggestUrl, {
            q: q,
            country: this.countryEl.value,
          }),
        onSelect: (item) => this.addTag(item.code, item.label, item.is_custom),
        onSave: (item) =>
          window.pickerRecord(this.recordUrl, {
            country: this.countryEl.value,
            label: item.label,
            code: item.code,
            is_custom: item.is_custom ? 'true' : 'false',
          }),
      });
    }

    setupCustomAutocomplete() {
      if (!this.customInput || !window.PickerAutocomplete) return;
      let customDropdown = this.customPanel.querySelector('[data-custom-dropdown]');
      if (!customDropdown) {
        customDropdown = document.createElement('div');
        customDropdown.className = 'picker-dropdown';
        customDropdown.setAttribute('data-custom-dropdown', '');
        customDropdown.hidden = true;
        this.customInput.parentElement.appendChild(customDropdown);
      }
      this.customAutocomplete = new window.PickerAutocomplete({
        inputEl: this.customInput,
        dropdownEl: customDropdown,
        fetchSuggestions: (q) =>
          window.pickerFetch(this.suggestUrl, {
            q: q,
            country: this.countryEl.value,
          }),
        onSelect: (item) => {
          const cur = this.customInput.value.trim();
          const add = item.label;
          this.customInput.value = cur ? cur + ', ' + add : add;
          this.syncHidden();
        },
        onSave: (item) =>
          window.pickerRecord(this.recordUrl, {
            country: this.countryEl.value,
            label: item.label,
            code: item.code,
            is_custom: 'true',
          }),
      });
    }

    bind() {
      this.countryEl.addEventListener('change', () => {
        this.syncHidden();
        if (this.autocomplete && this.inputEl.value.trim()) {
          this.autocomplete.load();
        }
      });

      this.inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (!this.autocomplete || !this.autocomplete.isOpen)) {
          e.preventDefault();
          this.addFromInput();
        } else if (e.key === ',') {
          e.preventDefault();
          this.addFromInput();
        } else if (e.key === 'Backspace' && !this.inputEl.value && this.tags.length) {
          this.tags.pop();
          this.renderTags();
          this.syncHidden();
        }
      });

      this.customCheck.addEventListener('change', () => this.syncMode());
      this.customInput.addEventListener('input', () => this.syncHidden());

      if (this.tryBtn) {
        this.tryBtn.addEventListener('click', () => {
          this.countryEl.value = 'US';
          this.customCheck.checked = false;
          this.syncMode();
          this.inputEl.focus();
        });
      }
    }

    syncMode() {
      const custom = this.customCheck.checked;
      this.customPanel.classList.toggle('d-none', !custom);
      this.tagsWrap.classList.toggle('d-none', custom);
      this.countryEl.disabled = custom;
      this.syncHidden();
    }

    addTag(code, label, isCustom, save) {
      if (!this.tags.some((t) => t.code === code && t.label === label)) {
        this.tags.push({ code, label, isCustom: !!isCustom });
      }
      this.renderTags();
      this.syncHidden();
      if (save !== false) {
        window.pickerRecord(this.recordUrl, {
          country: this.countryEl.value,
          label: label,
          code: code,
          is_custom: isCustom ? 'true' : 'false',
        });
      }
    }

    addFromInput() {
      const raw = this.inputEl.value.trim().replace(/,$/, '');
      if (!raw) return;

      const country = this.countryEl.value;
      const list = REGIONS[country] || [];
      let code = normalizeCode(raw);
      let label = raw;
      let isCustom = false;

      const byCode = list.find((r) => r.code === code);
      const byLabel = list.find(
        (r) => r.label.toLowerCase() === raw.toLowerCase()
      );
      if (byCode) {
        code = byCode.code;
        label = byCode.label;
      } else if (byLabel) {
        code = byLabel.code;
        label = byLabel.label;
      } else {
        isCustom = true;
        code = raw;
      }

      this.addTag(code, label, isCustom, true);
      this.inputEl.value = '';
      if (this.autocomplete) this.autocomplete.close();
    }

    removeTag(code) {
      this.tags = this.tags.filter((t) => t.code !== code);
      this.renderTags();
      this.syncHidden();
    }

    renderTags() {
      this.tagsEl.innerHTML = '';
      const maxVisible = 8;
      const visible = this.tags.slice(0, maxVisible);
      const overflow = this.tags.length - visible.length;

      visible.forEach((tag) => {
        const pill = document.createElement('span');
        pill.className = 'location-pill';
        pill.setAttribute('role', 'listitem');
        pill.innerHTML =
          '<span class="location-pill-label"></span>' +
          '<button type="button" class="location-pill-remove" aria-label="Remove">&times;</button>';
        pill.querySelector('.location-pill-label').textContent = tag.label;
        pill.querySelector('.location-pill-remove').addEventListener('click', () => {
          this.removeTag(tag.code);
        });
        this.tagsEl.appendChild(pill);
      });

      if (overflow > 0) {
        const more = document.createElement('span');
        more.className = 'location-pill location-pill-more';
        more.textContent = '+ ' + overflow + ' …';
        this.tagsEl.appendChild(more);
      }
    }

    syncHidden() {
      if (this.customCheck.checked) {
        this.hiddenEl.value = formatValue('US', [], this.customInput.value);
      } else {
        this.hiddenEl.value = formatValue(
          this.countryEl.value,
          this.tags,
          ''
        );
      }
    }
  }

  function init() {
    document.querySelectorAll('[data-location-picker]').forEach((root) => {
      if (!root._locationPicker) {
        root._locationPicker = new LocationPicker(root);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
