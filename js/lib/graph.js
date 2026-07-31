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
  const campusParallelBikeFactor = raw.campus_parallel_bike_factor ?? 1.50;

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
  const eCampusParallelBike = new Uint8Array(m);

  const deg = new Int32Array(n + 1);
  for (let e = 0; e < m; e++) {
    const [u, v, id, mi, lts, campusParallelBike = 0] = raw.edges[e];
    eu[e] = u; ev[e] = v; eId[e] = id; eMi[e] = mi; eLts[e] = lts;
    eCampusParallelBike[e] = campusParallelBike;
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

  return { n, m, head, to, via, eu, ev, eId, eMi, eLts,
           eCampusParallelBike, campusParallelBikeFactor, lon, lat,
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
 * Used to distinguish a route blocked by the selected comfort ceiling from
 * points that are not connected in the routable graph.
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


/* ------------------------------------------------------- snapping to a line */

/** Squared distance from p to segment ab, plus the closest point. */
function projectOnSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  const ex = px - cx;
  const ey = py - cy;
  return { d2: ex * ex + ey * ey, x: cx, y: cy, t };
}

/**
 * Nearest point on the network *geometry*, not the nearest node.
 *
 * Snapping to a node puts the start of a route wherever the nearest junction
 * happens to be -- on a long trail stretch that can be hundreds of feet from
 * where someone actually clicked, and the drawn route visibly fails to reach
 * their pin. Snapping to the line means the route starts where they pointed.
 *
 * Returns the edge, the point on it, how far along it that is, and the two
 * nodes it runs between, so the caller can route from whichever end is better.
 */
export function snapToNetwork(g, geometryOf, lng, lat, maxRings = 6) {
  const gx = Math.floor(lng / CELL);
  const gy = Math.floor(lat / CELL);
  const tried = new Set();
  let best = null;

  for (let r = 0; r <= maxRings; r++) {
    for (let dx = -r; dx <= r; dx++) {
      for (let dy = -r; dy <= r; dy++) {
        if (r > 0 && Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
        const bucket = g.grid.get(key(gx + dx, gy + dy));
        if (!bucket) continue;

        for (const node of bucket) {
          for (let k = g.head[node]; k < g.head[node + 1]; k++) {
            const e = g.via[k];
            if (tried.has(e)) continue;
            tried.add(e);

            const coords = geometryOf(g.eId[e]);
            if (!coords || coords.length < 2) continue;

            // Walk the polyline; track distance along so the edge can be split.
            let along = 0;
            for (let i = 0; i < coords.length - 1; i++) {
              const [ax, ay] = coords[i];
              const [bx, by] = coords[i + 1];
              const seg = projectOnSegment(lng, lat, ax, ay, bx, by);
              if (!best || seg.d2 < best.d2) {
                const segLen = Math.hypot(bx - ax, by - ay);
                best = {
                  d2: seg.d2, edge: e, x: seg.x, y: seg.y,
                  vertexIndex: i, t: seg.t,
                  alongDeg: along + segLen * seg.t,
                  u: g.eu[e], v: g.ev[e], coords,
                };
              }
              along += Math.hypot(bx - ax, by - ay);
            }
            if (best) best.totalDeg = along;
          }
        }
      }
    }
    // Nothing in a further ring can beat a hit closer than this ring's edge.
    if (best && Math.sqrt(best.d2) <= r * CELL) break;
  }

  if (!best) return null;
  // Recompute total length for the winning edge (the loop above may have
  // overwritten totalDeg while scanning a different edge).
  let total = 0;
  for (let i = 0; i < best.coords.length - 1; i++) {
    total += Math.hypot(best.coords[i + 1][0] - best.coords[i][0],
                        best.coords[i + 1][1] - best.coords[i][1]);
  }
  best.totalDeg = total;
  best.fraction = total > 0 ? best.alongDeg / total : 0;
  return best;
}

/** The piece of an edge's geometry between a snap point and one of its ends. */
export function clipToEnd(snap, toEnd) {
  const { coords, vertexIndex, x, y } = snap;
  if (toEnd) {
    return [[x, y], ...coords.slice(vertexIndex + 1)];
  }
  return [...coords.slice(0, vertexIndex + 1), [x, y]];
}
