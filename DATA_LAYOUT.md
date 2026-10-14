# Sailwind raw data: logical layout and quirks

Audience: a human who wants to understand what is in `Sailwind_Data/`, and a future Claude session that needs to be
productive quickly. This is **not** a byte-level format description (UnityPy does that); it explains how the pieces
fit together and which traps cost us time. Everything here was verified against the install this checkpoint was built
from; anything uncertain is marked **(unverified)**.

- Game: *Sailwind* (Raw Lion Workshop), Unity **2019.1.10f1**, **Mono** build (not IL2CPP), 4.2 GB data folder.
- The folder also contains BepInEx (a mod loader) - irrelevant here; `changelog.txt` in the install root is BepInEx's.
- Reading tool: [UnityPy](https://github.com/K0lb3/UnityPy) 1.25.x (pure Python reader for Unity asset files).

---------------------------------------------------------------------------------------------------

## 1. What is on disk

`Sailwind_Data/` is a standard Unity player data folder. Everything is Unity "serialized files" plus raw payload files.

| Files | What they are |
|---|---|
| `level0` … `level42` (43 files) | **Scenes.** Serialized object graphs (GameObjects, Transforms, components). `level24` (35 MB) is the **main world scene**; `level1`…`level42` (except 24) are **per-island scenery scenes**; `level0` is a tiny (5 KB) boot/menu scene. `level31` is almost empty (4 KB). |
| `sharedassetsN.assets` (43) | **Asset containers** the scenes reference: Meshes, Textures, Materials, **TerrainData**, AudioClips, MonoScripts… Scenes point into them through an *externals* table (a PPtr = "file index + object id"). `sharedassets24.assets` (200 MB) holds the island meshes and all 37 island TerrainData objects. |
| `*.resS`, `*.resource` | **Streaming payloads**: bulk vertex/texture/audio data that the `.assets` file refers to by offset. They must sit next to their `.assets`; UnityPy resolves them automatically. `sharedassets24.assets.resS` is 2 GB. Never delete or rename. |
| `resources.assets`, `globalgamemanagers`, `globalgamemanagers.assets` | Project-wide data: **TagManager** (layer names), build settings, script registry (MonoScripts), shared resources. |
| `Managed/*.dll` | The C# code. `Assembly-CSharp.dll` = **game code**; `Crest.dll` = the **ocean** asset (Crest); `UnityEngine*.dll` = engine. Because this is a Mono build, they are readable .NET assemblies (ILSpy / dnSpy / `dnfile`). |
| `StreamingAssets/OceanPresets`, `Resources`, `GI`, `Plugins` | Ocean presets, Unity built-in resources, baked lighting data, native plugins. Not needed for charts. |

**Typetrees.** Built-in Unity types (GameObject, Transform, Mesh, TerrainData, …) carry their own type descriptions, so
UnityPy can parse them directly. **MonoBehaviours (the game's own scripts) cannot be parsed without the game's
assemblies**; the charts never need them. For exploration, the script *class name* of a component can still be found
by reading the MonoScript the component points to (see `explore tree`).

---------------------------------------------------------------------------------------------------

## 2. The world model: islands

### 2.1 `level24` = the world; one GameObject per island
The root is a GameObject called **`_shifting world`** (a *floating origin*: the game shifts it to keep the player near
(0,0,0)). Its children are the islands. Each island is **one GameObject** named like

    island <N> <region letter> (<name>)        e.g.  island 4 A (fish island),  island 13 E (Sage Hills)
    island <N> Lagoon <name>                    e.g.  island 27 Lagoon Shipyard
    island <N>                                  e.g.  island 36, island 42

- `N` is the **island number** (1–42). It also selects the island's scenery scene (`level<N>`, section 2.2).
- The letter (A, E, M, or none) is a region code **(unverified meaning; presumably the three game regions)**.
- **These are developer object names, not the names players see.** Example: `island 4 A (fish island)` is shown in
  game as **Albacore Island** (town: Albacore Town). The user supplied such mappings; `DISPLAY_TITLES` in the script
  holds the ones we know. Do not assume the in-game name from the object name.
- World positions are huge (±40,000–70,000 m from the origin) - float precision is poor there. **Always work in
  island-local coordinates** (subtract the island parent's position). The charts use +x = east, +z = north (assumed;
  consistent with docks/compass behaviour seen).
- Island parents usually have **uniform scale 0.5**, often a **yaw rotation** (multiples of 15°/30°/90°), and one
  (`island 22 E (Serpent Isle)`) is rotated **−90° about X** (Blender-style import). See Terrain quirk (§4.1).
- Some parents are saved **inactive** (`island 14 M (Nightcall)`, `island 9 E scenery backup`). Inactive at save time
  does not mean unused in game (§4.2).

Typical island children:

- `Terrain` (+ `TerrainCollider`, script `TerrainLayerFix`) - Unity heightmap terrain (§3.2). Not every island has one.
- **Land meshes** - names like `Cube`, `Cube_001`, `Cylinder`, `island_terrain_original`, `mountain_*`, `terrain_fix`,
  `Plane` (collider-bearing, rendered rock/sand).
- Docks and moorings: `dock_mooring E (n)` (rigidbody + `GPButtonDockMooring`), dock meshes (`gold_rock_dock`, …).
- Gameplay objects: `port … ` objects carrying `Port`, `IslandEconomy`, `IslandMarket`, `IslandMissionOffice`,
  `PortRumors`…; NPCs (`port dude`, shopkeepers), `seagulls`, mission UI.
- **`depth`** - an object with Crest's `OceanDepthCache` (§3.3). Its transform *scale* is the size of the square it covers.
- Components on the **island parent**: `IslandHorizon` (from its field and method names - `islandLoadDistance`,
  `LoadIslandScene`, `sunkHeight`, `SetHeight` - it loads/unloads the scenery scene by distance and sinks the island
  below the horizon when far away; **inferred, not read from code**), `IslandPerformanceSwitcher` (shows/hides parts
  by distance, same caveat), an `Animator` with no controller (inert).

**Island groups.** Islands that belong together are only linked by position: the six Lagoon objects (`island 26`–`29`, `31`, plus `island 26 lagoon parent terrain (-1)`) sit within ~4 km of each other and ring a central lagoon; nothing in the object names or hierarchy says so. World positions are therefore the way to find groups (`compose` uses them).

There are also **nested** "island…" objects *inside* other islands (e.g. `island A new1` under Coffee Island,
`island A new2` under Mirage Mountain, `island 4 8` under Valley, cave-church pieces). They look like leftovers or
variants; they have **no scenery scene** of their own. `island_terrain_original*`, `island_0_terrain*` etc. are
*mesh pieces*, not islands (they have no children).

### 2.2 Scenery scenes `level<N>`
Each island's buildings, docks, props, palm trees, fences… live in **its own scene file**, `level<N>` with N from the
island's name. The scene has **one root** named `island <N> … scenery` whose transform **equals the island parent's
transform** in `level24` (verified on several islands), so scenery lines up with the land when both are placed with
their stored transforms. The scenery has hundreds of children (Neverdin 385, Gold Rock 686). `IslandHorizon` loads the
scene additively when the player gets near. Consequences:

- Docks, buildings, fortifications - the things a chart must show - are mostly **in the scenery scene, not in level24**.
- A few scenery scenes also contain a `Terrain` (levels 30 and 37).
- **Mesh combiners.** Scenery scenes hold `_mesh combiner (<material name>)` objects (a MonoBehaviour plus one child called
  `New Game Object`) - e.g. `buildings_A_paint`, `palm tree leaves`, `palm tree bark`, `furniture_paint`, `rocks_paint`,
  `bushes atlas`. The child is a **baked mesh of all scenery that uses that material**: tens of thousands of vertices, no
  collider, often hundreds of metres wide (Neverdin: 7 combiners, 160k+ vertices). This is the likely reason most scenery
  renderers are *saved disabled* (Neverdin: 1,030 of 1,156): the original pieces are kept but the combined mesh is what is
  drawn. **Inference** from the object layout, not from reading the script. Consequence for charts: these `New Game Object`
  meshes are **not land** (they once looked like it). By default the individual, named originals give structure footprints;
  `render --scenery` instead adds the combined meshes by material category (buildings/furniture, trees, bushes, rocks).
- Nested islands have no scene; numbering is by object name, so `island 4 8` must *not* be mistaken for island 4.

---------------------------------------------------------------------------------------------------

## 3. Geometry and where heights come from

### 3.1 Meshes
`MeshFilter` → `Mesh` object in a `sharedassets*.assets` (vertex data may live in a `.resS`). World placement =
product of local transforms up the parent chain (translation·rotation·scale; Unity quaternions; left-handed, y up).
Land meshes are often imported from Blender and carry a `−90° about X` rotation and scale `(0.8, 0.8, 1.0)`.
**Empty meshes.** 14 meshes referenced by the islands decode to *nothing*: vertex count 0, empty index buffer, one empty
submesh, no stream data. They are not decode failures and nothing is lost. What they are: 8 leftover Blender objects
(`BezierCurve`, `BezierCurve_00n`, `Cube_001`: Gold Rock 3, Dragon Cliffs 4, Nightcall 1; each also carries an empty
`MeshCollider`), a `Cylinder_004` under "chronos church", four empty ship-part meshes (`ropes_000`, `metal_parts_001` under a
"dhow" model in `level30`; `parts_shrouds_B/F_default` under `structure_container` in `level33`), and the empty result of one
"mesh combiner" (`rocks_paint` in `level7`: that scene has no rocks to combine). None has a script that could fill it in.
(An earlier version of the pipeline mislabelled these as "decode failed". Every other mesh referenced by the island trees and
their scenery scenes decodes fine; meshes elsewhere in the game were not tested.)
Many island meshes are **open sheets**: they have no skirt and simply end (e.g. Sage Hills' hill mesh stops 20–30 m
above sea level on its north flank). The pipeline treats cells without geometry as *no data* (and enclosed holes are
shown grey).

### 3.2 Terrain (heightmaps)
`Terrain` → `TerrainData` (in `sharedassets24.assets`: 37 objects, one per island Terrain). Each holds a **513×513
int16 heightmap**. `m_Scale = (sx, sy, sz)`: `sx`,`sz` = **metres per sample** horizontally (0.39 – 3.5 m), `sy` = the
full vertical range (50–600 m). **Height (m) = sample / 32766 × sy**. The array is indexed `[row = z, col = x]`.
Terrain base is usually 20–40 m below the island's sea level (the terrain's transform y is −16 … −148), so
only part of it rises above water.

### 3.3 Layers and the ocean depth cache
Layer names live in the TagManager (`globalgamemanagers`). Relevant: **14 `TerrainDepth`**, 8 `WalkCols`, 4 `Water`,
27 `ChartObjects` (nothing found on it inside the islands). The **Crest `OceanDepthCache`** on each island's `depth`
child renders everything on layer 14 into a top-down depth map (realtime mode; layer list `["TerrainDepth"]`;
resolution 512/1024; max height 100 m) - that is how the game knows where the water is shallow. So **layer 14 =
the geometry the game itself treats as seabed/land**. `TerrainLayerFix` moves Terrains off layer 14 to layer 0 after
two frames, so a Terrain is on layer 14 only at start-up.

### 3.4 Sea level and the shelf
- **Sea level is y = 0** in island-local frames (island parents sit at y = 0; dock tops are +0.3…1.6 m).
- There is no per-island water plane in level24. A big flat mesh **`terrain_fix`** (material *ocean bottom 1*, e.g. at
  −9.7 m on Gold Rock) forms a flat seabed shelf with sloping skirts; for most islands "deeper than ~10 m" is
  featureless, which is why the charts stop at the 10 m shelf edge.

---------------------------------------------------------------------------------------------------

## 4. Quirks and traps (read before changing anything)

1. **A Unity Terrain ignores its transform's rotation and scale. Only the position counts.** Size and heights come
   from TerrainData, the terrain stays axis-aligned and unscaled even if its island parent is rotated or scaled 0.5×.
   Applying the parent's rotation/scale (the "obvious" thing) shrank terrains, rotated them, and made them look like
   displaced underwater "shelves"; Serpent Isle's whole terrain was skipped as "tilted". With the correct rule the
   terrain overlaps the island meshes (IoU 0.6–0.85 instead of ~0) and docks sit where they should. Gold Rock was
   unaffected only because its Terrain object has its own scale 2.0, cancelling the parent's 0.5.
   → `explore terrain-check NAME` demonstrates this on any island.
2. **Saved "active"/"enabled" flags are not runtime state.** The island parent may be inactive (Nightcall); most
   scenery `MeshRenderer`s are saved *disabled* (probably because the pieces are baked into mesh combiners, §2.2). So: do **not** filter on
   `renderer.enabled`; do honour `GameObject.activeSelf` of children (an inactive child hides its subtree); ignore the
   island root's own flag.
3. **Collision proxies** (`col_1`, `col_2`, `dock col`) have big footprints but are invisible. Excluded by name
   (`COLLISION_PROXY_RE`). Beware: real scenery also has disabled renderers, so name - not flag - is the test.
4. **Switched-off alternate meshes** (`island_terrain_original`, `_001`, `_002`, `Plane`, `tea`, `Cube` copies, `debug range`)
   are old/variant versions or helpers. We spent a long time compensating for a *wrong terrain placement* by pulling
   these in; once Terrain was right they were unnecessary and sometimes harmful. **Ignore inactive objects.**
5. **Names lie.** `mountain_shipyard` is a mountain, not a structure; a lighthouse is built from generic colliders called
   `Cube` / `Cylinder_00n` (Oasis); `building` can be a whole academy; `Plane` can be a 3 km sea sheet. Classification
   in the script is by name + size and has per-island quirks (`ISLAND_QUIRKS`). Review new islands visually.
6. **Scenery scenes carry far-away strays** (e.g. Nightcall's `gda_building_*` 36 km away). Items further than 2.5 km
   from the island origin are dropped.
7. **Drydock / ramps are part of "structure" meshes.** Gold Rock's drydock floor is inside the mesh `arab_shipyard`; only
   its waterline-crossing triangles near the dock footprint are treated as ground. Keep such fixes scoped.
8. **Open meshes and holes.** A hole inside an island's data (e.g. the lagoons of `island 35 M valley` and `island 4 8`)
   means "no geometry", not "deep". Rendered grey and listed in the key as "No data (not modelled)".
9. **Float precision.** Island positions are ~10⁴–10⁵ m; subtract the island origin *before* rasterising.
10. **Empty meshes** (14 of them, see 3.1) look like decode failures but are just empty. The code treats them silently as
    empty; a *real* decode error is still logged as `mesh decode failed <id>`, and there are currently none.
11. **IslandHorizon appears to sink distant islands** (inferred from its fields, see §2.1). The saved scene is the emerged
    state; do not try to "fix" heights with it. (An earlier idea - lowering Albacore's land a few metres to open its
    channel - was a symptom of the Terrain bug in item 1, not a real effect.)
12. **Developer vs player names; island numbers vs scene numbers.** See §2.1/§2.2.

---------------------------------------------------------------------------------------------------

## 5. How the chart pipeline maps onto this (`sailwind_charts.py`)

1. **Discover** island objects in `level24` (name starts with `island`, not `island_`, has children).
2. **Collect geometry** of the subtree (+ the scene `level<N>` for top-level islands): Terrains (position-only rule),
   meshes that are on layer 14, *or* solid and ≥ 20 m wide, *or* structure-named, minus excluded names.
3. **Classify** meshes as ground or structure (name rules, size rules, quirks).
4. **Rasterise** ground (top-down maximum height) at 0.5 m (coarser for very large islands, ≤ 5000 cells per side);
   structures become **vector footprints** (above water / submerged / dock poles).
5. **Render**: depth bands (0–2, 2–5 flat; 5–10 gradient), contours (1 m & 5 m depth, 10 m land), hillshade, red
   structures, auto-placed title / scale bar / north arrow / key. Crop = 10 m shelf edge ∪ all structures.

## 6. Known unknowns
- Which `island A new*` / `island 4 8` style objects are live in the game, and whether Mirage Mountain's peak is
  `island A new2` (see `ISLAND_NOTES.md`).
- The exact meaning of region letters; which Lagoon pieces belong together.
- Whether the `_mesh combiner` scripts (editor-time baking vs runtime) change anything about which scenery is visible.
- Runtime-only effects (tides, wave attenuation by the depth cache, `IslandHorizon` sinking) are not in the data.
