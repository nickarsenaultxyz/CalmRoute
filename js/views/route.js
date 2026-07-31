/**
 * Route planning.
 *
 * The interesting screen here is the failure one. Most routers, asked for a
 * comfortable route that does not exist, quietly hand back a compromised one
 * and let the rider discover the arterial themselves. This says so, names the
 * limitation, and offers the best available compromise as a separate,
 * explicitly-labelled choice.
 *
 * The same principle governs the ordinary screen. The default route trades some
 * comfort for distance, so it can put a rider on a busy road — which it states,
 * in miles, and then offers the quieter alternative with its price attached. How
 * hard to try is a slider rather than a checkbox, because it is a preference.
 */

import {
  LTS, QUIETEST_ROUTE_LEVEL, ROUTE_LEVELS,
} from '../config.js?v=20260731-field-notebook';
import { escapeHtml, miles as fmtMiles, minutes } from '../lib/format.js';

const POINT_LABEL = { from: 'Start', to: 'Destination' };

export function render(state, stats) {
  const row = (which) => {
    const p = state[which];
    const set = !!p;
    const search = state.locationSearch?.[which] || {};
    const query = search.query ?? p?.name ?? '';
    const pending = search.status === 'pending';
    const matches = (search.results || []).map((location, index) => `
      <button type="button" class="location-result"
              data-location-result data-which="${which}" data-index="${index}">
        ${escapeHtml(location.name)}
      </button>`).join('');
    return `
      <section class="location-group${set ? ' set' : ''}"
               aria-labelledby="location-label-${which}">
        <form class="location-row" data-location-form="${which}">
          <span class="pin ${which}" aria-hidden="true"></span>
          <label class="location-field" for="location-${which}">
            <b id="location-label-${which}">${POINT_LABEL[which]}</b>
            <input id="location-${which}" type="search" autocomplete="street-address"
                   enterkeyhint="search" data-location-input="${which}"
                   value="${escapeHtml(query)}"
                   placeholder="Address or place in Lexington">
          </label>
          <button class="btn location-submit" type="submit"${pending ? ' disabled' : ''}>
            ${pending ? 'Searching…' : 'Search'}
          </button>
        </form>
        <div class="location-actions">
          <button type="button" class="linklike" data-point="${which}"
                  aria-pressed="${state.picking === which}">
            ${state.picking === which ? 'Tap the map…' : 'Pick on map'}
          </button>
          ${set ? `<button type="button" class="linklike" data-clear="${which}">Clear</button>` : ''}
          ${set ? `<span>${escapeHtml(p.name || 'Map pin selected')}</span>` : ''}
        </div>
        ${search.message ? `<p class="location-status ${search.status === 'error' ? 'error' : ''}"
             role="status">${escapeHtml(search.message)}</p>` : ''}
        ${matches ? `<div class="location-results"
          aria-label="${POINT_LABEL[which]} location matches">${matches}</div>` : ''}
      </section>`;
  };

  return `
    <p class="tech">Search for two locations, or pick them on the map. The route
      balances comfort against distance — it will go a bit further to avoid a
      busy road, but not three times as far.</p>

    <div class="points">
      ${row('from')}
      ${row('to')}
    </div>
    <p class="tech geocoder-note">Address searches are sent to
      <a href="https://www.openstreetmap.org/" target="_blank" rel="noopener">OpenStreetMap</a>
      only when you press Search. Search powered by
      <a href="https://nominatim.openstreetmap.org/" target="_blank"
         rel="noopener">Nominatim</a> under its
      <a href="https://operations.osmfoundation.org/policies/nominatim/"
         target="_blank" rel="noopener">usage policy</a>.</p>

    <div class="share-actions">
      <button class="btn" data-route="swap"${state.from && state.to ? '' : ' disabled'}>Swap</button>
      <button class="btn" data-route="clear"${state.from || state.to ? '' : ' disabled'}>Clear both</button>
    </div>

    ${levelSlider(state.level)}

    <div id="route-result">${state.result ? result(state) : ''}</div>
    ${state.result && state.result.kind === 'ok' ? coverageNote(stats) : ''}`;
}

/**
 * The comfort/distance trade, as a slider.
 *
 * A checkbox could only say "quiet or nothing", which is one honest answer and
 * three missing ones. The endpoints are labelled and the current notch is spelt
 * out in text below, because a bare slider position tells a rider nothing about
 * what it will do — and `aria-valuetext` gives a screen reader the same words
 * rather than reading out "2".
 */
function levelSlider(level) {
  const current = ROUTE_LEVELS[level] || ROUTE_LEVELS[0];
  const ends = ROUTE_LEVELS.filter((l) => l.tick);
  // The quietest preference is the comfortable end, so it wears the sage tag;
  // everything below can put a rider on a busy road, so it wears the terracotta.
  const tagClass = level >= QUIETEST_ROUTE_LEVEL ? 'tag-accent-2' : 'tag-accent';
  return `
    <div class="box stress-pref">
      <div class="pref-head">
        <label class="eyebrow" for="stress-level" style="margin:0">Comfort vs. distance</label>
        <span class="grow"></span>
        <span class="tag ${tagClass}">${escapeHtml(current.label)}</span>
      </div>
      <input type="range" class="themed" id="stress-level" data-route="level"
             min="0" max="${QUIETEST_ROUTE_LEVEL}" step="1" value="${level}"
             aria-describedby="stress-hint"
             aria-valuetext="${escapeHtml(current.label)}">
      <div class="ticks" aria-hidden="true">
        ${ends.map((l) => `<span>${escapeHtml(l.tick)}</span>`).join('')}
      </div>
      <p class="tech" id="stress-hint" style="margin-top:8px">${escapeHtml(current.hint)}</p>
    </div>`;
}

/** The one caveat a rider needs when a route looks like a detour. */
function coverageNote(stats) {
  const pct = stats?.coverage?.missing_pct;
  if (!pct) return '';
  return `<p class="tech">If this looks like a detour: the city's street file is
    missing about ${pct}% of named streets, and a street that is not in the data
    cannot be routed over.</p>`;
}

/** Verdict for a completed search. */
function result(state) {
  const r = state.result;
  if (r.kind === 'pending') {
    return '<p class="tech">Working…</p>';
  }

  if (r.kind === 'none') {
    return `<div class="verdict bad">
        <b>No route found</b>
        <span>These two points are not connected on the bikeable network at all.
          One of them may be on a street the map does not cover.</span>
      </div>`;
  }

  if (r.kind === 'error') {
    return `<div class="verdict bad">
        <b>The route could not be drawn</b>
        <span>One of the map layers did not finish loading. Reload the page and
          try again.</span>
      </div>`;
  }

  if (r.kind === 'blocked') {
    return `
      <div class="verdict bad">
        <b>No route at this comfort setting</b>
        <span>The lower-stress streets between these points do not connect
          continuously. You can review the best available route below, with
          stressful portions clearly marked.</span>
      </div>
      ${r.fallback ? `
        <p class="tech">The best available route is
          <b>${fmtMiles(r.fallback.miles)}</b> and uses
          <b>${fmtMiles(r.fallback.stressMiles)}</b> of
          ${escapeHtml((LTS[r.fallback.worstLts] || LTS[4]).short.toLowerCase())}
          road. It is drawn on the map, with the stressful part in red.</p>
        <div class="share-actions">
          <button class="btn" data-route="accept">Show it anyway</button>
        </div>` : ''}`;
  }

  const comfortable = r.stressMiles < 0.01;
  return `
    <div class="verdict ${comfortable ? 'good' : 'warn'}">
      <b>${fmtMiles(r.miles)} · about ${minutes(r.miles)} min</b>
      <span>${r.detour ? `${r.detour.toFixed(1)}× longer than the direct route`
                       : 'Direct route'}</span>
    </div>
    <p class="tech">${comfortable
      ? '✓ Comfortable the whole way.'
      : `<b>${fmtMiles(r.stressMiles)}</b> of this is on
         ${escapeHtml((LTS[r.worstLts] || LTS[4]).short.toLowerCase())} road,
         drawn in red.`}</p>
    ${r.quieter ? quieterOffer(r, r.quieter) : ''}`;
}

/**
 * The escape hatch from a busy route.
 *
 * The numbers are on the button's own description because "want something
 * quieter?" without a price is a question no one can answer. The alternative has
 * already been searched for, so this never offers a route that turns out not to
 * exist.
 */
function quieterOffer(current, alt) {
  const extra = alt.miles - current.miles;
  const cost = extra > 0.05
    ? `${fmtMiles(extra)} further (${fmtMiles(alt.miles)} total)`
    : `no further (${fmtMiles(alt.miles)} total)`;

  return `
    <div class="alt">
      <b>There is a quieter way.</b>
      <span>${cost}, ${quieterGain(current, alt)}.</span>
      <div class="share-actions">
        <button class="btn primary" data-route="quieter">Use the quieter route</button>
      </div>
    </div>`;
}

/**
 * What the alternative actually buys, in the terms in which it is better.
 *
 * The interesting case is a swap of arterial for collector: total stress miles
 * go *up* while the ride genuinely improves. Saying "cuts the busy part to 2.96"
 * against a current 2.72 would read as a typo, so when the gain is arterial
 * mileage the sentence has to be about arterial mileage.
 */
function quieterGain(current, alt) {
  if (alt.stressMiles < 0.01) return 'and stays off busy roads entirely';

  const droppedArterial = alt.severeMiles < current.severeMiles - 0.01;
  if (droppedArterial && alt.severeMiles < 0.01) {
    return `and keeps you off ${fmtMiles(current.severeMiles)} of
      ${escapeHtml(LTS[4].short.toLowerCase())} road`;
  }
  if (droppedArterial) {
    return `and cuts the ${escapeHtml(LTS[4].short.toLowerCase())} part from
      ${fmtMiles(current.severeMiles)} to ${fmtMiles(alt.severeMiles)}`;
  }
  return `and cuts the busy part from ${fmtMiles(current.stressMiles)} to
    ${fmtMiles(alt.stressMiles)}`;
}

/**
 * Which control to put focus back on after the next re-render.
 *
 * Every interaction rebuilds the whole panel body, which discards the element
 * the rider was on. That was survivable when every control was a button that
 * ends an interaction, but a slider is adjusted repeatedly: one arrow-key nudge
 * dropped focus to the document, and the second nudge went nowhere. So remember
 * the control by selector — the new DOM has a different element for it — and
 * restore focus once the search has settled.
 */
let refocus = null;

const selectorFor = (el) => {
  const d = el && el.dataset;
  if (!d) return null;
  if (d.route) return `[data-route="${d.route}"]`;
  if (d.point) return `[data-point="${d.point}"]`;
  if (d.locationInput) return `[data-location-input="${d.locationInput}"]`;
  return null;
};

export function mount(root, handlers, state) {
  const remember = () => { refocus = selectorFor(document.activeElement); };

  root.querySelectorAll('[data-point]').forEach((btn) => {
    btn.addEventListener('click', () => {
      remember();
      handlers.onPick(btn.dataset.point);
    });
  });

  root.querySelectorAll('[data-clear]').forEach((btn) => {
    btn.addEventListener('click', () => handlers.onClear(btn.dataset.clear));
  });

  root.querySelectorAll('[data-location-input]').forEach((input) => {
    input.addEventListener('input', () => {
      handlers.onQuery?.(input.dataset.locationInput, input.value);
    });
  });

  root.querySelectorAll('[data-location-form]').forEach((form) => {
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      remember();
      const which = form.dataset.locationForm;
      const input = form.querySelector(`[data-location-input="${which}"]`);
      handlers.onSearch?.(which, input?.value || '');
    });
  });

  root.querySelectorAll('[data-location-result]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const which = btn.dataset.which;
      refocus = `[data-location-input="${which}"]`;
      const location = state.locationSearch?.[which]?.results?.[Number(btn.dataset.index)];
      if (location) handlers.onChooseLocation?.(which, location);
    });
  });

  const act = {
    swap: handlers.onSwap,
    clear: handlers.onClearBoth,
    accept: handlers.onAccept,
    quieter: handlers.onQuieter,
  };
  root.querySelectorAll('[data-route]').forEach((el) => {
    if (el.dataset.route === 'level') {
      // `change`, not `input`: dragging across four notches on the way to the
      // one you want would otherwise fire three searches and three re-renders,
      // rebuilding the slider out from under the pointer mid-drag.
      el.addEventListener('change', () => {
        remember();
        handlers.onLevel(Number(el.value));
      });
    } else if (act[el.dataset.route]) {
      el.addEventListener('click', () => { remember(); act[el.dataset.route](); });
    }
  });

  root.querySelectorAll('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => handlers.onNav?.(btn.dataset.nav));
  });

  // Wait for the settled render: restoring focus on the "Working…" pass would
  // consume the selector one render too early and lose it again straight after.
  const locationPending = Object.values(state?.locationSearch || {})
    .some((search) => search.status === 'pending');
  if (refocus && state?.result?.kind !== 'pending' && !locationPending) {
    const target = root.querySelector(refocus);
    refocus = null;
    target?.focus();
  }
}
