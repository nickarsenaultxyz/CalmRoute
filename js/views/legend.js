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
  const low = stats?.low_stress;
  const layers = stats?.layers;
  const residentialMiles = layers?.residential?.miles != null
    ? Math.round(layers.residential.miles)
    : null;

  // Report the mileage that is actually drawn. Quiet streets are hidden by
  // default, and 8,908 of them carry most of the low-stress mileage, so the
  // city-wide totals would badly overstate what is on screen.
  const shown = (lts) => {
    if (!layers) return null;
    const sources = state.residential
      ? ['network', 'context', 'residential']
      : ['network', 'context'];
    const total = sources.reduce(
      (sum, s) => sum + (layers[s]?.by_lts?.[String(lts)] ?? 0), 0);
    return total > 0 ? total : null;
  };
  const cityTotal = (lts) => (stats?.by_lts || []).find((r) => r.lts === lts)?.miles ?? null;

  const rows = LTS_ORDER_LEGEND.map((lts) => {
    const s = LTS[lts];
    const mi = shown(lts);
    const all = cityTotal(lts);
    const hidden = all != null && mi != null && all - mi > 1;
    const on = state.lts.has(lts);
    return `
      <button class="legend-row" role="switch" aria-checked="${on}" data-lts="${lts}"
        ${hidden ? `title="${all.toFixed(0)} mi citywide; the rest is on hidden neighbourhood streets"` : ''}>
        ${swatchSvg(lts)}
        <span class="label">
          <b>${escapeHtml(s.short)}</b>
          <span>${escapeHtml(s.detail)}</span>
        </span>
        <span class="miles">${mi != null ? `${mi.toFixed(0)} mi` : '—'}${
          hidden ? `<em> of ${all.toFixed(0)}</em>` : ''}</span>
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
      <span>${state.residential
        ? 'Quiet streets with no bike facility'
        : 'Hidden — turn on to add quiet streets'}</span></span>
      <span class="miles">${residentialMiles != null ? `${residentialMiles} mi` : ''}</span>
    </button>
    <p class="note">${EXISTING_ONLY_NOTE}</p>
    <p class="tech">
      Tap any street for its rating and the data behind it.
      Grey streets on the base map are not in Lexington's centreline file —
      usually private drives and apartment roads — so they carry no rating.
    </p>`;
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
    // The caller re-renders: the per-row mileages describe what is drawn, so
    // they all change when this layer comes or goes.
    onResidential(on);
  });
}
