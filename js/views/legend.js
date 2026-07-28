/**
 * The legend IS the filter.
 *
 * Replaces the previous map's uncollapsible 400px layer control. Each row is a
 * real `<button role="switch">` so it is keyboard operable and announced
 * correctly, and carries live mileage from stats.json -- the distributions the
 * old pipeline printed to stdout and never showed anyone.
 */

import { EXISTING_ONLY_NOTE, LTS, LTS_ORDER_LEGEND } from '../config.js';
import { swatchSvg } from '../layers.js';
import { escapeHtml } from '../lib/format.js';

export function render(stats, state) {
  const milesByLts = new Map((stats?.by_lts || []).map((r) => [r.lts, r.miles]));
  const low = stats?.low_stress;
  const residentialMiles = stats?.layers?.residential?.miles != null
    ? Math.round(stats.layers.residential.miles)
    : null;

  const rows = LTS_ORDER_LEGEND.map((lts) => {
    const s = LTS[lts];
    const mi = milesByLts.get(lts);
    const on = state.lts.has(lts);
    return `
      <button class="legend-row" role="switch" aria-checked="${on}" data-lts="${lts}">
        ${swatchSvg(lts)}
        <span class="label">
          <b>${escapeHtml(s.short)}</b>
          <span>${escapeHtml(s.detail)}</span>
        </span>
        <span class="miles">${mi != null ? `${mi.toFixed(0)} mi` : ''}</span>
      </button>`;
  }).join('');

  const headline = low ? `
    <p class="note">
      <strong>${low.miles.toLocaleString('en-US')} miles</strong> are comfortable for an
      ordinary adult — but they are split into
      <strong>${low.islands.toLocaleString('en-US')} disconnected islands</strong>,
      and the largest holds only ${low.largest_island_share_pct}% of them.
      About ${stats.ridable_lts3.miles.toLocaleString('en-US')} miles are ridable
      if you are a confident rider.
    </p>` : '';

  return `
    ${headline}
    <div class="legend" role="group" aria-label="Filter by comfort rating">
      ${rows}
    </div>
    <button class="legend-row" role="switch" aria-checked="${state.residential}"
            data-toggle="residential">
      <svg class="swatch" viewBox="0 0 34 12" aria-hidden="true">
        <line x1="1" y1="3.5" x2="33" y2="3.5" stroke="${LTS[1].color}" stroke-width="2.5"/>
        <line x1="1" y1="8.5" x2="33" y2="8.5" stroke="${LTS[2].color}" stroke-width="2.5"/>
      </svg>
      <span class="label"><b>Neighbourhood streets</b>
      <span>The quiet-street bulk of the network</span></span>
      <span class="miles">${residentialMiles != null ? `${residentialMiles} mi` : ''}</span>
    </button>
    <p class="note">${EXISTING_ONLY_NOTE}</p>
    <p class="tech">Tap any street for its rating and the data behind it.</p>`;
}

/** Wire the switches. `onLts` / `onResidential` receive (value, isOn). */
export function mount(root, { onLts, onResidential }) {
  root.querySelectorAll('[data-lts]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const on = btn.getAttribute('aria-checked') !== 'true';
      btn.setAttribute('aria-checked', String(on));
      onLts(Number(btn.dataset.lts), on);
    });
  });

  const res = root.querySelector('[data-toggle="residential"]');
  res?.addEventListener('click', () => {
    const on = res.getAttribute('aria-checked') !== 'true';
    res.setAttribute('aria-checked', String(on));
    onResidential(on);
  });
}
