/**
 * CalmRoute route planner.
 *
 * This keeps the supplied Field Notebook reference's information architecture
 * while using the real Lexington graph: search first, compare three actual
 * route preferences, then inspect stress along the selected ride.
 */

import {
  FAC_PUBLIC, LTS,
} from '../config.js?v=20260731-solid-lines';
import { escapeHtml, miles as fmtMiles, minutes } from '../lib/format.js';

const POINT = {
  from: { letter: 'A', label: 'Start' },
  to: { letter: 'B', label: 'Destination' },
};

const PRESETS = {
  calmest: { label: 'Calmest', slider: 0 },
  balanced: { label: 'Balanced', slider: 50 },
  fastest: { label: 'Fastest', slider: 100 },
};

export function render(state, stats, mapState) {
  const screen = state.screen || 'explore';
  const content = screen === 'detail' && selected(state)
    ? detail(state)
    : screen === 'results'
      ? results(state, mapState)
      : explore(state, stats, mapState);

  return `
    <div class="route-stack">
      ${screen === 'detail' ? '' : searchBox(state)}
      ${content}
    </div>`;
}

function searchBox(state) {
  return `
    <section class="box route-search-box" aria-label="Route locations">
      ${locationRow(state, 'from')}
      ${locationRow(state, 'to')}
      <div class="route-search-actions">
        <button class="btn ghost" type="button" data-point="from"
                aria-pressed="${state.picking === 'from'}">
          ${state.picking === 'from' ? 'Tap map for A…' : 'Pick A on map'}
        </button>
        <button class="btn ghost" type="button" data-point="to"
                aria-pressed="${state.picking === 'to'}">
          ${state.picking === 'to' ? 'Tap map for B…' : 'Pick B on map'}
        </button>
        <span class="grow"></span>
        ${state.from || state.to
          ? '<button class="btn ghost" type="button" data-route-action="clear">Clear</button>'
          : ''}
      </div>
      ${state.screen === 'explore' ? `
        <button class="btn primary route-find" type="button"
                data-route-action="find"
                ${state.from && state.to ? '' : 'disabled'}>
          ${routeIcon()} Find calm routes
        </button>` : ''}
    </section>
    <p class="route-geocoder-note">
      Press Enter or use the search button to resolve an address through
      <a href="https://nominatim.openstreetmap.org/" target="_blank"
         rel="noopener">OpenStreetMap Nominatim</a>.
    </p>`;
}

function locationRow(state, which) {
  const point = state[which];
  const search = state.locationSearch?.[which] || {};
  const query = search.query ?? point?.name ?? '';
  const pending = search.status === 'pending';
  const matches = (search.results || []).map((location, index) => `
    <button type="button" class="location-result route-location-result"
            data-location-result data-which="${which}" data-index="${index}">
      ${escapeHtml(location.name)}
    </button>`).join('');

  return `
    <div class="route-location">
      <form class="ab-row" data-location-form="${which}">
        <span class="route-pin ${which}" aria-hidden="true">${POINT[which].letter}</span>
        <label class="visually-hidden" for="location-${which}">${POINT[which].label}</label>
        <input class="input" id="location-${which}" type="search"
               autocomplete="street-address" enterkeyhint="search"
               data-location-input="${which}" value="${escapeHtml(query)}"
               placeholder="${POINT[which].label} address or place">
        <button class="btn btn-icon route-search-submit" type="submit"
                aria-label="Search for ${POINT[which].label.toLowerCase()}"
                ${pending ? 'disabled' : ''}>${pending ? '…' : searchIcon()}</button>
        ${which === 'to' ? `
          <button class="btn btn-icon" type="button" data-route-action="swap"
                  aria-label="Swap start and destination"
                  ${state.from && state.to ? '' : 'disabled'}>${swapIcon()}</button>` : ''}
      </form>
      ${search.message ? `<p class="location-status ${
        search.status === 'error' ? 'error' : ''}" role="status">${
        escapeHtml(search.message)}</p>` : ''}
      ${matches ? `<div class="location-results"
        aria-label="${POINT[which].label} location matches">${matches}</div>` : ''}
    </div>`;
}

function explore(state, stats, mapState) {
  return `
    <p class="route-intro">
      Plan a practical ride that trades a little distance for calmer streets.
      Every route keeps each segment's stress colour so the hard parts are
      visible before you leave.
    </p>
    ${routeLegend(stats, mapState)}
    <p class="route-footnote">Tap any street to see why it received its rating.</p>
    ${appNav()}`;
}

function results(state, mapState) {
  if (state.result?.kind === 'pending') {
    return `<div class="box route-working" role="status">
      <span class="spinner" aria-hidden="true"></span> Finding calm routes…
    </div>`;
  }
  if (state.result?.kind === 'error') {
    return `<div class="verdict bad"><b>The routes could not be calculated</b>
      <span>Reload the page and try again.</span></div>${appNav()}`;
  }
  if (!state.candidates?.length) {
    return `<div class="verdict bad"><b>No route found</b>
      <span>These points are not connected on the bikeable network. One may be
      on a street the map data does not contain.</span></div>${appNav()}`;
  }

  const chosen = selected(state) || state.candidates[0];
  return `
    ${preference(state, chosen)}
    <div class="route-list-head">
      <span class="eyebrow">${state.candidates.length} ${
        state.candidates.length === 1 ? 'route' : 'routes'}</span>
      <span class="grow"></span>
      <span>${escapeHtml(shortPoint(state.from))} → ${
        escapeHtml(shortPoint(state.to))}</span>
    </div>
    <div class="route-cards">
      ${state.candidates.map((candidate) =>
        routeCard(candidate, candidate.key === chosen.key)).join('')}
    </div>
    <p class="route-time-note">Ride times are distance estimates at 10 mph.
      They do not account for hills, signals, stops, or turn delay.</p>
    ${stressChips(mapState)}
    ${appNav()}`;
}

function preference(state, chosen) {
  const fastest = state.candidates.find((candidate) => candidate.key === 'fastest')
    || state.candidates[state.candidates.length - 1];
  const extraMinutes = Math.max(0, minutes(chosen.miles) - minutes(fastest.miles));
  const extraMiles = Math.max(0, chosen.miles - fastest.miles);
  const stressChange = fastest.stressMiles - chosen.stressMiles;
  const comparison = chosen.key === 'fastest'
    ? `${fmtMiles(chosen.severeMiles)} of this route is LTS 4.`
    : `Costs <b>+${extraMinutes} min</b> and <b>+${
      fmtMiles(extraMiles)}</b>, and ${
      stressChange > 0.01
        ? `cuts high-stress riding from <b>${fmtMiles(fastest.stressMiles)}</b> `
          + `to <b>${fmtMiles(chosen.stressMiles)}</b>.`
        : `uses <b>${fmtMiles(chosen.stressMiles)}</b> of LTS 3–4 streets.`
    }`;

  return `
    <section class="route-preference-section"
             aria-labelledby="route-preference-heading">
      <h2 class="eyebrow" id="route-preference-heading">How calm do you want it?</h2>
      <div class="box route-preference">
        <input class="range" type="range" min="0" max="100" step="1"
               value="${state.sliderValue ?? PRESETS[chosen.key]?.slider ?? 50}"
               data-route-slider aria-label="How calm do you want it?"
               aria-valuetext="${escapeHtml(PRESETS[chosen.key]?.label || chosen.label)}">
        <div class="range-ends"><span>Calmest</span><span>Fastest</span></div>
        <p>${comparison}</p>
      </div>
    </section>`;
}

function routeCard(candidate, active) {
  const hardPct = candidate.miles
    ? Math.round((candidate.stressMiles / candidate.miles) * 100) : 0;
  const tag = candidate.severeMiles > 0.01
    ? `${fmtMiles(candidate.severeMiles)} on LTS 4`
    : candidate.worstLts <= 2 ? 'No busy roads' : 'No LTS 4';
  return `
    <article class="rcard" aria-current="${active}">
      <button class="rcard-pick" type="button" data-route-preset="${candidate.key}">
        <span class="rcard-top">
          <span class="rcard-name">${escapeHtml(candidate.label)}</span>
          <span class="tag ${candidate.severeMiles > 0.01 ? 'tag-accent' : 'tag-accent-2'}">
            ${tag}
          </span>
          <span class="rcard-num"><b>${minutes(candidate.miles)} min</b><br>${
            fmtMiles(candidate.miles)}</span>
        </span>
        ${stressBand(candidate)}
        <span class="rcard-meta">${fmtMiles(candidate.stressMiles)} on LTS 3–4
          (${hardPct}%)${candidate.extraMinutes
            ? ` · +${candidate.extraMinutes} min` : ''}</span>
      </button>
      ${active ? `<button class="btn ghost route-detail-button" type="button"
          data-route-action="detail">See stress breakdown →</button>` : ''}
    </article>`;
}

function stressBand(candidate) {
  const segments = candidate.segments?.length
    ? candidate.segments
    : [{ lts: candidate.worstLts, miles: candidate.miles }];
  return `<span class="band" aria-hidden="true">${segments.map((segment) =>
    `<i style="flex:${Math.max(segment.miles, 0.001)};background:${
      (LTS[segment.lts] || LTS[0]).color}"></i>`).join('')}</span>`;
}

function detail(state) {
  const candidate = selected(state);
  const hardPct = candidate.miles
    ? Math.round((candidate.stressMiles / candidate.miles) * 100) : 0;
  const worst = [...(candidate.segments || [])].sort(
    (a, b) => b.lts - a.lts || b.miles - a.miles,
  )[0];
  const fastest = state.candidates.find((route) => route.key === 'fastest');
  const detourMinutes = fastest
    ? Math.max(0, minutes(candidate.miles) - minutes(fastest.miles)) : 0;
  const detourMiles = fastest ? Math.max(0, candidate.miles - fastest.miles) : 0;

  return `
    <div class="route-detail">
      <div class="route-detail-head">
        <button class="btn" type="button" data-route-action="compare">← Compare</button>
        <span class="grow"></span>
        <span class="tag ${candidate.worstLts >= 4 ? 'tag-accent' : 'tag-accent-2'}">
          ${escapeHtml(candidate.label)}
        </span>
      </div>
      <div>
        <h2>${minutes(candidate.miles)} min · ${fmtMiles(candidate.miles)}</h2>
        <p>${escapeHtml(shortPoint(state.from))} → ${
          escapeHtml(shortPoint(state.to))}</p>
        <p class="route-time-note">Time estimated at 10 mph; hills, signals,
          stops, and turn delay are not modeled.</p>
      </div>
      <div class="box">${stressProfile(candidate)}</div>
      <dl class="kv route-kv">
        <dt>High-stress riding (LTS 3–4)</dt><dd>${
          fmtMiles(candidate.stressMiles)} · ${hardPct}%</dd>
        <dt>Worst stretch</dt><dd>${escapeHtml(worst?.name || 'Unknown')} · LTS ${
          worst?.lts ?? candidate.worstLts}</dd>
        <dt>Route sections</dt><dd>${candidate.segments?.length || 1}</dd>
        <dt>Detour vs. fastest</dt><dd>${candidate.key === 'fastest'
          ? '—' : `+${detourMinutes} min · +${fmtMiles(detourMiles)}`}</dd>
      </dl>
      <div>
        <div class="eyebrow route-legs-title">Leg by leg</div>
        <div class="steps">
          ${(candidate.segments || []).map(routeStep).join('')}
        </div>
      </div>
      ${appNav()}
    </div>`;
}

function stressProfile(candidate) {
  return `
    <div class="prof">
      ${(candidate.segments || []).map((segment) => `
        <div class="prof-col" style="flex:${Math.max(segment.miles, 0.001)}"
             title="${escapeHtml(segment.name)} · LTS ${segment.lts}">
          <i style="height:${Math.max(18, segment.lts * 25)}%;background:${
            (LTS[segment.lts] || LTS[0]).color}"></i>
        </div>`).join('')}
    </div>
    <div class="prof-axis"><span>0 mi · A</span><span>stress along the ride</span>
      <span>${fmtMiles(candidate.miles)} · B</span></div>`;
}

function routeStep(segment) {
  const detail = [
    FAC_PUBLIC[segment.fac] || 'Street',
    segment.speed != null ? `${segment.speed} mph` : null,
    segment.lanes > 2 ? `${segment.lanes} lanes` : null,
  ].filter(Boolean).join(' · ');
  return `
    <div class="step">
      <span class="bar" style="background:${
        (LTS[segment.lts] || LTS[0]).color}"></span>
      <span><span class="st">${escapeHtml(segment.name)}</span><br>
        <span class="nt">${escapeHtml(detail)}</span></span>
      <span class="mi">${fmtMiles(segment.miles)}</span>
    </div>`;
}

function routeLegend(stats, mapState) {
  const totals = new Map((stats?.by_lts || []).map((row) => [row.lts, row.miles]));
  return `
    <div class="box route-legend-box">
      <div class="eyebrow">Traffic stress · mapped streets</div>
      <div class="route-legend" role="group" aria-label="Filter by traffic stress">
        ${[1, 2, 3, 4].map((lts) => {
          const info = LTS[lts];
          const on = mapState?.lts?.has(lts) !== false;
          return `<button type="button" class="route-lg-item" data-route-lts="${lts}"
              aria-pressed="${on}">
            <span class="sw" style="background:${info.color}"></span>
            <span class="nm">${escapeHtml(info.short)}</span>
            <span class="desc">${escapeHtml(info.detail)}</span>
            <span class="mi">${totals.has(lts)
              ? `${totals.get(lts).toFixed(0)} mi` : '—'}</span>
          </button>`;
        }).join('')}
      </div>
    </div>`;
}

function stressChips(mapState) {
  return `
    <div class="route-stress-chips" aria-label="Filter map stress ratings">
      ${[1, 2, 3, 4].map((lts) => `
        <button class="lg-chip" type="button" data-route-lts="${lts}"
                aria-pressed="${mapState?.lts?.has(lts) !== false}">
          <span class="sw" style="background:${LTS[lts].color}"></span>
          LTS ${lts} ${escapeHtml(LTS[lts].short)}
        </button>`).join('')}
    </div>`;
}

function appNav() {
  return `
    <nav class="route-app-nav" aria-label="More map tools">
      <button class="btn ghost" data-nav="legend">All ratings</button>
      <button class="btn ghost" data-nav="browse">Browse streets</button>
      <button class="btn ghost" data-nav="style">Map style</button>
      <button class="btn ghost" data-nav="methodology">Methodology</button>
      <button class="btn ghost" data-nav="share">Share</button>
    </nav>`;
}

function selected(state) {
  return state.candidates?.find((candidate) => candidate.key === state.selected)
    || null;
}

function shortPoint(point) {
  const name = point?.name || 'Map point';
  return name.split(',')[0].trim();
}

function routeIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="6" cy="18" r="2.5"></circle><circle cx="18" cy="6" r="2.5"></circle>
    <path d="M8.5 18H14a4 4 0 0 0 0-8H9"></path></svg>`;
}

function searchIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="10.5" cy="10.5" r="5.5"></circle><path d="m15 15 4 4"></path></svg>`;
}

function swapIcon() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M8 4 5 7l3 3M5 7h10M16 20l3-3-3-3M19 17H9"></path></svg>`;
}

let refocus = null;

const selectorFor = (el) => {
  const data = el?.dataset;
  if (!data) return null;
  if (data.routeAction) return `[data-route-action="${data.routeAction}"]`;
  if (data.routePreset) return `[data-route-preset="${data.routePreset}"]`;
  if (data.routeSlider !== undefined) return '[data-route-slider]';
  if (data.point) return `[data-point="${data.point}"]`;
  if (data.locationInput) return `[data-location-input="${data.locationInput}"]`;
  return null;
};

export function mount(root, handlers, state) {
  const remember = () => { refocus = selectorFor(document.activeElement); };

  root.querySelectorAll('[data-point]').forEach((button) => {
    button.addEventListener('click', () => {
      remember();
      handlers.onPick?.(button.dataset.point);
    });
  });

  root.querySelectorAll('[data-location-input]').forEach((input) => {
    input.addEventListener('input', () =>
      handlers.onQuery?.(input.dataset.locationInput, input.value));
  });

  root.querySelectorAll('[data-location-form]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      remember();
      const which = form.dataset.locationForm;
      const input = form.querySelector(`[data-location-input="${which}"]`);
      handlers.onSearch?.(which, input?.value || '');
    });
  });

  root.querySelectorAll('[data-location-result]').forEach((button) => {
    button.addEventListener('click', () => {
      const which = button.dataset.which;
      refocus = `[data-location-input="${which}"]`;
      const location = state.locationSearch?.[which]?.results?.[
        Number(button.dataset.index)
      ];
      if (location) handlers.onChooseLocation?.(which, location);
    });
  });

  const action = {
    find: handlers.onFind,
    swap: handlers.onSwap,
    clear: handlers.onClearBoth,
    detail: handlers.onDetail,
    compare: handlers.onCompare,
  };
  root.querySelectorAll('[data-route-action]').forEach((button) => {
    const handler = action[button.dataset.routeAction];
    if (handler) button.addEventListener('click', () => {
      remember();
      handler();
    });
  });

  root.querySelectorAll('[data-route-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      refocus = `[data-route-preset="${button.dataset.routePreset}"]`;
      handlers.onPreset?.(button.dataset.routePreset);
    });
  });

  const routeSlider = root.querySelector('[data-route-slider]');
  routeSlider?.addEventListener('input', (event) => {
    const value = Number(event.currentTarget.value);
    const key = value < 34 ? 'calmest' : value < 70 ? 'balanced' : 'fastest';
    event.currentTarget.setAttribute('aria-valuetext', PRESETS[key].label);
    handlers.onSliderPreview?.(value);
  });
  routeSlider?.addEventListener('change', (event) => {
    refocus = '[data-route-slider]';
    const value = Number(event.currentTarget.value);
    const key = value < 34 ? 'calmest' : value < 70 ? 'balanced' : 'fastest';
    handlers.onSlider?.(value, key);
  });

  root.querySelectorAll('[data-route-lts]').forEach((button) => {
    button.addEventListener('click', () => {
      const lts = Number(button.dataset.routeLts);
      const on = button.getAttribute('aria-pressed') !== 'true';
      handlers.onLts?.(lts, on);
    });
  });

  root.querySelectorAll('[data-nav]').forEach((button) => {
    button.addEventListener('click', () => handlers.onNav?.(button.dataset.nav));
  });

  const locationPending = Object.values(state?.locationSearch || {})
    .some((search) => search.status === 'pending');
  if (refocus && state?.result?.kind !== 'pending' && !locationPending) {
    const target = root.querySelector(refocus);
    refocus = null;
    target?.focus();
  }
}
