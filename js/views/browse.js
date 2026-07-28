/**
 * Browse streets as a list — the parallel non-map path.
 *
 * Map features are drawn on a GPU canvas. They are not DOM nodes, cannot take
 * focus, and cannot be reached by a screen reader or by keyboard. Rather than
 * pretending otherwise by scattering ARIA over a canvas, this view offers the
 * same information and the same actions through ordinary focusable controls:
 * type a street name, pick it from a list, get its rating and put it on the
 * map. Selecting here does exactly what clicking the map does.
 *
 * This is the pragmatic route to WCAG conformance for a canvas map, and it is
 * genuinely useful with a mouse too — "where is Tates Creek Rd" is a common
 * question that panning cannot answer.
 */

import { LTS } from '../config.js';
import { escapeHtml } from '../lib/format.js';

/**
 * Group loaded features by street name.
 *
 * Built from whatever is loaded, so it improves as layers arrive. Names are
 * already Title Cased by the exporter.
 */
export function buildIndex(featuresById) {
  const byName = new Map();
  for (const f of featuresById.values()) {
    const name = f.properties?.nm;
    if (!name) continue;
    let entry = byName.get(name);
    if (!entry) {
      entry = { name, ids: [], miles: 0, best: 9, worst: 0 };
      byName.set(name, entry);
    }
    entry.ids.push(f.id);
    entry.miles += f.properties.mi || 0;
    const lts = f.properties.lts;
    if (lts > 0) {
      entry.best = Math.min(entry.best, lts);
      entry.worst = Math.max(entry.worst, lts);
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function render(index, { query = '', loadedAll = false } = {}) {
  return `
    <label class="field">
      <span class="visually-hidden">Search street names</span>
      <input id="browse-q" type="search" placeholder="Search street names…"
             autocomplete="off" spellcheck="false" value="${escapeHtml(query)}">
    </label>
    ${loadedAll ? '' : `<p class="tech">Showing streets with a bike facility and
      busy roads. Turn on neighbourhood streets to search all of them.</p>`}
    <ul id="browse-results" class="browse-list"></ul>`;
}

export function results(index, query) {
  const q = query.trim().toLowerCase();
  const matches = q
    ? index.filter((e) => e.name.toLowerCase().includes(q))
    : index;
  const shown = matches.slice(0, 120);

  if (!shown.length) {
    return `<li class="browse-empty">No street matches “${escapeHtml(query)}”.</li>`;
  }

  const rows = shown.map((e) => {
    const lts = LTS[e.best] || LTS[4];
    const mixed = e.worst > e.best;
    return `
      <li>
        <button class="browse-row" data-ids="${e.ids.join(',')}">
          <span class="dot" style="background:${lts.color}" aria-hidden="true"></span>
          <span class="label">
            <b>${escapeHtml(e.name)}</b>
            <span>${escapeHtml(lts.short)}${mixed ? ' — varies along its length' : ''}</span>
          </span>
          <span class="miles">${e.miles.toFixed(1)} mi</span>
        </button>
      </li>`;
  }).join('');

  const more = matches.length > shown.length
    ? `<li class="browse-empty">${matches.length - shown.length} more —
       keep typing to narrow.</li>`
    : '';
  return rows + more;
}

export function mount(root, index, { onPick, onQuery } = {}) {
  const input = root.querySelector('#browse-q');
  const list = root.querySelector('#browse-results');

  const draw = (q) => { list.innerHTML = results(index, q); };
  draw(input?.value || '');

  input?.addEventListener('input', () => {
    draw(input.value);
    onQuery?.(input.value);
  });

  // Delegated so the handler survives every re-render of the list.
  list?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.browse-row');
    if (!btn) return;
    const ids = btn.dataset.ids.split(',').map(Number);
    onPick?.(ids);
  });

  // Down-arrow from the search box moves into the results, so the whole view is
  // operable without leaving the keyboard.
  input?.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      list?.querySelector('.browse-row')?.focus();
    }
  });

  list?.addEventListener('keydown', (ev) => {
    const rows = [...list.querySelectorAll('.browse-row')];
    const i = rows.indexOf(document.activeElement);
    if (i < 0) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); rows[i + 1]?.focus(); }
    if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (i === 0) input?.focus(); else rows[i - 1]?.focus();
    }
  });

  return { focus: () => input?.focus(), redraw: () => draw(input?.value || '') };
}
