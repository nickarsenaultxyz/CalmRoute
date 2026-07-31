/**
 * Map style.
 *
 * "None" is not a novelty option. Removing the basemap maximises contrast
 * between the LTS colours and the page, which is the most effective
 * low-vision accommodation available here — far more so than nudging the
 * palette, since the ratings must stay distinguishable from each other as well
 * as from the background.
 */

import { BASEMAPS } from '../config.js?v=20260731-field-notebook';
import { escapeHtml } from '../lib/format.js';

export function render(current) {
  const options = Object.entries(BASEMAPS).map(([key, bm]) => `
    <button class="legend-row" role="radio" aria-checked="${key === current}"
            data-basemap="${escapeHtml(key)}">
      <span class="label">
        <b>${escapeHtml(bm.label)}</b>
        <span>${escapeHtml(describe(key))}</span>
      </span>
    </button>`).join('');

  return `
    <div role="radiogroup" aria-label="Map style">${options}</div>
    <p class="note">Choosing <strong>None</strong> removes the background map.
      The street ratings stay, at maximum contrast.</p>`;
}

function describe(key) {
  return {
    light: 'Muted streets and labels (default)',
    gray: 'Plainer, fewer labels',
    satellite: 'Aerial imagery',
    none: 'No background — highest contrast',
  }[key] || '';
}

export function mount(root, { current, onPick }) {
  root.querySelectorAll('[data-basemap]').forEach((btn) => {
    btn.addEventListener('click', () => {
      root.querySelectorAll('[data-basemap]').forEach((b) =>
        b.setAttribute('aria-checked', String(b === btn)));
      onPick(btn.dataset.basemap);
    });
  });
}
