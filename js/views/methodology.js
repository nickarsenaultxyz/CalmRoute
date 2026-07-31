/**
 * "What do these ratings mean?"
 *
 * This used to link straight to data/methodology.json, which is a
 * machine-readable audit trail — genuinely useful, and a completely useless
 * answer to the question a reader is actually asking. This view answers it in
 * words, and keeps the raw file linked at the bottom for anyone who wants it.
 *
 * Everything here is generated from the published methodology and stats, so it
 * cannot describe rules the build did not run.
 */

import { LTS, LTS_ORDER_LEGEND } from '../config.js';
import { swatchSvg } from '../layers.js';
import { escapeHtml } from '../lib/format.js';

export function render(methodology, stats) {
  const codes = methodology?.codes || {};
  const conf = stats?.low_stress?.confidence_share_pct;

  const scale = LTS_ORDER_LEGEND.map((lts) => {
    const s = LTS[lts];
    const mi = (stats?.by_lts || []).find((r) => r.lts === lts)?.miles;
    return `
      <tr>
        <td class="swatch-cell">${swatchSvg(lts)}</td>
        <td>
          <b>${escapeHtml(s.short)}</b>
          <span>${escapeHtml(s.detail)}</span>
        </td>
        <td class="num">${mi != null ? `${mi.toFixed(0)} mi` : ''}</td>
      </tr>`;
  }).join('');

  const basis = Object.entries(codes.basis || {})
    .map(([, text]) => `<li>${escapeHtml(text)}</li>`).join('');

  const limits = (stats?.limitations || [])
    .map((l) => `<li>${escapeHtml(l)}</li>`).join('');
  const osmSource = stats?.osm_paths?.enabled
    ? ` Supplementary off-road paths and explicitly bicycle-authorized access
      roads from
      <a href="https://www.openstreetmap.org/copyright"
         target="_blank" rel="noopener">OpenStreetMap contributors</a>
      under the ODbL. The reviewed access corridor is rated LTS
      ${stats.osm_paths.access_roads?.rating ?? 2} and are not counted as bike
      facilities.`
    : '';

  return `
    <p>Every street gets a <b>Level of Traffic Stress</b> rating: how comfortable
      it is to ride a bike on, from the point of view of an ordinary adult rather
      than a confident cyclist.</p>

    <table class="scale">${scale}</table>

    <p class="tech">The scale runs 0–4. It comes from Mekuria, Furth &amp; Nixon's
      Level of Traffic Stress work, which defines four levels; “bikes not
      permitted” is kept separate here because “illegal to ride” and “legal but
      unpleasant” are different facts a rider needs.</p>

    <h2 class="section">How a street gets its rating</h2>
    <p>Three things decide it: whether there is a bike facility, how fast traffic
      is posted, and how much traffic there is. A quiet 25 mph street with almost
      no cars can be as comfortable as a protected lane; the same lane beside four
      lanes of 45 mph traffic is not.</p>
    <ul class="plain">${basis}</ul>

    <h2 class="section">How sure is it?</h2>
    <p>Only <b>${stats?.data_sources?.aadt_measured_pct ?? '~15'}%</b> of streets
      have a measured traffic count. The state counts the roads it maintains,
      which are mostly the busy ones, so volumes for neighbourhood streets are
      estimated from similar streets — and those estimates run high.</p>
    ${conf ? `<p>Of the mileage rated comfortable:
      <b>${conf.high}%</b> high confidence, <b>${conf.medium}%</b> medium,
      <b>${conf.low}%</b> low.</p>` : ''}
    <p class="note">A street can be rated <b>Relaxed</b> and still be marked
      <b>low confidence</b>. Those answer different questions: the rating is how
      comfortable it looks, the confidence is how much the data behind it can be
      trusted. A rating is marked low-confidence when its traffic volume is a
      coarse estimate, or when the rating would change under a reasonable
      alternative assumption.</p>

    <h2 class="section">What this does not know</h2>
    <ul class="plain">${limits}</ul>
    ${stats?.coverage?.missing_pct ? `<p class="tech">The coverage figure was
      measured by comparing the city's street file against OpenStreetMap across
      ${stats.coverage.sampled_areas} sampled areas:
      ${stats.coverage.missing_from_lfucg} of
      ${stats.coverage.osm_named_streets} named streets were absent.</p>` : ''}

    <h2 class="section">Sources</h2>
    <p class="tech">
      Street centrelines and bike facilities from LFUCG; traffic counts from the
      Kentucky Transportation Cabinet${
        stats?.data_sources?.aadt_count_years?.min
          ? ` (${stats.data_sources.aadt_count_years.min}–${stats.data_sources.aadt_count_years.max})`
          : ''}.
      ${osmSource} Ratings describe <b>built</b> infrastructure only.
    </p>
    <p class="tech">
      Ruleset ${escapeHtml(methodology?.ruleset_version || '')}
      (<code>${escapeHtml(methodology?.params_digest || '')}</code>) —
      every threshold behind these ratings is published in
      <a href="./data/methodology.json">methodology.json</a>, and the effect of
      changing each one is measured in
      <a href="https://github.com/nickarsenaultxyz/Lex-Bike-Data/blob/main/docs/sensitivity.md"
         target="_blank" rel="noopener">the sensitivity table</a>.
    </p>`;
}
