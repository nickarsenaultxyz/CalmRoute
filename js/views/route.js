/**
 * Route planning.
 *
 * TODO(phase-5): the blocked-route verdict should offer "see what would fix
 * this", linking to the ranked gap crossings. The data is already published in
 * gaps.json; the view is not built, and a button that silently lands on the
 * overview is worse than no button.
 *
 * The interesting screen here is the failure one. Most routers, asked for a
 * comfortable route that does not exist, quietly hand back a compromised one
 * and let the rider discover the arterial themselves. This says so, names the
 * two islands, and offers the best available compromise as a separate,
 * explicitly-labelled choice.
 */

import { LTS } from '../config.js';
import { escapeHtml, miles as fmtMiles, minutes } from '../lib/format.js';

const POINT_LABEL = { from: 'Start', to: 'Destination' };

export function render(state, stats) {
  const row = (which) => {
    const p = state[which];
    const set = !!p;
    return `
      <button class="point-row${set ? ' set' : ''}" data-point="${which}"
              aria-pressed="${state.picking === which}">
        <span class="pin ${which}" aria-hidden="true"></span>
        <span class="label">
          <b>${POINT_LABEL[which]}</b>
          <span>${set ? escapeHtml(p.name || 'Dropped pin')
                      : (state.picking === which ? 'Tap the map…' : 'Not set')}</span>
        </span>
        ${set ? '<span class="clear" data-clear="' + which + '">Clear</span>' : ''}
      </button>`;
  };

  return `
    <p class="tech">Pick two points on the map. The route prefers quiet streets
      even when that means going further.</p>

    <div class="points">
      ${row('from')}
      ${row('to')}
    </div>

    <div class="share-actions">
      <button class="btn" data-route="swap"${state.from && state.to ? '' : ' disabled'}>Swap</button>
      <button class="btn" data-route="clear"${state.from || state.to ? '' : ' disabled'}>Clear both</button>
    </div>

    <label class="check">
      <input type="checkbox" data-route="strict"${state.strict ? ' checked' : ''}>
      <span>Only quiet streets — no route rather than a stressful one</span>
    </label>

    <div id="route-result">${state.result ? result(state) : ''}</div>
    ${state.result && state.result.kind === 'ok' ? coverageNote(stats) : ''}`;
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

  if (r.kind === 'blocked') {
    // The screen this whole feature exists for.
    const a = r.islandA;
    const b = r.islandB;
    return `
      <div class="verdict bad">
        <b>There's no comfortable route between these two places.</b>
        <span>Your start and destination sit on different low-stress islands${
          a != null && b != null ? ` (#${a} and #${b})` : ''}. The quiet streets
          are there; they just don't join up.</span>
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
         drawn in red.`}</p>`;
}

export function mount(root, handlers) {
  root.querySelectorAll('[data-point]').forEach((btn) => {
    btn.addEventListener('click', (ev) => {
      if (ev.target.dataset.clear) {
        handlers.onClear(ev.target.dataset.clear);
        return;
      }
      handlers.onPick(btn.dataset.point);
    });
  });

  const act = {
    swap: handlers.onSwap,
    clear: handlers.onClearBoth,
    accept: handlers.onAccept,
  };
  root.querySelectorAll('[data-route]').forEach((el) => {
    if (el.dataset.route === 'strict') {
      el.addEventListener('change', () => handlers.onStrict(el.checked));
    } else if (act[el.dataset.route]) {
      el.addEventListener('click', () => act[el.dataset.route]());
    }
  });

  root.querySelectorAll('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => handlers.onNav?.(btn.dataset.nav));
  });
}
