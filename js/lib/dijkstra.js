/**
 * Shortest path over the routing graph.
 *
 * A flat binary heap in typed arrays rather than a sorted array or an object
 * heap: ~33,000 heap operations per query, and per-operation allocation is what
 * would actually make this slow. Measured at a few milliseconds for the whole
 * network, which is why nothing is precomputed and why there is no web worker —
 * message-passing would cost more than the search.
 */

/**
 * Detour multipliers applied to distance.
 *
 * These are preferences, not physics: 1 mile of LTS 4 arterial is treated as
 * costing what 6 miles of quiet street costs, so the router will take a long
 * way round rather than put someone on it. LTS 0 is prohibited outright.
 */
export const PENALTY = { 0: Infinity, 1: 1.0, 2: 1.05, 3: 2.6, 4: 6.0 };

export const MODES = {
  // Default: strongly prefers comfort but will use a busy road if that is the
  // only way through, and shows the rider where it did.
  comfort: (g, e) => {
    const p = PENALTY[g.eLts[e]];
    return p === Infinity ? Infinity : g.eMi[e] * p;
  },
  // Hard filter. Returns no route at all rather than a compromised one.
  avoid: (g, e) => (g.eLts[e] >= 1 && g.eLts[e] <= 2 ? g.eMi[e] : Infinity),
  // Ignores stress entirely; the denominator for the detour factor.
  shortest: (g, e) => (g.eLts[e] === 0 ? Infinity : g.eMi[e]),
};

class Heap {
  constructor(capacity) {
    this.key = new Float64Array(capacity);
    this.val = new Int32Array(capacity);
    this.size = 0;
  }

  push(key, val) {
    let i = this.size++;
    this.key[i] = key;
    this.val[i] = val;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.key[p] <= this.key[i]) break;
      this._swap(p, i);
      i = p;
    }
  }

  pop() {
    const top = this.val[0];
    if (--this.size > 0) {
      this.key[0] = this.key[this.size];
      this.val[0] = this.val[this.size];
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let s = i;
        if (l < this.size && this.key[l] < this.key[s]) s = l;
        if (r < this.size && this.key[r] < this.key[s]) s = r;
        if (s === i) break;
        this._swap(s, i);
        i = s;
      }
    }
    return top;
  }

  _swap(a, b) {
    const k = this.key[a]; this.key[a] = this.key[b]; this.key[b] = k;
    const v = this.val[a]; this.val[a] = this.val[b]; this.val[b] = v;
  }
}

/**
 * One-to-one search.
 *
 * Returns `null` when the target is unreachable under this mode, which is a
 * real answer rather than an error: for `avoid` it means the two points are on
 * different low-stress islands.
 */
export function route(g, source, target, mode = 'comfort') {
  if (source < 0 || target < 0) return null;
  const weight = MODES[mode] || MODES.comfort;

  const dist = new Float64Array(g.n).fill(Infinity);
  const prevEdge = new Int32Array(g.n).fill(-1);
  const done = new Uint8Array(g.n);
  const heap = new Heap(g.m * 2 + 16);

  dist[source] = 0;
  heap.push(0, source);

  let settled = 0;
  while (heap.size > 0) {
    const u = heap.pop();
    if (done[u]) continue;      // lazy deletion: stale entries are skipped
    done[u] = 1;
    settled++;
    if (u === target) break;

    for (let k = g.head[u]; k < g.head[u + 1]; k++) {
      const v = g.to[k];
      if (done[v]) continue;
      const w = weight(g, g.via[k]);
      if (!isFinite(w)) continue;
      const nd = dist[u] + w;
      if (nd < dist[v]) {
        dist[v] = nd;
        prevEdge[v] = g.via[k];
        heap.push(nd, v);
      }
    }
  }

  if (!isFinite(dist[target])) return null;
  return { ...trace(g, source, target, prevEdge), cost: dist[target], settled };
}

/** Walk the predecessor edges back from the target. */
function trace(g, source, target, prevEdge) {
  const edges = [];
  const featureIds = [];
  let miles = 0;
  let stressMiles = 0;
  let worstLts = 0;
  let node = target;

  while (node !== source) {
    const e = prevEdge[node];
    if (e < 0) break;               // defensive: disconnected mid-trace
    edges.push(e);
    featureIds.push(g.eId[e]);
    miles += g.eMi[e];
    const l = g.eLts[e];
    if (l > 2) stressMiles += g.eMi[e];
    if (l > worstLts) worstLts = l;
    node = g.eu[e] === node ? g.ev[e] : g.eu[e];
  }
  edges.reverse();
  featureIds.reverse();
  return { edges, featureIds, miles, stressMiles, worstLts };
}

/**
 * Everything reachable from a source, as a distance array.
 *
 * Same single search as a point-to-point query without an early exit, so an
 * isochrone costs no more than a route. Unused by the routing UI; here because
 * it is three lines and Phase 7 would otherwise duplicate the whole function.
 */
export function reachable(g, source, mode = 'comfort', maxCost = Infinity) {
  const weight = MODES[mode] || MODES.comfort;
  const dist = new Float64Array(g.n).fill(Infinity);
  const done = new Uint8Array(g.n);
  const heap = new Heap(g.m * 2 + 16);

  dist[source] = 0;
  heap.push(0, source);
  while (heap.size > 0) {
    const u = heap.pop();
    if (done[u]) continue;
    done[u] = 1;
    if (dist[u] > maxCost) break;
    for (let k = g.head[u]; k < g.head[u + 1]; k++) {
      const v = g.to[k];
      if (done[v]) continue;
      const w = weight(g, g.via[k]);
      if (!isFinite(w)) continue;
      const nd = dist[u] + w;
      if (nd < dist[v]) { dist[v] = nd; heap.push(nd, v); }
    }
  }
  return dist;
}
