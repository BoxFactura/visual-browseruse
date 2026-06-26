import spect from 'spect';

// Each fix is a self-contained IIFE: it checks its own URL inline and bails if it
// doesn't apply. Adding a new fix = appending another IIFE below. The script runs
// on every site (@include *), so every block must gate itself.

// ── e-facturate: turn "Uso de CFDI" into a plain <select> ────────────────────
// The field is a jQuery UI autocomplete on a text input (#txt_cucfdi). The
// free-text + searchable dropdown trips up the AI agent, so we swap it for a
// regular <select> built from the same catalog (window.objects_CFDIUse). Option
// values stay identical to the catalog strings ("G03 - Gastos en general.") so
// every page code path (validation, RecoverDataClient, submit's
// .val().split('-')[0]) keeps working unchanged.
(() => {
  if (!/(^|\.)e-facturate\.com$/.test(window.location.hostname)) return;

  const buildSelect = (input, options) => {
    const select = document.createElement('select');
    select.id = input.id;
    select.className = input.className;
    select.name = input.name;

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Seleccione el uso de CFDI';
    select.appendChild(placeholder);

    for (const value of options) {
      const option = document.createElement('option');
      option.value = value; // full catalog string, e.g. "G03 - Gastos en general."
      option.textContent = value;
      select.appendChild(option);
    }

    // Preserve any value the page already wrote into the input.
    if (input.value) select.value = input.value;

    input.replaceWith(select);
    return select;
  };

  const replaceWhenReady = (input) => {
    const catalog = window.objects_CFDIUse;
    if (!Array.isArray(catalog) || catalog.length === 0) return false;

    // Tear down the jQuery UI autocomplete bound to the old input, if any.
    try {
      const $ = window.$ || window.jQuery;
      if ($ && $(input).autocomplete) $(input).autocomplete('destroy');
    } catch (e) {
      /* autocomplete not initialized yet — replacing the node is enough */
    }

    buildSelect(input, catalog);
    return true;
  };

  spect('#txt_cucfdi', (el) => {
    // Only act on the original <input>; ignore the <select> we swap in (same id).
    if (el.tagName !== 'INPUT') return;

    if (replaceWhenReady(el)) return;

    // Catalog loads in $(document).ready; poll until it's populated.
    const timer = setInterval(() => {
      if (replaceWhenReady(el) || !el.isConnected) clearInterval(timer);
    }, 200);
    setTimeout(() => clearInterval(timer), 15000);
  });
})();
