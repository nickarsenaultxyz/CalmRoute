/**
 * Segment detail.
 *
 * Designed around the fact that most segments have no measured traffic count.
 * The previous map rendered an empty table cell for those, which reads as "no
 * traffic" rather than "not measured". Here an unknown is always labelled, and
 * the `basis` chip says in plain language what the rating actually rests on.
 *
 * The plain-language rating is primary and the numeric LTS is a small secondary
 * label, because the audience is the public. Planners lose nothing: the
 * technical value and a methodology link are both still present.
 */

import {
  BASIS_MEASURED,
  CONFIDENCE,
  FAC_PUBLIC,
  KIND_PUBLIC,
  LTS,
  ROAD_CLASS_PUBLIC,
} from '../config.js?v=20260731-field-notebook';
import { escapeHtml, miles, speed, traffic } from '../lib/format.js';

const BASIS_NOTE = {
  0: 'Rated from the type of path alone — traffic on nearby roads does not affect it.',
  1: 'Rated from the street type and posted speed. No traffic count is available '
     + 'for this street, so the volume is estimated from similar streets.',
  2: 'Rated from the street type, posted speed and a measured traffic count.',
};

const MODEL_BASIS_NOTE =
  'Rated from the street type and posted speed. No traffic count is available '
  + 'for this street, so the volume is a conservative estimate from a model '
  + 'validated on held-out routes.';

function fact(label, value, unknownText) {
  if (value == null || value === '') {
    return `<dt>${label}</dt><dd class="unknown">${unknownText}</dd>`;
  }
  return `<dt>${label}</dt><dd>${escapeHtml(value)}</dd>`;
}

export function render(props, { stats, aadtYear, council } = {}) {
  const lts = LTS[props.lts] || LTS[4];
  const name = props.nm || 'Unnamed street';
  const conf = CONFIDENCE[props.cf ?? 1];
  const basis = props.am === 1
    ? MODEL_BASIS_NOTE
    : BASIS_NOTE[props.basis ?? 1];

  const facility = FAC_PUBLIC[props.fac ?? 0];
  const kind = KIND_PUBLIC[props.kind ?? 0];
  const descriptor = props.fac ? facility : `${kind}, no bike facility`;

  const tagClass = props.lts >= 3 ? 'tag-accent'
    : props.lts === 0 ? 'tag-neutral' : 'tag-accent-2';

  return `
    <div class="box">
      <div class="box-head">
        <span class="tag ${tagClass}">LTS ${props.lts}</span>
        <span class="grow"></span>
        <span class="tag tag-neutral">${escapeHtml(conf.label)} confidence</span>
      </div>
      <div class="rating">
        <span class="dot" style="background:${lts.color}"></span>
        <span>
          <b>${escapeHtml(lts.short)}</b>
          <span>${escapeHtml(descriptor)}</span>
        </span>
      </div>

      <dl class="kv">
        ${fact('Posted speed', speed(props.sp), 'Not on record')}
        ${fact('Traffic', traffic(props.ad, {
          year: aadtYear,
          measured: props.basis === BASIS_MEASURED,
        }), 'Not available')}
        ${props.kind === 0
          ? fact('Road type', ROAD_CLASS_PUBLIC[props.rc], 'Not on record')
          : ''}
        ${props.kind === 0
          ? fact(
            'Through lanes',
            props.ln == null ? null : `${props.ln} (estimated)`,
            'Not available',
          )
          : ''}
        ${fact('Length', miles(props.mi), '—')}
        ${props.src === 'osm'
          ? fact(
            props.osm_role === 'reviewed_street'
              ? 'Reviewed-street data'
              : props.osm_role === 'campus_path'
                ? 'UK academic-core walkway data'
              : props.fac ? 'Path data' : 'Access-road data',
            'OpenStreetMap contributors (ODbL)',
            '—',
          )
          : ''}
        ${councilRow(props, council)}
      </dl>
    </div>

    <p class="note">${escapeHtml(basis)}</p>

    <nav class="panel-nav" aria-label="Actions for this street">
      <button class="btn primary" data-nav="route">Plan a route</button>
      <button class="btn" data-nav="share">Share, or contact your council member</button>
    </nav>

    <p class="tech">
      LTS ${props.lts} · ${escapeHtml(conf.label)} confidence ·
      <button class="linklike" data-nav="methodology">what do these ratings mean?</button>
    </p>`;
}

/** Which council member represents this street. Shown here because it is a
 *  fact about the street, and because it makes the email action's recipient
 *  visible before the user commits to opening a draft. */
function councilRow(props, council) {
  if (props.cd == null) return '';
  const rep = council?.districts?.[String(props.cd)];
  const who = rep?.name ? ` — ${rep.name}` : '';
  return `<dt>Council district</dt><dd>${props.cd}${escapeHtml(who)}</dd>`;
}

/** Compact hover preview: name and rating only, no DOM churn beyond this. */
export function hoverHtml(props) {
  const lts = LTS[props.lts] || LTS[4];
  return `<b>${escapeHtml(props.nm || 'Unnamed street')}</b>
          <span>${escapeHtml(lts.short)}</span>`;
}
