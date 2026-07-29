/**
 * Routing graph, in compressed sparse row form.
 *
 * Nothing about routing is precomputed. The alternative is an all-pairs
 * structure over 10,842 nodes — 117 million pairs — and it still could not
 * answer a question with a user-adjustable stress threshold. A Dijkstra over
 * this graph costs a couple of milliseconds, so it is cheaper to just run one.
 *
 * CSR with typed arrays rather than objects-and-arrays: no per-edge allocation,
 * so no GC pressure mid-interaction, and the adjacency of a node is a
 * contiguous slice.
 */

/** Node coordinates are Float64. Float32 loses roughly half a metre at
 *  longitude -84.5, which is enough to snap a click to the wrong street. */
export function buildGraph(raw) {
  const n = raw.nodes.length;
  const m = raw.edges.length;

  const lon = new Float64Array(n);
  const lat = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    lon[i] = raw.nodes[i][0];
    lat[i] = raw.nodes[i][1];
  }

  const eu = new Int32Array(m);
  const ev = new Int32Array(m);
  const eId = new Int32Array(m);
  const eMi = new Float32Array(m);      // distances, where f32 is plenty
  const eLts = new Uint8Array(m);

  const deg = new Int32Array(n + 1);
  for (let e = 0; e < m; e++) {
    const [u, v, id, mi, lts] = raw.edges[e];
    eu[e] = u; ev[e] = v; eId[e] = id; eMi[e] = mi; eLts[e] = lts;
    deg[u]++; deg[v]++;
  }

  // head[i]..head[i+1] is node i's slice of `to` / `via`.
  const head = new Int32Array(n + 1);
  for (let i = 0; i < n; i++) head[i + 1] = head[i] + deg[i];
  const cursor = head.slice(0, n);
  const to = new Int32Array(2 * m);
  const via = new Int32Array(2 * m);
  for (let e = 0; e < m; e++) {
    to[cursor[eu[e]]] = ev[e]; via[cursor[eu[e]]++] = e;
    to[cursor[ev[e]]] = eu[e]; via[cursor[ev[e]]++] = e;
  }

  return { n, m, head, to, via, eu, ev, eId, eMi, eLts, lon, lat,
           grid: buildGrid(lon, lat, n) };
}

/* ------------------------------------------------------------------ snapping */

const CELL = 0.002;   // ~180 m at this latitude

function key(gx, gy) { return `${gx}|${gy}`; }

function buildGrid(lon, lat, n) {
  const cells = new Map();
  for (let i = 0; i < n; i++) {
    const k = key(Math.floor(lon[i] / CELL), Math.floor(lat[i] / CELL));
    let bucket = cells.get(k);
    if (!bucket) cells.set(k, (bucket = []));
    bucket.push(i);
  }
  return cells;
}

/**
 * Nearest graph node to a point, or -1 if nothing is close.
 *
 * Searches outward in rings so a point in an empty cell still finds its
 * neighbour, and stops as soon as a ring cannot contain anything closer than
 * what has already been found.
 */
export function nearestNode(g, lng, lat, maxRings = 6) {
  const gx = Math.floor(lng / CELL);
  const gy = Math.floor(lat / CELL);
  let best = -1;
  let bestD = Infinity;

  for (let r = 0; r <= maxRings; r++) {
    for (let dx = -r; dx <= r; dx++) {
      for (let dy = -r; dy <= r; dy++) {
        // Only the perimeter of this ring; the interior was covered already.
        if (r > 0 && Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
        const bucket = g.grid.get(key(gx + dx, gy + dy));
        if (!bucket) continue;
        for (const i of bucket) {
          const a = g.lon[i] - lng;
          const b = g.lat[i] - lat;
          const d = a * a + b * b;
          if (d < bestD) { bestD = d; best = i; }
        }
      }
    }
    // A found node closer than this ring's inner edge cannot be beaten.
    if (best >= 0 && Math.sqrt(bestD) <= r * CELL) break;
  }
  return best;
}

/* -------------------------------------------------------------- components */

/**
 * Connected components under a stress ceiling, via union-find.
 *
 * Used to answer "is a comfortable route even possible" *before* running a
 * search, so the honest failure message can name both islands instead of the
 * UI just spinning and giving up.
 */
export function components(g, maxLts) {
  const parent = new Int32Array(g.n);
  for (let i = 0; i < g.n; i++) parent[i] = i;

  const find = (x) => {
    while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
    return x;
  };
  for (let e = 0; e < g.m; e++) {
    const l = g.eLts[e];
    if (l === 0 || l > maxLts) continue;
    const a = find(g.eu[e]);
    const b = find(g.ev[e]);
    if (a !== b) parent[a] = b;
  }
  const label = new Int32Array(g.n);
  for (let i = 0; i < g.n; i++) label[i] = find(i);
  return label;
}
