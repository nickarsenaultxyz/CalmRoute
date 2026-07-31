# Lexington Bike Stress

How comfortable is each street in Lexington, Kentucky to ride a bike on?

This computes a **Level of Traffic Stress (LTS)** rating for all 1,776 miles of
Lexington's street network — not just the streets that already have bike
infrastructure — and analyses how well the low-stress parts connect to each
other.

Live map: <https://nickarsenaultxyz.github.io/Lex-Bike-Data/>

---

## The finding

> **About 1,248 miles are ridable for a confident rider, in one nearly-whole
> network. But the 947 miles that are comfortable for an ordinary adult are
> shattered into 936 disconnected islands, and the largest holds only 19% of
> them.**

Lexington's problem is not a shortage of quiet streets. It is that the quiet
streets do not join up: to get between almost any two neighbourhoods you have to
cross something stressful.

Both halves of that sentence matter, and they are quoted together deliberately.
The island count is a **methodology choice, not a measurement** — it swings by a
factor of two depending on how you rate a 35 mph collector. The LTS ≤ 3 mileage
does not move at all across every variant tested. See
[docs/sensitivity.md](docs/sensitivity.md), which is generated, not asserted.

## The ratings

| LTS | Label | Who it is for | Miles |
|----:|---|---|----:|
| 1 | Relaxed | Kids and new riders | 244.7 |
| 2 | Comfortable for most adults | Quiet streets and bike lanes | 702.1 |
| 3 | Busy | Confident riders | 301.1 |
| 4 | Stressful | Experienced riders only | 396.9 |
| 0 | Bikes not permitted | Interstates and parkways | 130.9 |

The scale is 0–4. **There is no LTS 5.** Furth and Mekuria define 1–4, and an
earlier version of this project used "LTS 5" for two different things — roads
where cycling is illegal, and roads that are merely unpleasant. Those are
distinct facts a rider needs, so they are now `0` and `4`.

## How honest is it?

Only **about 13% of published segments have a measured traffic count**. KYTC counts
state-maintained routes, so the pipeline uses a route-group validated histogram
gradient-boosting model only for represented road classes and feature values.
Neighbourhood streets remain on the transparent class-median fallback rather
than letting the model learn KYTC's sampling bias.

The safety-facing estimate is the conditional 75th percentile, not the raw
median. On held-out routes in represented road classes, it improves RMSLE from
1.047 to 0.705, median absolute percentage error from 42.2% to 39.6%, and the
LTS-relevant AADT-bin accuracy from 79.1% to 81.3%. More importantly, dangerous
low-bin errors fall to 3.1%, versus 6.4% for the model median. Repeated segments
sharing a count station are weighted as one observation during training.

Every segment therefore ships with:

- **`basis`** — whether its rating rests on facility type alone, on type and
  posted speed, or on type, speed and a real traffic count.
- **`cf`** — confidence. A segment is demoted to *low* if its volume is a coarse
  fallback **or** if its rating flips under any variant in the sensitivity sweep.
  That second condition makes "uncertain" mechanical rather than asserted.

Of low-stress mileage: **6.5% high confidence, 74.1% medium, 19.4% low.**

Known limitations, also published in `data/methodology.json`:

- Lane counts are in no source; they are inferred from road class, one-way
  status and cartographic class, and shipped as a property so you can audit it.
- On-street parking and bike lane width are not modelled — neither is recorded.
- Ratings describe **built** infrastructure only. Funded projects are separate.
- **LFUCG's street file is missing about 6% of named streets** — 22 of 375
  across three sampled areas, measured against OpenStreetMap. A street that is
  not in the data cannot be rated or routed over, so a route may detour around
  one that exists. Not filled from OSM: imported geometry would carry no posted
  speed or road class, so assuming a comfortable rating invents one and assuming
  a stressful one leaves the router avoiding it anyway.

## Usage

```bash
pip install -r requirements.txt

make build         # compute ratings, write data/
make validate      # golden corridors + aggregate stability
make sensitivity   # parameter sweep -> docs/sensitivity.md
make stats         # print the current build's figures
make test          # run the test suite
make serve         # serve the map at http://localhost:8000/
```

Or `python -m lexbike build --help`. Any threshold can be overridden for a
one-off run:

```bash
python -m lexbike build --set lts.mixed.speed_35_lts=2 --out data/experiment
```

## How it works

**`params.toml` is the single source of truth.** Every threshold lives there,
and it is serialized into `data/methodology.json` at build time, so the
published methodology cannot drift from the rules that produced the numbers.
Each build stamps a digest of the effective ruleset into `stats.json`, so any
screenshot can be traced back to the rules behind it.

```
lexbike/
  io.py         load and validate the LFUCG and KYTC sources
  conflate.py   attach bike facilities to street centrelines
  lts.py        the classifier — pure functions, no I/O, no pandas
  network.py    graph, low-stress islands, barrier ranking
  export.py     the artifacts the map fetches
  pipeline.py   orchestration
```

### The central design decision

**The LFUCG centreline layer is the one canonical network.** On-road bike
facilities become *attributes* of a centreline; only off-road paths keep their
own geometry, joined to the streets by explicit connector edges that are drawn
on the map so you can see and argue with each one.

This matters because the two source layers are independently digitized and
share almost no nodes — only 241 of the bike layer's 1,056 endpoints land within
a metre of a centreline endpoint. Treating them as one network required
reconciliation filters that silently deleted 2,417 street segments. Making
centrelines canonical removes that entire class of problem.

### Inputs

| File | Source | Contents |
|---|---|---|
| `lex_street_data.geojson` | LFUCG | 13,775 street centrelines |
| `Bicycle_Network_Master.geojson` | LFUCG | 542 bike facility segments |
| `StaList_Fayette (1).csv` | KYTC | 546 traffic count stations |
| Live Overpass query | OpenStreetMap | County-scoped cycleways, bicycle-designated paths, UK campus walkways, and narrowly reviewed street exceptions |

A scheduled GitHub Actions build downloads the current
[LFUCG Bicycle Network Public View](https://www.arcgis.com/home/item.html?id=90961d8f5c854453abf4123d4a99e139)
each day before running the pipeline. The download count is checked against the
FeatureServer count, and the normal schema, classification, regression and size
checks must all pass before the map is deployed. If LFUCG is unavailable or
publishes a breaking schema change, the build fails and the last good map stays
online.

The same build queries the
[Fayette County OSM relation](https://www.openstreetmap.org/relation/130537)
for unambiguous off-road cycling paths and service roads carrying explicit
bicycle permission. Parking aisles, private access and one-way service roads are
excluded. Geometry already represented by LFUCG is removed by an overlap check;
every remaining OSM segment is marked in the download and attributed on the
map. In addition, every non-private walking path inside the exact
[University of Kentucky campus relation](https://www.openstreetmap.org/relation/4815526)
is imported as LTS 1 because bicycles are permitted on those campus paths;
explicit `bicycle=no` paths remain excluded. Untagged footways elsewhere in the
county are not imported. Calm and balanced routing treats campus walkways as
secondary links with a 1.20× cost factor, so parallel purpose-built bicycle
infrastructure remains preferred while useful campus cut-throughs still work;
the Fastest option remains literal shortest distance. The reviewed Baptist
Health access corridor is rated
LTS 2 and does not count as a bike facility. Commonwealth Drive, which is
missing from LFUCG, is a separate locally reviewed exception rated LTS 1; its
short University Court approach is included so the west end joins the existing
graph. Neither is presented or counted as a bike facility. A failed, empty, or
unexpectedly large OSM response also stops deployment instead of silently
reverting the public map to LFUCG-only data.

A note on the bike layer, because it is easy to misread: `Type_Facility` is the
facility that physically exists. `AltType_Facility` is a *recommended upgrade*,
not infrastructure — 37 segments on Redding Rd are `Type = Preferred Route` with
`AltType = Bicycle Lane`, and their `Name_Facility` reads
`EXISTING PREFERRED ROUTE`. Only `Type_Facility` feeds a rating; `AltType` feeds
the "what if we built it" scenario.

Lexington has **91.1 miles of on-road bike treatment** (53.0 bike lane, 19.1
shoulder, 13.3 buffered lane, 5.7 sharrow). The existing bike layer spans 221.7
on-road miles, but the other 130.6 are `Preferred Route` — signed wayfinding
with no physical treatment, which earns no rating credit.

### Outputs

Written to `data/` (gitignored — regenerate with `make build`):

Three of these are map layers, split by the role each plays in answering "can I
ride here" — which is also the order they are needed in:

| File | Gzipped | Purpose |
|---|--:|---|
| `network.geojson` | 103 KB | built bike facilities and trails, loaded first |
| `context.geojson` | 164 KB | busy and prohibited roads, fetched right after |
| `residential.geojson` | 367 KB | quiet streets, shown and fetched by default |
| `graph.json` | 218 KB | 11,941 nodes, for client-side routing |
| `gaps.json` / `gaps.geojson` | 11 KB | 317 ranked barrier crossings |
| `islands.json` | 5 KB | connected low-stress components |
| `stats.json`, `methodology.json`, `manifest.json`, `planned.geojson`, `council.json` | < 2 KB each | |

## Using it without a mouse

Map features are drawn on a GPU canvas: they are not DOM nodes, cannot take
focus, and cannot be reached by a screen reader. Rather than scattering ARIA
over a canvas and calling it done, the map offers a **parallel non-map path**
that does everything the map does.

- **Browse streets as a list** — the first focusable control on the page is a
  skip link that opens it. Type a street name, arrow down into the results,
  press enter. Selecting there does exactly what clicking the map does.
- **Escape** returns to the overview from any view; the panel's back button and
  the browser back button are the same gesture.
- Every map action is announced through a live region.
- LTS is encoded by **dash pattern and line width as well as colour**, so the
  ratings stay distinguishable without colour vision. The legend swatches draw
  the same patterns the map does.
- **Map style → None** removes the basemap entirely, which is the strongest
  contrast available: the ratings have to stay distinguishable from each other
  as well as from the background.
- Pinch-zoom works. The previous version set `user-scalable=no` on a map.

## Sharing

A bare link is not much use for advocacy, so every share composes the
**statistic alongside the URL** — a pasted link carries its own argument:

> Lexington has 946.9 miles of streets comfortable for an ordinary adult to bike
> on — but they are split into 936 disconnected islands, and the largest holds
> only 18.7% of them.

The link restores exactly what you were looking at: location, zoom, which
ratings are shown, and any selected street. The text is generated from the live
build, so it cannot drift from what the map currently says.

### Contacting a council member

Select a street and the map shows **the council member who actually represents
it**, with a one-tap email link. Which member that is depends on where the
street is, so a single generic address would send most messages to the wrong
person — working that out is the part the map can usefully do.

The message itself is left blank on purpose. What to say is yours, and a note
in a constituent's own words carries more weight than an obvious form letter.

The roster is read from
[LFUCG's published council district layer](https://data.lexingtonky.gov/datasets/lfucg::council-district)
on every build rather than copied into this repository. Council members change
with elections, and a hardcoded address keeps working long after it has become
wrong — quietly sending constituent mail to someone who no longer holds the
seat. Reading the city's own directory means the map is as current as the city
is.

Nothing is sent automatically — it opens a new message in your mail app. If the
district layer is unavailable at build time, or a street falls outside the
district boundaries (19 of 14,169 do), the map says so and links to the
council's contact page rather than guessing.

## Planning a route

Pick two points and the map routes you over quiet streets, going a little
further to avoid busy ones. It reports the trade honestly: total distance, how
much longer that is than the direct line, and how much of it is still on a busy
road — drawn in red so you see the compromise before you set off.

**How much you mind busy roads is a slider**, because it is a preference and not
a fact. It has four notches, from *most direct* to *quiet streets only*, and the
default is **balanced**. Balanced is measured rather than asserted: over 120
trips of 1.5–8 direct miles, it adds a median 15% to the distance and cuts the
median mileage on an arterial from 0.80 to 0.05 miles. The full table, and why
keeping *busy* cheap is what makes avoiding *stressful* affordable, is in
`js/config.js`.

The previous single setting was what is now one notch quieter, and at a median
29% detour it was the kind of route a rider looks at once and then ignores.

When the route it picks still uses a busy road, it says so — and, having already
searched for the alternative, offers it with the price attached:

> **There is a quieter way.** 0.75 mi further (5.01 mi total), and cuts the busy
> part to 1.59 mi.

Drag the slider to **quiet streets only** and it will refuse rather than
compromise. That refusal is the most useful screen in the tool:

> There's no comfortable route between these two places. Your start and
> destination sit on different low-stress islands (#0 and #1). The quiet
> streets are there; they just don't join up.
>
> The best available route is 4.06 mi and uses 0.38 mi of busy road.

All of it runs in the browser. Nothing about routing is precomputed — an
all-pairs structure over 10,842 nodes would be 117 million pairs and still could
not answer a question with an adjustable stress threshold. A Dijkstra over the
whole network takes **under a millisecond**, so it is cheaper to just run one:
graph build 7 ms, query 0.2–0.8 ms, measured on the real network.

## Where the gaps are

`make build` ranks every high-stress segment whose two ends sit on *different*
low-stress islands — treat it and the islands merge. The top crossings are
W Second St, E High St, Leestown Rd, Price Rd, Keithshire Way and Iron Works
Pike.

One caveat the data carries explicitly: several streets often cross between the
same pair of islands, so they are **alternatives, not additive wins**. Fixing
any one merges that pair. `best_for_pair` marks the cheapest option and
`alternatives` counts the rest, so their "miles unlocked" can never be summed by
mistake.

## Validation

Three checks, each catching what the others miss:

- **Golden corridors** (`tests/golden_corridors.csv`) pin specific real segments
  a person has looked at and judged. Catches a rule change that is wrong in a
  way the totals hide. *Currently a template — `expected_lts` is unlabelled, and
  `make validate` says so loudly.*
- **Aggregate stability** (`tests/baseline_stats.json`) pins the headline
  figures within tolerance. Catches a change that is wrong everywhere at once,
  which a handful of hand-picked corridors would miss.
- **Sensitivity sweep** asks a different question: not "did this change?" but
  "how much would it change if a judgement call went the other way?"

The build also fails rather than publishing when conflation quality drops
(recall below 90%, or inflation above 1.45×), when an artifact exceeds its size
budget, or when the source data contains a facility type or road class the rules
do not cover.

## Method

Mekuria, Furth & Nixon (2012), *Low-Stress Bicycling and Network Connectivity*,
Mineta Transportation Institute. Furth, Mekuria & Nixon (2016), *Network
Connectivity for Low-Stress Bicycling*, TRR 2587.

Departures from the source method, all forced by what Lexington's data actually
contains, are listed in `data/methodology.json`.
