# Sailwind island charts - checkpoint

Nautical-style bathymetry charts for every island of *Sailwind*, generated straight from the game's Unity data.
Each chart is a top-down map (north up, island-local metres) with depth bands and contours (1 m / 5 m down to the
10 m shelf edge), land contours every 10 m, hillshading, and docks / quays / walls / buildings as red vector
footprints, with dock poles drawn as hazard markers.

This folder is a **checkpoint**: everything needed to reproduce the results and to extend them (new islands, game
updates) without re-deriving what we learned.

| File | Purpose |
|---|---|
| `DATA_LAYOUT.md` | **Start here.** How the raw Unity data is organised and the traps in it (esp. §4: Terrain ignores rotation/scale). |
| `sailwind_charts.py` | The whole pipeline in one file: discovery, geometry collection, rasterising, rendering, regression test, exploration tools. |
| `ISLAND_NOTES.md` | Per-island status: what was validated, quirks, open questions. |
| `requirements.txt`, `setup_env.sh` | Pinned dependencies and a one-shot venv setup (`contourpy` comes with matplotlib). |
| `charts5k/` | Output of the last full run: all 50 islands at 5000 px (`--scenery`, display titles, JPEG q90, 112 MB), `Lagoon.jpg` composite and `_overview.jpg`. Regenerable; delete freely. |
| `reference/` | Golden files: Albacore reference chart + numeric fingerprint (selftest), island manifest, overview sheet of all 50 charts. |

## Quick start

```bash
./setup_env.sh                                   # creates ./venv with the pinned packages (Python 3.12)
PY=./venv/bin/python
$PY -I sailwind_charts.py selftest               # must print SELFTEST PASS
$PY -I sailwind_charts.py list                   # the 50 island objects found
$PY -I sailwind_charts.py render "island 4 A (fish island)" --out charts --display-titles
$PY -I sailwind_charts.py render --all --out charts --no-key --jobs 6      # all islands, ~90 s on 6 workers
$PY -I sailwind_charts.py sheet charts --out overview.jpg                  # contact sheet for review
```

- The game folder defaults to `/tmp/Sailwind/Sailwind_Data`; override with `--game-data PATH` or `SAILWIND_DATA`.
- Run with `-I` (isolated mode): the game files are treated as untrusted data and Python must not pick up stray modules.
- `render` options: `--size` (long side in px, default 2000), `--quality` (JPEG, default 80), `--no-key`,
  `--title "..."`, `--display-titles` (use the names we know players see, e.g. "Albacore Island", "Gold Rock City"),
  `--jobs N`, `--dump-grid DIR` (save the height grids as .npz for analysis), `--max-cells N`,
  `--scenery` (see below).
- Titles default to the **developer object name** (e.g. `island 4 A (fish island)`), which is not what players see.

### `--scenery`: fuller town maps
`render ... --scenery` also uses the scenery scenes' baked **mesh combiners** (DATA_LAYOUT.md section 2.2): complete building
outlines plus fences/walls and furniture (red), **trees** (dark green) and **bushes/plants** (light green) as extra layers, and
baked **rocks** merged into the ground. Files get a `_scenery` suffix, so both versions can sit side by side. Categories are
decided by combiner material name (`scenery_category`, regexes at the top of the script; 35 material names exist, items/carts/
ships/fruit are ignored). On Neverdin it adds ~28% structure area (fences, small buildings) and adds nothing at sea. Default
renders (and `selftest`) are unchanged. Footprints are traced from a rasterised mask (<= 0.5 m cells) with contourpy, not a triangle union (that took ~40 s per island); thin fences come out about one cell wide. Looks right on Neverdin; **not yet reviewed on every island**.

### `compose`: several islands as one map
`compose NAME... --title T [--scenery] [--size 5000] [--res 1.0]` renders islands that belong together in their **true relative
positions** (world position of each island parent; islands are world-aligned, so only a translation is needed). Heights combine by
maximum, polygons are unioned. Example (the Lagoon ring: parent terrain, Temple, Shipyard, Senna, Onna, Fisherman; ~65 s):
`compose "island 26 lagoon parent terrain (-1)" "island 26 Lagoon Temple" "island 27 Lagoon Shipyard" "island 28 Lagoon Senna" "island 29 Lagoon Onna" "island 31 Lagoon Fisherman" --title Lagoon --scenery`.
Data check: all six sit within ~4 km (x 24.7-28.4 km, z -71.1..-69.4 km), scale 1, yaw 0 (the parent terrain yaw 180); Rock of Despair is 40 km away and not part of it.

### `--mods`: islands added by BepInEx mods
`--mods` (a global flag, before the command) also scans asset bundles under `BepInEx/plugins/**` for island prefabs and their
scenery scenes. Currently that finds **`island 67 Bottleneck`** from the *Better Ports* mod (`betterports` = island prefab with
a Terrain, `betterportsscenery` = scene `island 67 scenery`); `CustomIslandAPI.dll` is only the loader framework and adds no
island. Example: `python -I sailwind_charts.py --mods render "island 67 Bottleneck" --out charts --scenery`
(`reference/island_67_bottleneck_mod_3k_q85.jpg`). Without `--mods` nothing changes (selftest/manifest ignore mods).
Mod islands follow the game's conventions (parent at world position, scenery root at the same transform, Terrain on layer 14), but
the parent has scale 1 (originals use 0.5). Better Ports bundles also contain *replacement* TerrainData for existing islands
(`fa`, `oasis`, `dc`, `kb`, `hb`, `grc`), ladders and a drydock - not handled; the charts show the vanilla islands.

### Reproducing the "finished" style
Layout is designed for 10,000 px and scaled by DPI, so any `--size` keeps proportions. The 10k Gold Rock version we
agreed on (JPEG, quality 90, with key):

```bash
$PY -I sailwind_charts.py render "island 1 A (gold rock)" --out charts --size 10000 --quality 90 --display-titles --max-cells 8000
```
`--max-cells 8000` keeps the 0.5 m raster on the largest islands (default 5000 cells/side coarsens big islands, e.g. Gold
Rock becomes 0.6 m). It needs several GB of RAM. Runtime is seconds-to-a-minute per island.

## How it works (one paragraph)
`GameData` opens `level24` (the world scene) with UnityPy. Islands are objects named `island …` with children.
For each island the script collects the Terrains (placed by **position only**), the land meshes, and structure meshes of
the island's subtree plus its scenery scene `level<N>`; classifies meshes as ground or structure by name/size/quirk;
rasterises ground top-down (highest surface wins) into a height grid; turns structures into polygons; and renders with
matplotlib (crop = 10 m shelf edge ∪ structures, panels auto-placed away from land). Details: `DATA_LAYOUT.md` §5.

## Lessons baked into the code (do not undo without reading why)
1. **Terrain ignores transform rotation/scale** - position only. (`terrain_placement`.) Getting this wrong produced displaced
   shelves, a missing Serpent Isle, and a wave of misguided workarounds.
2. **Don't trust saved enabled/active flags**: ignore the island root's own active flag; honour children's; never test
   `renderer.enabled` (scenery renderers are saved disabled).
3. **Ignore inactive/alternate meshes** (`island_terrain_original*`, `Plane`, …).
4. **Docks/buildings are in the scenery scene**, not in level24.
5. **Classify by name carefully**: `mountain_shipyard` is land; generic `Cube`/`Cylinder` can be a lighthouse (Oasis quirk);
   collision proxies (`col_*`, `dock col`) are excluded; small `Cube*` (<60 m) are buildings.
6. **Never crop through a structure**; **holes are "no data", not deep water**.
7. **Scope fixes per island** (`ISLAND_QUIRKS`), e.g. Gold Rock's drydock floor.
8. Work in island-local coordinates (positions are ~10⁵ m).

## Adding a new island / handling a game update
1. `python -I sailwind_charts.py manifest --check` compares the islands in the data with `reference/islands_manifest.json`;
   it lists **new / removed / changed** islands and exits non-zero if anything differs.
2. `render "island NEW …" --out charts` (use the exact object name from `list`).
3. Look at it against what you know of the game. If something is off:
   - `explore tree "NAME" --depth 3` - hierarchy, active flags, layers, scripts.
   - `explore near "NAME" X0 X1 Z0 Z1` - every mesh/Terrain overlapping an island-local rectangle (incl. switched-off ones),
     with footprint, height range, collider/renderer state.
   - `explore terrain-check "NAME"` - confirms the Terrain placement rule (position-only should overlap the meshes).
   - `explore layers` - Unity layer names.
4. Fix with the narrowest tool: first a **per-island quirk** in `ISLAND_QUIRKS` (supported keys: `floor_split`
   [mesh names whose waterline-crossing triangles near the footprint are ground], `extra_struct_regex` [names to force to
   structures, L24 only, solid meshes], `extra_land` [mesh names to force into the ground without a collider]); only
   change the global regexes/thresholds if the same problem is clearly general.
5. Re-run `selftest` (must still PASS) and render all islands; compare with `sheet` against `reference/all_islands_overview_2k_q80.jpg`
   to be sure other islands did not change unintentionally.
6. If the change is intended to alter Albacore too, run `selftest --update` once and commit the new reference deliberately.
7. Refresh the manifest (`manifest`) and update `ISLAND_NOTES.md`.

## Regression test
`selftest` rebuilds **Albacore Island** (terrain-only land under a rotated+scaled parent, pier with poles, no key) and
checks (a) a numeric fingerprint of the height grid and polygons (`reference/albacore_summary.json`), then (b) that the
2000 px, quality-80, no-key JPEG matches `reference/island_4_A_fish_island_2k_q80_nokey.jpg`. In the build environment it is
**byte-identical**. The 49 other 2k charts also matched the earlier pipeline byte for byte. If you change matplotlib,
Pillow or numpy versions you may see tiny image differences while the numeric fingerprint still matches; investigate before
re-baselining.

## Tunables (top of `sailwind_charts.py`)
`MIN_SOLID_FOOTPRINT_M` 20 · `FAR_ITEM_LIMIT_M` 2500 · `GRID_RES_M` 0.5 · `GRID_MAX_CELLS` 5000 · `SHELF_EDGE_DEPTH_M` 10.05 ·
`CROP_STRUCTURE_MARGIN_M` 8 · `SMALL_CUBE_MAX_M` 60 · `POLE_DRAW_RADIUS_M` 0.9 · colours in `BAND_COLORS`.

## Known limitations
- Mesh decoding is complete: the 14 meshes that looked like failures are genuinely empty (leftover Blender curves, empty ship
  parts, an empty combiner output) - see `DATA_LAYOUT.md` §3.1. A real failure would be logged as `mesh decode failed <id>`.
- Name-based structure detection can mislabel things on islands nobody has reviewed (see `ISLAND_NOTES.md`).
- Depth below ~10 m is not modelled (the data has no useful bathymetry there).
- Charts show the **saved** scene state; runtime effects (tides, waves, island sinking) are out of scope.
- A few islands are only partly understood (Lagoon Shipyard's floating pier, Mirage Mountain's peak, nested leftovers).
