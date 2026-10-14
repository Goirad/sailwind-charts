# Sailwind island charts

Nautical-style bathymetry charts for every island of *Sailwind*, generated straight from the game's Unity data (the game files
are only read, never modified or redistributed). Each chart is a top-down map (north up, island-local metres) with depth bands
and contours (1 m / 5 m down to the 10 m shelf edge, 5 m below that down to the 20 m terrain floor), land contours every 10 m,
hillshading, and docks / quays / walls / buildings as red vector footprints, trees and bushes in green, dock poles as hazard markers.

```
charts.py                  launcher: python -I charts.py <command> ...
sailwind_charts/           the pipeline (see "How it works")
data/islands.toml          the catalogue: real names, status, quirks, composites   <- edit this when you learn something
reference/                 selftest reference (Albacore) and the island manifest
tests/                     pytest suite on synthetic data (runs without the game)
DATA_LAYOUT.md             how the Unity data is organised, and the traps in it (read first; esp. section 4)
```

## Quick start

```bash
./setup_env.sh                                  # ./venv with the pinned packages (Python 3.12+)
export SAILWIND_DATA=".../Sailwind/Sailwind_Data"
PY=./venv/bin/python
$PY -I charts.py selftest                       # must print SELFTEST PASS
$PY -I charts.py list                           # the islands found, with their real names
$PY -I charts.py render "Albacore Island" --size 5000 --quality 90 --out charts
$PY -I charts.py render --all --size 5000 --quality 90 --out charts       # every catalogued island, 12 workers
$PY -I charts.py sheet charts --out overview.jpg                           # contact sheet for review
$PY -m pytest tests                                                        # unit tests (no game needed)
```

Run with `-I` (isolated mode): the game files are treated as untrusted data and Python must not pick up stray modules.
The game folder is `$SAILWIND_DATA` or `--game-data PATH`.

### Commands
| command | what it does |
|---|---|
| `list` | islands in the game data with real name, scene, quirks |
| `names` | check `data/islands.toml` against the game: new islands, stale entries, islands without a real name (exit 1 if any) |
| `render NAME... \| --all` | charts; NAME is the object name or the real name. `--all` renders every island not marked `skip`, and each composite as one map |
| `compose --group TITLE` | one composite (e.g. the Lagoon) on its own; or `compose NAME... --title T` |
| `selftest [--update]` | Albacore must reproduce `reference/` (numbers and image) |
| `manifest [--check]` | detect new/removed/changed islands after a game update |
| `sheet FOLDER` | contact sheet |
| `site --charts DIR --out DIR` | build the static site from charts rendered with `--key separate` (see below) |
| `explore tree\|near\|terrain-check\|layers` | work out how an odd island is built |

### Render options
`--size` long side in px · `--quality` JPEG · `--key baked|separate|none` (separate = for the site: the image reserves the key's corner and the key is written as `NAME_key.png` with its placement in `NAME.json`) · `--margin F` sea buffer as a fraction of the long side (min 15 m; 0 = none) ·
`--key-width PX` key width in final pixels (default 500, 0 = full size) · `--max-depth M` deepest drawn water (default 20) ·
`--no-scenery` · `--fixed-res` (always 0.5 m raster; default is finer for small islands) · `--max-cells N` · `--jobs N` · `--members` ·
`--title` · `--dump-grid DIR`. File names come from the real name (`Albacore_Island.jpg`, apostrophes dropped).
`--mods` (before the command) also scans BepInEx island mods; currently that finds `island 67 Bottleneck` (Better Ports).

## The catalogue (`data/islands.toml`)
One table per island keyed by the developer object name: `name` (title and file name), `skip`, `mod`, `status`
(validated / checked / doubtful), `notes`, and `quirks` (per-island overrides, documented at the top of the file). Composites are
islands that are one place in the game and are drawn as one map in their true relative positions (the Lagoon ring). After a game
update: `manifest --check` and `names` list what is new; render it, compare against the game, fix with the narrowest quirk, and
update the notes.

## The site
```bash
$PY -I charts.py render --all --key separate --size 5000 --quality 90 --out charts
$PY -I charts.py site --charts charts --out site        # then open site/index.html
```
One static page, no JavaScript, no dependencies: region-grouped buttons (`#Name` links, so every chart has a shareable URL), the
chart at a fixed 1100 CSS px so browser zoom / pinch magnifies it, a key that can be switched off, and an About box. Set `[site]`
(`title`, `author`, `repo_url`, `regions`) and each island's `region` in `data/islands.toml`; the build fails on an island whose
region is not listed (`--lenient` groups those under "Unassigned"). Needs a browser with CSS `:has()` (2023+). To publish, copy
`site/` to a `gh-pages` branch (kept separate so the binaries never enter `master`'s history).

## How it works
`gamedata.py` opens `level24` (the world scene) with UnityPy and finds the objects named `island …`; `classify.py` decides by
object/material name what is land, a structure, a tree...; `grid.py` collects the Terrains (placed by **position only**), land meshes
and structure meshes of the island's subtree plus its scenery scene `level<N>`, rasterises the ground top-down (highest surface wins)
into a height grid and turns structures into polygons; `render.py` crops to the 10 m shelf (grown to contain every structure, plus a
margin), colours and contours it, draws the vectors and places title / north arrow / scale bar / key in the emptiest corners.
Details: `DATA_LAYOUT.md` section 5.

## Lessons baked into the code (do not undo without reading why)
1. **Terrain ignores transform rotation/scale** - position only (`terrain_placement`). Getting this wrong displaced shelves and hid Serpent Isle.
2. **Don't trust saved enabled/active flags**: ignore the island root's own active flag; honour children's; never test `renderer.enabled`.
3. **Ignore inactive/alternate meshes** (`island_terrain_original*`, `Plane`, ...).
4. **Docks/buildings are in the scenery scene**, not in level24.
5. **Classify by name carefully**: `mountain_shipyard` is land; generic `Cube`/`Cylinder` can be a lighthouse (Oasis); collision proxies are excluded; small `Cube*` (<60 m) are buildings unless the island's quirks say otherwise.
6. **Never crop through a structure**; **holes are "no data", not deep water** (unless the island says it is open water).
7. **Flat planes just under 10 m** (Gold Rock) are depth-cache skirts: they must not set the crop. Water below 20 m is clamped (mesh skirts there are artefacts; the terrain floor is what the game shows).
8. Work in island-local coordinates (positions are ~10^5 m).

## Regression tests
`selftest` rebuilds **Albacore Island** (terrain-only land under a rotated+scaled parent, pier with poles, no key) and checks a numeric
fingerprint of the height grid and polygons (`reference/albacore_summary.json`), then that the 2000 px, quality-80 JPEG matches
`reference/albacore_2k_q80_nokey.jpg`. It needs the game files, so it is local only; `pytest tests` covers the logic on synthetic data
and is what CI can run. Different matplotlib/Pillow/numpy versions can shift pixels slightly while the fingerprint still matches;
investigate before re-baselining (`selftest --update`, committed deliberately).

## Known limitations
- Depth below ~10 m is not real bathymetry in the data (flat terrain floor or mesh skirts); the deeper shading is indicative only.
- Name-based structure detection can mislabel things on islands nobody has reviewed (see `status` and `notes` in the catalogue).
- Charts show the **saved** scene state; runtime effects (tides, waves, island sinking) are out of scope.
- A few islands are only partly understood (Lagoon Shipyard's floating pier, Mirage Mountain's peak, nested leftovers).

## License
MIT (code). The charts are derived from *Sailwind*'s game data; *Sailwind* belongs to its developer.
