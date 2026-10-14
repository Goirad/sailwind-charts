# Island notes: status, quirks, open questions

Status key: **validated** = the user compared it with their knowledge of the game and accepted it; **checked** = we looked at
it closely but nobody has confirmed against the game; **sheet-only** = only seen on a contact sheet, no known problems;
**doubtful** = known or suspected problem. Object names are the developer names from `level24`; in-game names are only
known where the user told us.

`--scenery` (baked mesh combiners: fuller buildings, trees, bushes, rocks) was reviewed on Neverdin and Old Ankh Town and looked right on a contact sheet of all 50; not inspected island by island.

Reference overview of the current output: `reference/all_islands_overview_2k_q80.jpg` (50 charts, 2k, no key).

## Reviewed in detail

| Object name | Players see | Status | Notes |
|---|---|---|---|
| `island 1 A (gold rock)` | Gold Rock (user title: **Gold Rock City**) | **validated** (10k, quality 90, with key) | Quirk `floor_split`: the dry dock's floor/ramp inside mesh `arab_shipyard` is ground, not structure (the bay was hidden before). Quay slab may include `dock col` collision proxies (user judged the harbour right). Terrain has its own scale 2.0 so it was never affected by the Terrain bug. |
| `island 4 A (fish island)` | **Albacore Island** (Albacore Town) | **validated** against the user's bird's-eye screenshot | Two islands with a curved channel, pier from the north island into the channel. Land is Terrain only (parent scale 0.5, yaw −120°). **Selftest island.** Earlier "stitching"/"lowering" ideas were symptoms of the Terrain bug. |
| `island 22 E (Serpent Isle)` | Serpent Isle | checked | Parent rotated −90° about X; its 1200 m Terrain is real land (3 low islands, pier at the narrow waist). |
| `island 13 E (Sage Hills)` | Sage Hills | checked | Two islands, channel with the dock village. Ragged flanks seen earlier were the Terrain bug + an open hill mesh. |
| `island 20 A (Oasis)` | Oasis | checked (5k, **before** the Terrain fix - re-check) | Quirk `extra_struct_regex`: lighthouse on the small islet = generic `Cube`/`Cylinder(_n)` colliders. |
| `island 2 A (AlNilem)` | Al'Nilem | sheet-only after fix (5k version predates the fix) | Now a ring island around a caldera lake; **re-render the 5k**. |
| `island 14 M (Nightcall)` | Nightcall | checked | Parent object saved inactive - handled. Spiral channel and walled town look right. |
| `island 3 A (Neverdin)` | Neverdin | sheet-only after fix | Was the poster child for the Terrain bug (displaced "shelf"). Re-check against the game. |
| `island 10 E (sanctuary)` | Sanctuary | checked (crop fix) | Pier reaches past the 10 m shelf: border now grows to include structures. |

| `island 40 (coffee)` | **Old Ankh Town** | checked (5k, `--scenery`) | Two hill masses split by a channel, harbour with pier, a village of ~20 building blocks on small islets in the south. A thin straight line across the left hill (ridge/wall or mesh seam?) is unexplained. |
| Lagoon group: `island 26 lagoon parent terrain (-1)`, `26 Lagoon Temple`, `27 Lagoon Shipyard`, `28 Lagoon Senna`, `29 Lagoon Onna`, `31 Lagoon Fisherman` | the Lagoon | checked | Their world positions are within ~4 km and form a **ring around open water**; `compose` renders them as one map (`reference/lagoon_composite_3k_q85.jpg`). Rock of Despair (`island 30`) is 40 km away and not part of it. Seams between overlapping pieces unchecked. |

| `island 67 Bottleneck` (**mod**: Better Ports) | Bottleneck | checked once (`--mods`) | Ring of five islands around a deep central pool; port with a T-shaped pier and moorings on the southern island. One benign `mesh decode failed ... unity default resources` message (a built-in Unity primitive referenced by the bundle). No tree/building combiners were found in its scenery. |

## Open questions / doubtful

| Object name | Issue |
|---|---|
| `island 41 (mirage mountain)` and nested `island A new2` | `A new2` = two mountain meshes (164×140 m, 17–81 m high, no colliders) offset +18 m inside Mirage Mountain. Mirage Mountain's chart shows a low island with no peak. Possibly the real peak - not yet merged (quirk `extra_land` only renders `A new2` on its own). **Ask the user what Mirage Mountain looks like.** |
| `island 27 Lagoon Shipyard` | Pier/buildings float in open water NW of the mountain island, away from land - something else is misplaced or missing. Also fixed here: `mountain_shipyard` was mistaken for a structure (now excluded by `LAND_NAME_RE`). |
| `island 8 A (academy)` | Large red area from a mesh literally named `building` (probably the academy itself). Unverified. |
| `island 12 E (New Port)` | A flat fan-shaped area appeared on the north island when switched-off base meshes were included (since removed). With the Terrain fix it should be gone - confirm against the game. |
| Nested leftovers: `island 4 8` (under Valley), `island A new1` (under Coffee), `island E new3` (under Onsen), `island cave church final terrain`, `island cave church town`, `island4-7` | No scenery scene; unclear whether live. `island4-7` is only building meshes; `island cave church town` is an odd flat layout. |
| `island 9 E scenery backup` | Inactive backup of Dragon Cliffs' scenery: buildings only, no land. |
| `island 26 lagoon parent terrain (-1)` | Large (4×6 km) terrain-style parent of the lagoon islands. |

## Mechanics worth remembering per island type
- **Islands with a rotated/scaled parent and a Terrain** (Fish/Albacore, Neverdin, Sage Hills, Serpent Isle, Crab Beach, …):
  anything that looks displaced is probably the Terrain rule (position only) - run `explore terrain-check`.
- **Islands with no Terrain** (mesh islands: Lion Fang, Clear Mind, Alchemist's, …): land is `island_terrain_original`
  (+ hill meshes); the skirt of the mesh provides the shelf.
- **Docks/buildings always come from the scenery scene** - if they float in open water, suspect the land, not the docks
  (the scenery shares the island parent's transform).

## What was never checked
Islands that were only seen on contact sheets (most of 25–42: chronos, Lagoon Temple/Senna/Onna/Fisherman, hideout, cave church,
monastery, valley, 36, swamp, jungle, onsen, coffee, mirage mountain, 42; and 5, 6, 7, 9, 11, 15–19, 21, 23, 30) have **not**
been compared with the game. (Not a concern: the 14 "failed" meshes - Dragon Cliffs 4, Gold Rock 3, Rock of Despair 2, Cave
Church 2, Nightcall, Chronos, Alchemist's 1 each - are empty meshes, no geometry is missing.)
