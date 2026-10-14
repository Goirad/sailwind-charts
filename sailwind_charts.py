#!/usr/bin/env python3
"""
sailwind_charts.py - nautical-style bathymetry charts for every island in Sailwind.

Reads the Unity data of the game directly (no game code is run) and renders, per island, a top-down
chart: depth bands and contours (1 m / 5 m, to the 10 m shelf edge), land contours every 10 m,
hillshading, and docks / quays / walls / buildings / dock poles as vector polygons.

Sub-commands
------------
  list                      list the islands found in the game data
  manifest [--check]        write / compare reference/islands_manifest.json (detects new islands after an update)
  render NAME... | --all    render charts (parallel with --jobs N)
  selftest                  regression test: Albacore Island must reproduce the stored reference
  compose NAME... --title T  render several islands as one map in their true relative positions
  sheet FOLDER              contact sheet of all charts in a folder (visual review)
  explore tree NAME         dump an island's object hierarchy (components, layers, active flags, transforms)
  explore near NAME ...     list every mesh/terrain overlapping a rectangle (incl. switched-off ones)
  explore terrain-check NAME  prove which terrain placement rule fits the island's meshes
  explore layers            print Unity layer names

Run it isolated:  python3 -I sailwind_charts.py <command> ...      (see README.md)

Read DATA_LAYOUT.md first: it explains the data and the lessons baked into the code below
(most importantly: Unity Terrain objects ignore their transform's rotation and scale).
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME_DATA = os.environ.get('SAILWIND_DATA', '/tmp/Sailwind/Sailwind_Data')
REFERENCE_DIR = os.path.join(HERE, 'reference')
MODS = False   # set by --mods
SELFTEST_ISLAND = 'island 4 A (fish island)'     # shown in-game as "Albacore Island"

# --------------------------------------------------------------------------------------------------
# Tunables (every number here was chosen on purpose - see DATA_LAYOUT.md / README.md for why)
# --------------------------------------------------------------------------------------------------
TERRAIN_DEPTH_LAYER = 14        # Unity layer "TerrainDepth": geometry the ocean's depth cache draws
MIN_SOLID_FOOTPRINT_M = 20.0    # a solid (collider) scenery mesh must be at least this wide to count
FAR_ITEM_LIMIT_M = 2500.0       # drop items further than this from the island origin (stray scenery)
GRID_RES_M = 0.5                # base raster resolution
GRID_MAX_CELLS = 5000           # ...coarsened so the longest side never exceeds this many cells
SHELF_EDGE_DEPTH_M = 10.05      # the chart border is the bounding box of everything shallower than this
CROP_STRUCTURE_MARGIN_M = 8.0   # the border also grows to contain every structure, plus this margin
LAYOUT_PX = 10000               # all layout is designed for a 10,000 px long side, then scaled by DPI
POLE_DRAW_RADIUS_M = 0.9        # dock poles are drawn oversize so they read on the chart

# Structure classification (by mesh/GameObject name).  Order of tests matters, see is_structure().
STRUCT_INCLUDE_RE = re.compile(r'dock|bridge|shipyard|wall|building|pier|gate|granary|jetty|stairs', re.I)
STRUCT_CLASS_RE = re.compile(r'dock|bridge|shipyard|wall|building|pier|gate|granary|hull|jetty|stairs', re.I)
DETAIL_RE = re.compile(r'window|door|awning|addon|shelf|furniture|logs|sign', re.I)       # building details: skip
COLLISION_PROXY_RE = re.compile(r'^col(_|\d|$)|collider|collision', re.I)                 # invisible hulls: skip
POLE_RE = re.compile(r'pole|piling', re.I)
LAND_NAME_RE = re.compile(r'(mountain|mtn)', re.I)    # land meshes that may contain a structure word (mountain_shipyard)
SKIP_NAME_WORDS = ('water', 'ocean', 'foam', 'sea', 'cloud', 'horizon', 'sky', 'shadow', 'mooring', 'ui_', 'bookmark')
SMALL_CUBE_MAX_M = 60.0         # a mesh called Cube* narrower than this is a building, not land

# --scenery option: scenery scenes bake most of their pieces into "_mesh combiner (<material>)" objects, each with a child mesh
# per material.  Using them gives fuller building outlines, fences/walls, vegetation and rocks (see DATA_LAYOUT.md 2.2).
# Materials (35 exist) sort into categories; anything else (items, carts, ships, fruit, ...) is ignored.
COMBINER_PREFIX = '_mesh combiner'
SCENERY_STRUCT_RE = re.compile(r'buildings|senna_paint|dock|furniture', re.I)        # drawn red, like other structures
SCENERY_TREE_RE = re.compile(r'palm tree|tree leaves|tree wood|bamboo', re.I)         # trees (dark green layer)
SCENERY_BUSH_RE = re.compile(r'bush|planter plants', re.I)                            # bushes / plants (light green layer)
SCENERY_ROCK_RE = re.compile(r'^rocks', re.I)                                         # merged into the ground heights
SCENERY_COLORS = {'trees': '#1b5e20', 'bushes': '#9ccc65'}


def scenery_category(material):
    """'struct' | 'tree' | 'bush' | 'rock' | None for a mesh-combiner material name."""
    if SCENERY_ROCK_RE.search(material):
        return 'rock'
    if SCENERY_TREE_RE.search(material):
        return 'tree'
    if SCENERY_BUSH_RE.search(material):
        return 'bush'
    if SCENERY_STRUCT_RE.search(material):
        return 'struct'
    return None

# Per-island special cases.  Keep each one scoped to its island: they were validated against the
# user's knowledge of the game and would damage other maps.  Keys are exact in-game object names.
ISLAND_QUIRKS = {
    # Drydock: the floor and ramp of the dry dock are part of the *structure* mesh "arab_shipyard".
    # Triangles of that mesh that cross/lie under the waterline inside the dock footprint are ground.
    'island 1 A (gold rock)': {'floor_split': ['L24:arab_shipyard']},
    # The lighthouse on the small islet is ~15 generic colliders named Cube / Cylinder(_N).
    'island 20 A (Oasis)': {'extra_struct_regex': r'^(Cube|Cylinder)(_\d+)?$'},
    # Nested group under Mirage Mountain: two mountain meshes without colliders (maybe Mirage Mountain's peak).
    'island A new2': {'extra_land': ['mountain', 'mountain_001']},
}
# Titles used by the user for finished charts (object name -> display title). Optional.
DISPLAY_TITLES = {
    'island 1 A (gold rock)': 'Gold Rock City',
    'island 4 A (fish island)': 'Albacore Island',
    'island 40 (coffee)': 'Old Ankh Town',
}

BAND_COLORS = {'land': '#e3d7a3', '0-2': '#dcebf6', '2-5': '#a9cce6', '5-10': '#6fa8d3', '10+': '#4f8fc4'}
# 0-2 m and 2-5 m are flat bands; 5-10 m is a gradient from '5-10' (at 5 m) to '10+' (at 10 m).


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------------------------------
# Unity helpers
# --------------------------------------------------------------------------------------------------
def _tf(go):
    """The Transform object of a GameObject."""
    for c in go.m_Components:
        r = c.deref()
        if r.type.name == 'Transform':
            return r.parse_as_object()


def _comp(go, type_name):
    return [c.deref() for c in go.m_Components if c.deref().type.name == type_name]


def _trs(t):
    """Local 4x4 matrix (translation * rotation * scale) of a Unity Transform (quaternion -> matrix)."""
    p, q, s = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
    x, y, z, w = q.x, q.y, q.z, q.w
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R @ np.diag([s.x, s.y, s.z])
    M[:3, 3] = [p.x, p.y, p.z]
    return M


def terrain_placement(sx, sy, sz, M):
    """
    Where a Unity Terrain really is.  KEY LESSON: a Terrain ignores its Transform's rotation and scale; only
    the world POSITION (parent chain applied to the local position) counts.  Size and heights come straight
    from TerrainData, so the terrain is axis-aligned and unscaled even if its island parent is rotated/scaled.
    Returns (corners_xz[4,2], origin_xz[2], y_offset).
    """
    b = M[[0, 2], 3].copy()
    corners = np.array([[b[0] + a, b[1] + c] for a in (0, 512 * sx) for c in (0, 512 * sz)])
    return corners, b, M[1, 3]


class GameData:
    """Lazy access to the Unity data. level24 holds all island parents; level<N> is island N's scenery scene."""

    def __init__(self, data_dir=DEFAULT_GAME_DATA, mods=None):
        import UnityPy
        self._UnityPy = UnityPy
        self.dir = data_dir.rstrip('/') + '/'
        if not os.path.exists(self.dir + 'level24'):
            raise SystemExit(f'level24 not found in {self.dir} - pass --game-data /path/to/Sailwind_Data')
        env = UnityPy.load(self.dir + 'level24')
        self.l24 = list(env.files.values())[0]
        self._mesh, self._terrain, self._scene = {}, {}, {}
        # --mods: asset bundles of BepInEx island mods (e.g. Better Ports adds "island 67 Bottleneck": an island prefab in one
        # bundle and its scenery scene in another).  Their serialized files are scanned like level24 / level<N>.
        self.mod_files, self.mod_scenes = [], {}
        if MODS if mods is None else mods:
            self._load_mod_bundles()

    # ---- islands ----------------------------------------------------------------------------
    def _load_mod_bundles(self):
        plugins = os.path.join(os.path.dirname(self.dir.rstrip('/')), 'BepInEx', 'plugins')
        for root, _, files in os.walk(plugins):
            for fn in files:
                p = os.path.join(root, fn)
                if '.' in fn or os.path.getsize(p) < 100_000:      # asset bundles have no extension; skip dlls, manifests, json
                    continue
                try:
                    env = self._UnityPy.load(p)
                except Exception:
                    continue
                for bf in env.files.values():
                    for f in (getattr(bf, 'files', None) or {}).values():
                        if hasattr(f, 'objects'):
                            self._register_mod_file(f, p)
                    if hasattr(bf, 'objects'):
                        self._register_mod_file(bf, p)

    def _register_mod_file(self, f, path):
        has_island = False
        for o in f.objects.values():
            if o.type.name == 'Transform':
                t = o.parse_as_object()
                if t.m_Father.path_id:
                    continue
                n = t.m_GameObject.deref().parse_as_object().m_Name
                m = re.match(r'island (\d+).*scenery', n, re.I)
                if m:
                    self.mod_scenes[int(m.group(1))] = f
                elif n.lower().startswith('island') and not n.lower().startswith('island_') and len(t.m_Children):
                    has_island = True
        if has_island:
            self.mod_files.append(f)

    def all_gameobjects(self):
        for o in self.l24.objects.values():
            if o.type.name == 'GameObject':
                yield o.parse_as_object()

    def islands(self):
        """
        Every object that is an island: name starts with 'island' (but not 'island_' - those are mesh pieces),
        and it has children.  `nested` islands live under another island (leftovers / variants) and have no
        scenery scene of their own.
        """
        out = []
        sources = [(g, False) for g in self.all_gameobjects()]
        for f in self.mod_files:
            sources += [(o.parse_as_object(), True) for o in f.objects.values() if o.type.name == 'GameObject']
        for g, from_mod in sources:
            n = g.m_Name
            if not n.lower().startswith('island') or n.lower().startswith('island_'):
                continue
            t = _tf(g)
            if len(t.m_Children) == 0 or (from_mod and t.m_Father.path_id):
                continue                      # in mod bundles only root prefabs count
            parent = t.m_Father.deref().parse_as_object().m_GameObject.deref().parse_as_object().m_Name if t.m_Father.path_id else '<root>'
            out.append(dict(name=n, parent=parent, active=bool(g.m_IsActive), children=len(t.m_Children),
                            nested=(parent not in ('_shifting world', '<root>')), go=g, mod=from_mod))
        return sorted(out, key=lambda d: d['name'])

    def find(self, name):
        for isl in self.islands():
            if isl['name'] == name:
                return isl
        raise SystemExit(f'island {name!r} not found. Use the list command to see names.')

    # ---- decoded assets (cached) ------------------------------------------------------------
    def mesh(self, ptr):
        """(vertices[n,3] float64, triangles[m,3] int64) in mesh-local space; empty arrays if empty or undecodable."""
        from UnityPy.helpers.MeshHelper import MeshHandler
        k = (ptr.file_id, id(ptr.assetsfile), ptr.path_id)
        if k not in self._mesh:
            try:
                m = ptr.deref().parse_as_object()
                h = MeshHandler(m)
                h.process()
                if h.m_Vertices is None or len(h.m_Vertices) == 0:
                    # Genuinely empty mesh (vertex count 0, empty index buffer): 14 such meshes exist - leftover Blender
                    # BezierCurve/Cube objects, empty ship-part meshes, an empty "mesh combiner" result.  Nothing to draw.
                    self._mesh[k] = (np.zeros((0, 3)), np.zeros((0, 3), np.int64))
                    return self._mesh[k]
                V = np.array(h.m_Vertices, dtype=np.float64).reshape(-1, 3)
                Ts = [np.array(x, dtype=np.int64).reshape(-1, 3) for x in h.get_triangles()]
                self._mesh[k] = (V, np.concatenate(Ts) if Ts else np.zeros((0, 3), np.int64))
            except Exception as e:                                  # some meshes cannot be decoded
                log('  mesh decode failed', ptr.path_id, repr(e)[:80])
                self._mesh[k] = (np.zeros((0, 3)), np.zeros((0, 3), np.int64))
        return self._mesh[k]

    def terrain_data(self, ptr):
        """(heights[513,513] int16-as-float32, (sx, sy, sz)).  height_m = h/32766*sy ; sx,sz = metres per sample."""
        k = (ptr.file_id, id(ptr.assetsfile), ptr.path_id)
        if k not in self._terrain:
            t = ptr.deref().parse_as_object()
            s = t.m_Heightmap.m_Scale
            H = np.array(t.m_Heightmap.m_Heights, dtype=np.float32).reshape(513, 513)
            self._terrain[k] = (H, (s.x, s.y, s.z))
        return self._terrain[k]

    def scene(self, n):
        if n in self.mod_scenes:
            return self.mod_scenes[n]
        if n not in self._scene:
            try:
                self._scene[n] = list(self._UnityPy.load(self.dir + 'level%d' % n).files.values())[0]
            except Exception:
                self._scene[n] = None
        return self._scene[n]

    # ---- collecting an island's geometry -------------------------------------------------------
    def _walk(self, root_t, M0, items, tag, quirks):
        extra_land = set(quirks.get('extra_land', ()))
        extra_re = re.compile(quirks['extra_struct_regex']) if quirks.get('extra_struct_regex') else None

        def walk(t, Mparent, active):
            for ch in t.m_Children:
                tt = ch.deref().parse_as_object()
                g = tt.m_GameObject.deref().parse_as_object()
                M = Mparent @ _trs(tt)
                act = active and g.m_IsActive           # an inactive object hides its whole subtree
                if act:
                    terr = _comp(g, 'Terrain')
                    mf = _comp(g, 'MeshFilter')
                    lname = g.m_Name.lower()
                    if terr:
                        items.append(('terrain', self.terrain_data(terr[0].parse_as_object().m_TerrainData), M, tag + g.m_Name))
                    # NOTE: renderer.m_Enabled is deliberately NOT tested - scenery renderers are saved disabled and
                    # switched on at runtime.  Collision proxies are excluded by name instead.
                    elif mf and _comp(g, 'MeshRenderer') and not any(k in lname for k in SKIP_NAME_WORDS) \
                            and not COLLISION_PROXY_RE.search(g.m_Name):
                        mp = mf[0].parse_as_object().m_Mesh
                        if mp.path_id:
                            V0, T = self.mesh(mp)
                            if len(V0) and len(T):
                                V = (M[:3, :3] @ V0.T).T + M[:3, 3]
                                foot = max(np.ptp(V[:, 0]), np.ptp(V[:, 2]))
                                solid = bool(_comp(g, 'MeshCollider'))
                                keep = (g.m_Layer == TERRAIN_DEPTH_LAYER
                                        or (solid and foot >= MIN_SOLID_FOOTPRINT_M)
                                        or (STRUCT_INCLUDE_RE.search(g.m_Name) and not DETAIL_RE.search(g.m_Name))
                                        or g.m_Name in extra_land
                                        or (extra_re is not None and extra_re.search(g.m_Name) and solid and tag == 'L24:'))
                                if keep:
                                    items.append(('mesh', V, T, tag + g.m_Name))
                walk(tt, M, act)

        walk(root_t, M0, True)

    def combined_scenery(self, isl):
        """
        [(category, vertices_world[n,3], triangles[m,3])] from the island's scenery-scene mesh combiners (categories
        'struct' / 'tree' / 'bush' / 'rock'; other materials are skipped without decoding).  Only active combiners.
        """
        m = re.match(r'island (\d+)', isl['name'])
        sf = self.scene(int(m.group(1))) if (m and not isl['nested']) else None
        out = []
        if sf is None:
            return out

        def walk(t, Mp, active):
            for ch in t.m_Children:
                tt = ch.deref().parse_as_object()
                g = tt.m_GameObject.deref().parse_as_object()
                M = Mp @ _trs(tt)
                act = active and g.m_IsActive
                if g.m_Name.startswith(COMBINER_PREFIX):
                    cat = scenery_category(g.m_Name[len(COMBINER_PREFIX):].strip(' ()'))
                    if cat and act:
                        for c in tt.m_Children:
                            ct = c.deref().parse_as_object()
                            cg = ct.m_GameObject.deref().parse_as_object()
                            mf = _comp(cg, 'MeshFilter')
                            if cg.m_IsActive and mf and mf[0].parse_as_object().m_Mesh.path_id:
                                V0, T = self.mesh(mf[0].parse_as_object().m_Mesh)
                                if len(V0) and len(T):
                                    Mc = M @ _trs(ct)
                                    out.append((cat, (Mc[:3, :3] @ V0.T).T + Mc[:3, 3], T))
                else:
                    walk(tt, M, act)
        for o in sf.objects.values():
            if o.type.name == 'Transform':
                st = o.parse_as_object()
                if not st.m_Father.path_id:
                    walk(st, _trs(st), True)
        return out

    def collect(self, isl, quirks):
        """All geometry of an island: its subtree in level24 plus (top-level islands only) its scenery scene."""
        go = isl['go']
        items = []
        t = _tf(go)
        self._walk(t, _trs(t), items, 'L24:', quirks)
        m = re.match(r'island (\d+)', go.m_Name)
        sf = self.scene(int(m.group(1))) if (m and not isl['nested']) else None
        if sf:
            for o in sf.objects.values():
                if o.type.name == 'Transform':
                    st = o.parse_as_object()
                    if not st.m_Father.path_id:                    # scene roots
                        self._walk(st, _trs(st), items, 'SC:', quirks)
        return items, _trs(t)[:3, 3]


# --------------------------------------------------------------------------------------------------
# Rasterising
# --------------------------------------------------------------------------------------------------
def _make_raster():
    from numba import njit

    @njit(cache=True)
    def raster(V, T, x0, z0, res, out):
        """Top-down: write the highest surface height of every triangle into `out` (max-combine)."""
        H, W = out.shape
        for k in range(T.shape[0]):
            a, b, c = V[T[k, 0]], V[T[k, 1]], V[T[k, 2]]
            minx = min(a[0], b[0], c[0]); maxx = max(a[0], b[0], c[0])
            minz = min(a[2], b[2], c[2]); maxz = max(a[2], b[2], c[2])
            i0 = max(int(math.floor((minx - x0) / res)), 0); i1 = min(int(math.ceil((maxx - x0) / res)), W - 1)
            j0 = max(int(math.floor((minz - z0) / res)), 0); j1 = min(int(math.ceil((maxz - z0) / res)), H - 1)
            d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
            if abs(d) < 1e-12:
                continue
            for j in range(j0, j1 + 1):
                pz = z0 + j * res
                for i in range(i0, i1 + 1):
                    px = x0 + i * res
                    l1 = ((b[2] - c[2]) * (px - c[0]) + (c[0] - b[0]) * (pz - c[2])) / d
                    l2 = ((c[2] - a[2]) * (px - c[0]) + (a[0] - c[0]) * (pz - c[2])) / d
                    l3 = 1 - l1 - l2
                    if l1 >= -1e-6 and l2 >= -1e-6 and l3 >= -1e-6:
                        y = l1 * a[1] + l2 * b[1] + l3 * c[1]
                        if y > out[j, i]:
                            out[j, i] = y
    return raster


_RASTER = None


def raster(*a):
    global _RASTER
    if _RASTER is None:
        _RASTER = _make_raster()
    return _RASTER(*a)


def is_structure(item, quirks):
    """Is this mesh a man-made structure (drawn red, as a vector polygon) rather than ground?"""
    n = item[-1].split(':', 1)[1]
    if n.endswith('#floor'):                         # dry-dock floor split off a structure mesh: ground
        return False
    if LAND_NAME_RE.match(n):                        # e.g. mountain_shipyard is land despite the word "shipyard"
        return False
    if quirks.get('extra_struct_regex') and item[-1].startswith('L24:') and re.search(quirks['extra_struct_regex'], n):
        return True
    if STRUCT_CLASS_RE.search(n):
        return True
    V = item[1]
    return n.lower().startswith('cube') and max(np.ptp(V[:, 0]), np.ptp(V[:, 2])) < SMALL_CUBE_MAX_M


def build_grid(gd, isl, quirks=None, scenery=False):
    """
    Build the island's height grid (top-down maximum of all land geometry, island-local metres, y = height above
    sea level) plus the vector footprints of structures.  Returns a dict, or None if the island has no geometry.
    """
    import shapely
    from scipy.ndimage import map_coordinates
    quirks = ISLAND_QUIRKS.get(isl['name'], {}) if quirks is None else quirks
    items, origin = gd.collect(isl, quirks)
    o3 = np.array([origin[0], 0, origin[2]])

    # 1. drop stray far-away items
    def centre(it):
        if it[0] == 'mesh':
            V = it[1]
            return np.array([V[:, 0].mean(), V[:, 2].mean()])
        h, (sx, sy, sz) = it[1]
        return terrain_placement(sx, sy, sz, it[2])[0].mean(0)
    kept = []
    for it in items:
        d = np.hypot(*(centre(it) - origin[[0, 2]]))
        if d < FAR_ITEM_LIMIT_M:
            kept.append(it)
        else:
            log('  dropped far item', it[-1], int(d))
    items = kept

    # 2. a Terrain that is entirely under water and does not overlap the island's land meshes is a stray
    land = [i for i in items if i[0] == 'mesh' and not is_structure(i, quirks) and i[1][:, 1].max() > 0
            and max(np.ptp(i[1][:, 0]), np.ptp(i[1][:, 2])) > 100]
    if land:
        av = np.concatenate([i[1] for i in land])
        mb = (av[:, 0].min(), av[:, 0].max(), av[:, 2].min(), av[:, 2].max())
        k2 = []
        for it in items:
            if it[0] == 'terrain':
                h, (sx, sy, sz) = it[1]
                c, _, yo = terrain_placement(sx, sy, sz, it[2])
                tb = (c[:, 0].min(), c[:, 0].max(), c[:, 1].min(), c[:, 1].max())
                ix = max(0, min(tb[1], mb[1]) - max(tb[0], mb[0]))
                iz = max(0, min(tb[3], mb[3]) - max(tb[2], mb[2]))
                frac = ix * iz / ((tb[1] - tb[0]) * (tb[3] - tb[2]))
                peak = h.max() / 32766 * sy + yo
                if peak < 0 and frac < 0.15:
                    log('  dropped misplaced underwater terrain', it[-1], 'overlap', round(frac, 2))
                    continue
            k2.append(it)
        items = k2

    # 3. dry-dock style split: some "structure" meshes contain a floor/ramp that is really ground
    split = []
    for it in items:
        if it[0] == 'mesh' and it[-1] in quirks.get('floor_split', ()):
            Vl = it[1] - o3
            tri = Vl[it[2]]
            mn = tri[:, :, 1].min(1)
            mx = tri[:, :, 1].max(1)
            cen = tri.mean(1)
            wet = mx < 0
            if wet.any():       # footprint = bbox of fully-submerged triangles + 12 m margin
                bx = (cen[wet, 0].min() - 12, cen[wet, 0].max() + 12, cen[wet, 2].min() - 12, cen[wet, 2].max() + 12)
                inreg = (cen[:, 0] >= bx[0]) & (cen[:, 0] <= bx[1]) & (cen[:, 2] >= bx[2]) & (cen[:, 2] <= bx[3])
            else:
                inreg = np.zeros(len(tri), bool)
            floor = (mn < 0) & inreg
            split.append(('mesh', it[1], it[2][floor], it[-1] + '#floor'))
            split.append(('mesh', it[1], it[2][~floor], it[-1]))
        else:
            split.append(it)
    items = split

    # 4. extent and grid
    ext = []
    for it in items:
        if it[0] == 'mesh':
            V = it[1]
            if len(V):
                ext.append((V[:, 0].min(), V[:, 0].max(), V[:, 2].min(), V[:, 2].max()))
        else:
            h, (sx, sy, sz) = it[1]
            c = terrain_placement(sx, sy, sz, it[2])[0]
            ext.append((c[:, 0].min(), c[:, 0].max(), c[:, 1].min(), c[:, 1].max()))
    if not ext:
        return None
    x0 = min(e[0] for e in ext) - origin[0]; x1 = max(e[1] for e in ext) - origin[0]
    z0 = min(e[2] for e in ext) - origin[2]; z1 = max(e[3] for e in ext) - origin[2]
    res = max(GRID_RES_M, max(x1 - x0, z1 - z0) / GRID_MAX_CELLS)
    xs = np.arange(x0, x1 + res, res)
    zs = np.arange(z0, z1 + res, res)
    out = np.full((len(zs), len(xs)), -np.inf)
    X, Z = np.meshgrid(xs, zs)
    for it in items:
        if it[0] == 'mesh':
            if len(it[2]) and not is_structure(it, quirks):      # structures are drawn as polygons, not heights
                raster(it[1] - o3, it[2], xs[0], zs[0], res, out)
        else:
            h, (sx, sy, sz) = it[1]
            _, b, yo = terrain_placement(sx, sy, sz, it[2])
            # unrotated, unscaled: sample (row=z, col=x) at 1 index per sx / sz metres from the terrain corner
            col = (X + origin[0] - b[0]) / sx
            row = (Z + origin[2] - b[1]) / sz
            v = map_coordinates(h, [row, col], order=1, cval=np.nan)
            out = np.fmax(out, yo + v / 32766 * sy)
    comb = gd.combined_scenery(isl) if scenery else []
    for cat, V, T in comb:                                   # --scenery: baked rocks raise the ground like any other mesh
        if cat == 'rock':
            raster(V - o3, T, xs[0], zs[0], res, out)
    out[np.isneginf(out)] = np.nan

    # 5. structures -> vector footprints (above water / fully submerged / dock poles)
    above, sub, poles = [], [], []
    for it in items:
        if it[0] == 'mesh' and is_structure(it, quirks):
            tri = (it[1] - o3)[it[2]]
            top = tri[:, :, 1].max()
            xy = tri[:, :, [0, 2]]
            a = 0.5 * np.abs((xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1]) - (xy[:, 2, 0] - xy[:, 0, 0]) * (xy[:, 1, 1] - xy[:, 0, 1]))
            xy = xy[a > 1e-4]                                       # vertical faces project to nothing
            if len(xy) == 0:
                continue
            g = shapely.union_all(shapely.buffer(shapely.polygons(xy), 0.02))
            nm = it[-1].split(':', 1)[1]
            (poles if POLE_RE.search(nm) else (above if top >= -0.3 else sub)).append(g)
    veg = None
    if scenery:
        def footprint(V, T, min_top):
            """
            Footprint polygon of a baked scenery mesh.  These meshes have 10^4-10^5 triangles; unioning them with shapely takes
            tens of seconds, so rasterise to a binary mask (<= 0.5 m cells) and trace its outline with contourpy instead.
            Thin features (fences) become about one cell wide, i.e. slightly exaggerated.
            """
            import contourpy
            tri = (V - o3)[T]
            tri = tri[tri[:, :, 1].max(1) >= min_top]                # drop parts that are under water
            if len(tri) == 0:
                return None
            mres = min(res, 0.5)
            mx = np.arange(xs[0], xs[-1] + mres, mres); mz = np.arange(zs[0], zs[-1] + mres, mres)
            mask = np.full((len(mz), len(mx)), -np.inf)
            flat = tri.copy(); flat[:, :, 1] = 1.0
            raster(flat.reshape(-1, 3), np.arange(len(tri) * 3, dtype=np.int64).reshape(-1, 3), mx[0], mz[0], mres, mask)
            m = np.pad((mask > 0).astype(np.float64), 1)            # pad so shapes touching the edge close
            if not m.any():
                return None
            gen = contourpy.contour_generator(np.r_[mx[0] - mres, mx, mx[-1] + mres], np.r_[mz[0] - mres, mz, mz[-1] + mres], m,
                                              fill_type=contourpy.FillType.OuterOffset)
            pts, offs = gen.filled(0.5, 2.0)
            polys = []
            for p, o in zip(pts, offs):
                rings = [p[o[i]:o[i + 1]] for i in range(len(o) - 1)]
                if len(rings[0]) >= 4:
                    polys.append(shapely.Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4]))
            return shapely.union_all(polys) if polys else None
        groups = {'struct': [], 'tree': [], 'bush': []}
        for cat, V, T in comb:
            if cat in groups:
                g = footprint(V, T, -0.3 if cat == 'struct' else 0.0)
                if g is not None:
                    groups[cat].append(g)
        above += groups['struct']
        union = lambda gs: shapely.union_all(gs) if gs else None
        veg = (union(groups['tree']), union(groups['bush']))
    A = shapely.union_all(above) if above else None
    S = shapely.union_all(sub) if sub else None
    PL = shapely.union_all(poles) if poles else None
    if A is not None and S is not None:
        S = S.difference(A)
    return dict(xs=xs, zs=zs, Y=out.astype(np.float32), polys=(A, S, PL), items=[(i[0], i[-1]) for i in items], res=res, veg=veg)


def grid_summary(grid):
    """Small numeric fingerprint of a grid (used by selftest; independent of JPEG encoding)."""
    Y = grid['Y']
    A, S, PL = grid['polys']
    area = lambda g: 0.0 if g is None else float(g.area)
    ng = lambda g: 0 if g is None else len(getattr(g, 'geoms', [g]))
    return dict(shape=list(Y.shape), x0=float(grid['xs'][0]), x1=float(grid['xs'][-1]), z0=float(grid['zs'][0]), z1=float(grid['zs'][-1]),
                res=float(grid['res']), nan_cells=int(np.isnan(Y).sum()), ymin=float(np.nanmin(Y)), ymax=float(np.nanmax(Y)),
                ymean=float(np.nanmean(Y)), above_sea_cells=int((Y >= 0).sum()),
                n_items=len(grid['items']), struct_area=round(area(A), 3), struct_shapes=ng(A), sub_shapes=ng(S),
                pole_shapes=ng(PL), pole_area=round(area(PL), 3))


# --------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------
def render_chart(grid, title, out_path, final_px=2000, quality=80, show_key=True):
    """
    Render a grid to an image.  The layout is designed for a 10,000 px long side; smaller outputs lower the
    save DPI so everything scales together (line widths get a boost so they stay visible).
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource, to_rgb
    from matplotlib.path import Path
    from matplotlib.patches import PathPatch, Rectangle, Circle
    from scipy import ndimage

    LONG = LAYOUT_PX
    SC = final_px / LONG
    LWB = max(1.0, 1.0 / (SC * 2.0))
    PT = 0.72                      # points per px at dpi=100
    LWPT = PT * LWB
    xs, zs, Y = grid['xs'], grid['zs'], grid['Y'].astype(np.float32)
    A, S, PL = grid['polys']
    RES = float(xs[1] - xs[0])
    nan = np.isnan(Y)
    Y = np.where(nan, -200, Y)

    # --- crop: bounding box of everything shallower than the shelf edge, grown to contain all structures
    jj, ii = np.where(Y > -SHELF_EDGE_DEPTH_M)
    if jj.size == 0:           # nothing above the shelf threshold (e.g. only structures): use the whole grid
        jj = np.array([0, Y.shape[0] - 1]); ii = np.array([0, Y.shape[1] - 1])
    j0, j1, i0, i1 = jj.min(), jj.max(), ii.min(), ii.max()
    bs = [g.bounds for g in (A, S, PL) if g is not None and not g.is_empty]
    if bs:
        m = CROP_STRUCTURE_MARGIN_M
        bx0 = min(b[0] for b in bs) - m; by0 = min(b[1] for b in bs) - m
        bx1 = max(b[2] for b in bs) + m; by1 = max(b[3] for b in bs) + m
        i0 = min(i0, max(int((bx0 - xs[0]) / RES), 0)); i1 = max(i1, min(int((bx1 - xs[0]) / RES) + 1, len(xs) - 1))
        j0 = min(j0, max(int((by0 - zs[0]) / RES), 0)); j1 = max(j1, min(int((by1 - zs[0]) / RES) + 1, len(zs) - 1))
    Y = Y[j0:j1 + 1, i0:i1 + 1]; nan = nan[j0:j1 + 1, i0:i1 + 1]; xs = xs[i0:i1 + 1]; zs = zs[j0:j1 + 1]
    ext = [xs[0] - RES / 2, xs[-1] + RES / 2, zs[0] - RES / 2, zs[-1] + RES / 2]
    Wm = ext[1] - ext[0]; Hm = ext[3] - ext[2]
    pxm = LONG / max(Wm, Hm)
    wpx, hpx = int(round(Wm * pxm)), int(round(Hm * pxm))

    depth = -Y
    land = Y >= 0
    # enclosed holes in the mesh data are "no data", not deep water
    lab, n = ndimage.label(nan)
    sz = np.bincount(lab.ravel())
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    nodata = np.isin(lab, [k for k in range(1, n + 1) if k not in border and sz[k] >= 1600])

    # --- colour image
    cols = [to_rgb(BAND_COLORS[k]) for k in ('land', '0-2', '2-5', '5-10', '10+')]
    d_idx = np.digitize(depth, [0, 2, 5, 9.95])
    img = np.zeros(Y.shape + (3,), np.float32)
    for i, c in enumerate(cols):
        img[d_idx == i] = c
    m510 = (depth >= 5) & (depth < 10)
    tg = np.clip((depth - 5) / 5, 0, 1)[m510][:, None]
    img[m510] = np.array(cols[3]) * (1 - tg) + np.array(cols[4]) * tg
    lc = plt.cm.YlOrBr(np.clip(Y / max(Y.max(), 1), 0, 1) * 0.55 + 0.05)[..., :3]
    img[land] = 0.55 * np.array(to_rgb(BAND_COLORS['land'])) + 0.45 * lc[land]
    img[nodata] = to_rgb('#d4d4d4')
    sh = LightSource(315, 40).hillshade(np.where(land, Y, 0).astype(np.float64), vert_exag=2, dx=RES, dy=RES)
    img = np.clip(img * (0.75 + 0.35 * sh[..., None]), 0, 1)
    del lc, sh

    fig = plt.figure(figsize=(wpx / 100, hpx / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(img, origin='lower', extent=ext, interpolation='antialiased', zorder=0)
    del img

    # --- contours
    Ym = np.ma.masked_where(nan, Y)
    Dm = np.ma.masked_where(nan, depth)
    C_FINE, C_MED, C_LAND = '#17406b', '#0a2748', '#6b4a1f'
    W_FINE, W_MED, W_LAND, W_SHORE = 2.5, 5, 3, 6

    def cs(z, levels, color, px, alpha=1):
        ax.contour(xs, zs, z, levels=levels, colors=color, linewidths=px * LWPT, alpha=alpha, zorder=2)
    cs(Dm, [1, 2, 3, 4, 6, 7, 8, 9], C_FINE, W_FINE, 0.9)
    cs(Dm, [5], C_MED, W_MED)
    if Y.max() > 10:
        cs(Ym, np.arange(10, Y.max(), 10), C_LAND, W_LAND)
    cs(Ym, [0], 'k', W_SHORE)

    # --- structures (vector)
    def patches(geom, **kw):
        res = []
        if geom is None or geom.is_empty:
            return res
        for g in getattr(geom, 'geoms', [geom]):
            if g.geom_type != 'Polygon' or g.is_empty:
                continue
            verts, codes = [], []
            for ring in [g.exterior] + list(g.interiors):
                c = np.asarray(ring.coords)
                verts += list(c)
                codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
            res.append(PathPatch(Path(verts, codes), **kw))
        return res
    RED, DARK = '#c62828', '#4a0a0a'
    veg = grid.get('veg')                                   # (trees, bushes) polygons when built with --scenery
    if veg:
        for geom, kind in ((veg[1], 'bushes'), (veg[0], 'trees')):            # bushes under trees, both under structures
            for p in patches(geom, facecolor=SCENERY_COLORS[kind], edgecolor='none', alpha=0.7, zorder=5):
                ax.add_patch(p)
    for p in patches(A, facecolor=RED, edgecolor=DARK, lw=3 * LWPT, joinstyle='round', zorder=6):
        ax.add_patch(p)
    if S is not None and not S.is_empty:
        for p in patches(S, facecolor='#f4b4b0', edgecolor=RED, hatch='////', lw=3 * LWPT, zorder=6):
            ax.add_patch(p)
    if PL is not None:
        for p in patches(PL.buffer(POLE_DRAW_RADIUS_M), facecolor='#ffffff', edgecolor=DARK, lw=4 * LWPT, zorder=8):
            ax.add_patch(p)
        for p in patches(PL, facecolor=RED, edgecolor='none', zorder=9):
            ax.add_patch(p)

    # --- annotations: all inside the border; each panel is sized from its measured text
    sel = depth[(depth > 0) & (depth < SHELF_EDGE_DEPTH_M) & ~nan]
    vals, cnt = np.unique(np.round(sel, 1), return_counts=True)
    NOTE2 = [('note', 'Open seabed on the shelf is a flat %.1f m' % vals[cnt.argmax()], 80, 190)] if (sel.size and cnt.max() > 0.3 * len(sel)) else []
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3]); ax.set_aspect('auto')
    R = fig.canvas.get_renderer()
    X = lambda px: ext[0] + px / pxm
    Z = lambda px: ext[2] + px / pxm
    box = lambda x0, y0, w, h, **kw: ax.add_patch(Rectangle((X(x0), Z(y0)), w / pxm, h / pxm, zorder=10, **kw))
    txt = lambda x, y, s, size, **kw: ax.text(X(x), Z(y), s, fontsize=size * PT, zorder=12, **kw)

    def tm(s, size, **kw):
        t = ax.text(X(0), Z(0), s, fontsize=size * PT, **kw)
        bb = t.get_window_extent(R)
        t.remove()
        return bb.width, bb.height
    M, PAD = 100, 70
    FRAME = dict(facecolor='white', edgecolor='k', lw=4 * LWPT, alpha=0.92)
    fitlog = []

    def fits(name, rects, panel):
        px0, py0, px1, py1 = panel
        fitlog.append((name, all(r[0] >= px0 - 1 and r[1] >= py0 - 1 and r[2] <= px1 + 1 and r[3] <= py1 + 1 for r in rects)))

    tw_, th_ = tm(title, 190, fontweight='bold')
    bw, bh = tw_ + 2 * PAD, th_ + 2 * PAD * 0.8
    sbm = max(v for v in (10, 20, 25, 50, 100, 200, 250, 500, 1000) if v <= 0.3 * Wm) if Wm >= 34 else 10
    sbw = sbm * pxm
    LS = 95
    w0, h0 = tm('0', LS, fontweight='bold')
    w5, h5 = tm('%d m' % sbm, LS, fontweight='bold')
    bar_off = max(PAD, w0 / 2 + PAD * 0.5)
    panel_w = bar_off + sbw + max(PAD, w5 / 2 + PAD * 0.5)
    panel_h = PAD * 0.6 + 50 + 25 + h5 + PAD * 0.6

    KS, KP, SW = 88, 80, 260
    TX = KP + SW + 70
    rows = [('title', 'DEPTH BELOW SEA LEVEL', 100, 170),
            ('band', '0 – 2 m', '0-2', 150), ('band', '2 – 5 m', '2-5', 150), ('band', '5 – 10 m', '5-10', 150),
            ('note', 'Map edge: where the shelf drops below 10 m', 80, 110 if NOTE2 else 190), *NOTE2,
            ('line', C_FINE, W_FINE, 'Depth contour, every 1 m (thin)', 150), ('line', C_MED, W_MED, 'Depth contour, every 5 m (bold)', 150),
            ('line', 'k', W_SHORE, 'Shoreline (0 m)', 150), ('line', C_LAND, W_LAND, 'Land contour, every 10 m', 190),
            ('struct', 'Quay, dock, wall or building', 150),
            *([('veg', 'Trees', 'trees', 150)] if (veg and veg[0] is not None and not veg[0].is_empty) else []),
            *([('veg', 'Bushes and plants', 'bushes', 150)] if (veg and veg[1] is not None and not veg[1].is_empty) else []),
            *([('nodata', 'No data (not modelled)', 150)] if nodata.any() else []),
            *([('sub', 'Submerged structure', 150)] if (S is not None and not S.is_empty) else []),
            ('pole', 'Dock pole (drawn oversize) - hazard', 0)]
    wmax = 0
    for r in rows:
        if r[0] == 'title': wmax = max(wmax, KP + tm(r[1], r[2], fontweight='bold')[0])
        elif r[0] == 'note': wmax = max(wmax, KP + tm(r[1], r[2], style='italic')[0])
        elif r[0] == 'band': wmax = max(wmax, TX + tm(r[1], KS)[0])
        elif r[0] == 'line': wmax = max(wmax, TX + tm(r[3], KS)[0])
        else: wmax = max(wmax, TX + tm(r[1], KS)[0])
    kw_ = wmax + KP
    kh = KP + sum(r[-1] for r in rows[:-1]) + 110 + KP * 0.4

    # panel corners: each panel takes its preferred corner unless that corner covers land / no-data
    NW, NH = 240, 520
    busy = (Y >= -0.5) | nodata

    def overlap(x0, y0, w, h):
        a0 = max(int(x0 / pxm / RES), 0); a1 = min(int((x0 + w) / pxm / RES) + 1, busy.shape[1])
        b0 = max(int(y0 / pxm / RES), 0); b1 = min(int((y0 + h) / pxm / RES) + 1, busy.shape[0])
        s_ = busy[b0:b1, a0:a1]
        return float(s_.mean()) if s_.size else 0.0

    def rect_at(corner, w, h):
        return ((M if corner[1] == 'L' else wpx - M - w), (M if corner[0] == 'B' else hpx - M - h))
    SIZES = {'key': (kw_, kh), 'scale': (panel_w, panel_h), 'title': (bw, bh), 'north': (NW, NH)}
    DEFAULT = {'key': 'BR', 'scale': 'BL', 'title': 'TL', 'north': 'TR'}
    used, POS, POS_CORNER = set(), {}, {}
    order = ('north', 'key', 'scale', 'title') if show_key else ('north', 'scale', 'title')
    for name in order:
        w, h = SIZES[name]
        d = DEFAULT[name]
        if d not in used and overlap(*rect_at(d, w, h), w, h) <= 0.02:
            c = d
        else:
            c = min([k for k in ('TL', 'TR', 'BL', 'BR') if k not in used], key=lambda k: overlap(*rect_at(k, w, h), w, h))
        used.add(c); POS[name] = rect_at(c, w, h); POS_CORNER[name] = c

    bx, by = POS['title']
    box(bx, by, bw, bh, **FRAME)
    t = txt(bx + PAD, by + bh / 2, title, 190, fontweight='bold', va='center', ha='left')
    bb = t.get_window_extent(R)
    fits('title', [(bb.x0, bb.y0, bb.x1, bb.y1)], (bx, by, bx + bw, by + bh))
    nx0, ny0 = POS['north']
    ncx = nx0 + (NW - 120 if POS_CORNER['north'][1] == 'R' else 120)
    ax.annotate('N', xy=(X(ncx), Z(ny0 + NH)), xytext=(X(ncx), Z(ny0 + NH - 420)), ha='center', va='top', fontsize=150 * PT,
                fontweight='bold', zorder=12, arrowprops=dict(arrowstyle='-|>', color='k', lw=12 * LWPT, mutation_scale=70))
    sx0, sy0 = POS['scale']
    bar_x = sx0 + bar_off
    box(sx0, sy0, panel_w, panel_h, **FRAME)
    for k in range(5):
        box(bar_x + k * sbw / 5, sy0 + PAD * 0.6, sbw / 5, 50, facecolor='k' if k % 2 == 0 else 'white', edgecolor='k', lw=3 * LWPT)
    t0_ = txt(bar_x, sy0 + PAD * 0.6 + 50 + 25, '0', LS, ha='center', va='bottom', fontweight='bold')
    t5_ = txt(bar_x + sbw, sy0 + PAD * 0.6 + 50 + 25, '%d m' % sbm, LS, ha='center', va='bottom', fontweight='bold')
    fits('scale', [(b_.x0, b_.y0, b_.x1, b_.y1) for b_ in (t0_.get_window_extent(R), t5_.get_window_extent(R))], (sx0, sy0, sx0 + panel_w, sy0 + panel_h))

    if show_key:
        kx, ky = POS['key']
        box(kx, ky, kw_, kh, **FRAME)
        y = ky + kh - KP * 0.9
        rects = []

        def rec(t):
            b_ = t.get_window_extent(R)
            rects.append((b_.x0, b_.y0, b_.x1, b_.y1))
        for r in rows:
            if r[0] == 'title':
                rec(txt(kx + KP, y, r[1], r[2], fontweight='bold', va='center'))
            elif r[0] == 'band':
                if r[2] == '5-10':
                    nn = 40
                    for q in range(nn):
                        tq = q / (nn - 1)
                        box(kx + KP + q * SW / nn, y - 55, SW / nn + 1, 110, facecolor=tuple(np.array(cols[3]) * (1 - tq) + np.array(cols[4]) * tq), edgecolor='none')
                    box(kx + KP, y - 55, SW, 110, facecolor='none', edgecolor='k', lw=2 * LWPT)
                else:
                    box(kx + KP, y - 55, SW, 110, facecolor=BAND_COLORS[r[2]], edgecolor='k', lw=2 * LWPT)
                rec(txt(kx + TX, y, r[1], KS, va='center'))
            elif r[0] == 'note':
                rec(txt(kx + KP, y, r[1], r[2], va='center', style='italic'))
            elif r[0] == 'line':
                ax.plot([X(kx + KP), X(kx + KP + SW)], [Z(y), Z(y)], color=r[1], lw=r[2] * LWPT, zorder=12, solid_capstyle='butt')
                rec(txt(kx + TX, y, r[3], KS, va='center'))
            elif r[0] == 'struct':
                box(kx + KP, y - 55, SW, 110, facecolor=RED, edgecolor=DARK, lw=3 * LWPT)
                rec(txt(kx + TX, y, r[1], KS, va='center'))
            elif r[0] == 'veg':
                box(kx + KP, y - 55, SW, 110, facecolor=SCENERY_COLORS[r[2]], edgecolor='k', lw=2 * LWPT, alpha=0.8)
                rec(txt(kx + TX, y, r[1], KS, va='center'))
            elif r[0] == 'nodata':
                box(kx + KP, y - 55, SW, 110, facecolor='#d4d4d4', edgecolor='k', lw=2 * LWPT)
                rec(txt(kx + TX, y, r[1], KS, va='center'))
            elif r[0] == 'sub':
                box(kx + KP, y - 55, SW, 110, facecolor='#f4b4b0', edgecolor=RED, lw=3 * LWPT, hatch='////')
                rec(txt(kx + TX, y, r[1], KS, va='center'))
            elif r[0] == 'pole':
                cx = kx + KP + SW / 2
                ax.add_patch(Circle((X(cx), Z(y)), 55 / pxm, facecolor='white', edgecolor=DARK, lw=4 * LWPT, zorder=12))
                ax.add_patch(Circle((X(cx), Z(y)), 28 / pxm, facecolor=RED, edgecolor='none', zorder=13))
                rec(txt(kx + TX, y, r[1], KS, va='center'))
            y -= r[-1]
        fits('key', rects, (kx, ky, kx + kw_, ky + kh))
    kwargs = dict(quality=quality, subsampling=0, optimize=False) if out_path.lower().endswith(('.jpg', '.jpeg')) else {}
    fig.savefig(out_path, dpi=100 * SC, pil_kwargs=kwargs)
    plt.close(fig)
    return dict(panel_fit=fitlog, canvas_design_px=(wpx, hpx), crop_cells=Y.shape)


def slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')


# --------------------------------------------------------------------------------------------------
# Sub-commands
# --------------------------------------------------------------------------------------------------
def cmd_list(args):
    gd = GameData(args.game_data)
    log(f'{"name":42s} {"parent":28s} children  scene  note')
    for isl in gd.islands():
        m = re.match(r'island (\d+)', isl['name'])
        scene = '-' if (isl['nested'] or not m) else ('yes' if gd.scene(int(m.group(1))) is not None else 'MISSING')
        notes = []
        if isl['nested']: notes.append('nested')
        if isl.get('mod'): notes.append('MOD')
        if not isl['active']: notes.append('inactive')
        if isl['name'] in ISLAND_QUIRKS: notes.append('quirk')
        log(f'{isl["name"]:42s} {isl["parent"]:28s} {isl["children"]:8d}  {scene:6s} {" ".join(notes)}')


def manifest_dict(gd):
    out = {}
    for isl in gd.islands():
        t = _tf(isl['go'])
        n_terrain = sum(1 for ch in t.m_Children if _comp(ch.deref().parse_as_object().m_GameObject.deref().parse_as_object(), 'Terrain'))
        out[isl['name']] = dict(parent=isl['parent'], children=isl['children'], terrains=n_terrain, nested=isl['nested'], active=isl['active'])
    return out


def cmd_manifest(args):
    path = os.path.join(REFERENCE_DIR, 'islands_manifest.json')
    cur = manifest_dict(GameData(args.game_data))
    if args.check:
        ref = json.load(open(path))
        added = sorted(set(cur) - set(ref)); removed = sorted(set(ref) - set(cur))
        changed = sorted(n for n in set(cur) & set(ref) if cur[n] != ref[n])
        log('NEW islands (render and review these):', added or 'none')
        log('REMOVED islands:', removed or 'none')
        for n in changed:
            log('CHANGED:', n, ref[n], '->', cur[n])
        sys.exit(1 if (added or removed or changed) else 0)
    json.dump(cur, open(path, 'w'), indent=1, sort_keys=True)
    log('wrote', path, len(cur), 'islands')


def _render_one(game_data, name, out_dir, size, quality, show_key, title, dump_dir, scenery=False):
    gd = GameData(game_data)
    isl = gd.find(name)
    t0 = time.time()
    grid = build_grid(gd, isl, scenery=scenery)
    if grid is None:
        log(f'SKIP {name}: no geometry')
        return False
    if dump_dir:
        np.savez(os.path.join(dump_dir, slug(name) + '_grid.npz'), xs=grid['xs'], zs=grid['zs'], Y=grid['Y'])
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, slug(name) + ('_scenery' if scenery else '') + '.jpg')
    info = render_chart(grid, title or name, out, size, quality, show_key)
    log(f'OK   {name}  -> {out}  ({time.time() - t0:.0f}s)  fit={all(ok for _, ok in info["panel_fit"])}')
    return True


def cmd_render(args):
    gd = GameData(args.game_data)
    names = [i['name'] for i in gd.islands()] if args.all else args.names
    if not names:
        raise SystemExit('give island names or --all')
    if args.title and len(names) != 1:
        raise SystemExit('--title only makes sense with one island')
    global GRID_MAX_CELLS
    GRID_MAX_CELLS = args.max_cells
    if len(names) == 1:
        _render_one(args.game_data, names[0], args.out, args.size, args.quality, not args.no_key,
                    args.title or (DISPLAY_TITLES.get(names[0]) if args.display_titles else None), args.dump_grid, args.scenery)
        return
    # many islands: one subprocess each (keeps memory bounded, isolates failures), --jobs at a time
    def job(name):
        cmd = [sys.executable, '-I', os.path.abspath(__file__), '--game-data', args.game_data] + (['--mods'] if MODS else []) + [ 'render', name, '--out', args.out,
               '--size', str(args.size), '--quality', str(args.quality)] + (['--no-key'] if args.no_key else []) \
              + (['--display-titles'] if args.display_titles else []) + (['--dump-grid', args.dump_grid] if args.dump_grid else []) \
              + ['--max-cells', str(args.max_cells)] + (['--scenery'] if args.scenery else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or [''])[-1]
        if r.returncode != 0:
            tail = f'FAIL {name}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}'
        log(tail)
        return r.returncode == 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        ok = list(ex.map(job, names))
    log(f'{sum(ok)}/{len(names)} rendered in {time.time() - t0:.0f}s')


def cmd_selftest(args):
    """Albacore Island (a tricky island: terrain-only land, rotated+scaled parent, pier, no key) must reproduce the reference."""
    from PIL import Image
    ref_img = os.path.join(REFERENCE_DIR, 'island_4_A_fish_island_2k_q80_nokey.jpg')
    gd = GameData(args.game_data)
    grid = build_grid(gd, gd.find(SELFTEST_ISLAND))
    got = grid_summary(grid)
    if args.update:       # deliberate re-baselining after an intentional change to the pipeline
        json.dump(got, open(os.path.join(REFERENCE_DIR, 'albacore_summary.json'), 'w'), indent=1)
        render_chart(grid, SELFTEST_ISLAND, ref_img, 2000, 80, show_key=False)
        log('reference updated:', ref_img)
        return
    ref_json = json.load(open(os.path.join(REFERENCE_DIR, 'albacore_summary.json')))
    bad = []
    for k, v in ref_json.items():
        g = got[k]
        same = (g == v) if isinstance(v, (list, int)) else math.isclose(g, v, rel_tol=1e-9, abs_tol=1e-6)
        if not same:
            bad.append((k, v, g))
    log('grid summary:', 'MATCH' if not bad else 'MISMATCH')
    for b in bad:
        log('   ', b)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'albacore.jpg')
        render_chart(grid, SELFTEST_ISLAND, out, 2000, 80, show_key=False)
        a = np.asarray(Image.open(out).convert('RGB')).astype(int)
        b = np.asarray(Image.open(ref_img).convert('RGB')).astype(int)
        if a.shape != b.shape:
            log('image shape differs', a.shape, b.shape); bad.append('image shape')
        else:
            d = np.abs(a - b)
            log(f'image diff vs reference: max={d.max()} mean={d.mean():.6f} differing pixels={(d.sum(2) > 0).sum()}')
            if d.max() > 2:
                bad.append('image differs')
    log('SELFTEST', 'PASS' if not bad else 'FAIL')
    sys.exit(0 if not bad else 1)


# ---- exploration helpers (for working out a new / odd island) ------------------------------------
def _script_names(gd):
    """{(external file, path_id): MonoScript class name} for the MonoBehaviours in level24 (typetrees need Assembly-CSharp)."""
    import struct
    ext = [e.path for e in gd.l24.externals]
    need = {}
    for o in gd.l24.objects.values():
        if o.type.name == 'MonoBehaviour':
            b = o.get_raw_data()
            fid, pid = struct.unpack_from('<iq', b, 16)       # m_Script PPtr follows GameObject PPtr + enabled flag
            need.setdefault(ext[fid - 1] if fid else 'level24', set()).add(pid)
    names = {}
    for fn, pids in need.items():
        try:
            e = gd._UnityPy.load(gd.dir + fn)
        except Exception:
            continue
        for o in e.objects:
            if o.path_id in pids and o.type.name == 'MonoScript':
                names[(fn, o.path_id)] = o.parse_as_object().m_ClassName
    return names, ext


def cmd_explore(args):
    gd = GameData(args.game_data)
    if args.what == 'layers':
        env = gd._UnityPy.load(gd.dir + 'globalgamemanagers')
        for o in env.objects:
            if o.type.name == 'TagManager':
                t = o.parse_as_object()
                log([(i, l) for i, l in enumerate(t.layers) if l])
        return
    isl = gd.find(args.name)
    go = isl['go']
    if args.what == 'tree':
        import struct
        names, ext = _script_names(gd)

        def cname(r):
            if r.type.name != 'MonoBehaviour':
                return r.type.name
            b = r.get_raw_data()
            fid, pid = struct.unpack_from('<iq', b, 16)
            return 'MB:' + names.get((ext[fid - 1] if fid else 'level24', pid), '?')

        def show(g, t, ind):
            p, s = t.m_LocalPosition, t.m_LocalScale
            log(f'{ind}{g.m_Name} [{"on" if g.m_IsActive else "OFF"}] layer={g.m_Layer} pos=({p.x:.1f},{p.y:.1f},{p.z:.1f}) '
                f'scale=({s.x:.2f},{s.y:.2f},{s.z:.2f}) {[cname(c.deref()) for c in g.m_Components if c.deref().type.name != "Transform"]}')

        def walk(t, ind, d):
            for ch in t.m_Children:
                tt = ch.deref().parse_as_object()
                g = tt.m_GameObject.deref().parse_as_object()
                show(g, tt, ind)
                if d < args.depth:
                    walk(tt, ind + '  ', d + 1)
        t = _tf(go)
        show(go, t, '')
        walk(t, '  ', 1)
    elif args.what == 'near':
        x0, x1, z0, z1 = args.rect
        t = _tf(go)
        P = _trs(t)
        origin = P[[0, 2], 3]
        rows = []

        def scan(root_t, M0, tag):
            def walk(t_, Mp, act):
                for ch in t_.m_Children:
                    tt = ch.deref().parse_as_object(); g = tt.m_GameObject.deref().parse_as_object()
                    M = Mp @ _trs(tt); a = act and g.m_IsActive
                    for tr in _comp(g, 'Terrain'):
                        h, (sx, sy, sz) = gd.terrain_data(tr.parse_as_object().m_TerrainData)
                        c, _, yo = terrain_placement(sx, sy, sz, M)
                        rows.append((tag, g.m_Name + ' [TERRAIN]', 'on' if a else 'OFF', round(512 * sx), np.round(c.mean(0) - origin).astype(int).tolist(), round(yo, 1), round(float(h.max()) / 32766 * sy + yo, 1), 0))
                    mf = _comp(g, 'MeshFilter')
                    if mf and mf[0].parse_as_object().m_Mesh.path_id:
                        V0, T = gd.mesh(mf[0].parse_as_object().m_Mesh)
                        if len(V0):
                            V = (M[:3, :3] @ V0.T).T + M[:3, 3]
                            V[:, 0] -= origin[0]; V[:, 2] -= origin[1]
                            bx = (V[:, 0].min(), V[:, 0].max(), V[:, 2].min(), V[:, 2].max())
                            foot = max(bx[1] - bx[0], bx[3] - bx[2])
                            if bx[1] >= x0 and bx[0] <= x1 and bx[3] >= z0 and bx[2] <= z1 and foot >= args.min_foot:
                                mr = _comp(g, 'MeshRenderer')
                                rows.append((tag, g.m_Name, 'on' if a else 'OFF', round(foot), np.round(V[:, [0, 2]].mean(0)).astype(int).tolist(),
                                             round(V[:, 1].min(), 1), round(V[:, 1].max(), 1), len(T), 'rend-on' if (mr and mr[0].parse_as_object().m_Enabled) else ('rend-off' if mr else 'no-rend'),
                                             'collider' if _comp(g, 'MeshCollider') else '-', 'layer', g.m_Layer))
                    walk(tt, M, a)
            walk(root_t, M0, True)
        scan(t, P, 'L24')
        m = re.match(r'island (\d+)', go.m_Name)
        sf = gd.scene(int(m.group(1))) if (m and not isl['nested']) else None
        if sf:
            for o in sf.objects.values():
                if o.type.name == 'Transform':
                    st = o.parse_as_object()
                    if not st.m_Father.path_id:
                        scan(st, _trs(st), 'SC')
        for r in sorted(rows, key=lambda r: -r[3])[:args.limit]:
            log(r)
    elif args.what == 'terrain-check':
        _terrain_check(gd, isl)


def _terrain_check(gd, isl):
    """Compare two Terrain placement rules against the island's mesh land: (H1) apply parent rotation/scale (WRONG) vs
    (H2) position only (what Unity does).  A correct rule makes terrain land overlap mesh land (IoU well above 0)."""
    from scipy.ndimage import map_coordinates
    quirks = ISLAND_QUIRKS.get(isl['name'], {})
    items, origin = gd.collect(isl, quirks)
    ox, oz = origin[0], origin[2]
    meshes = [i for i in items if i[0] == 'mesh' and i[-1].startswith('L24:') and not is_structure(i, quirks) and not STRUCT_CLASS_RE.search(i[-1])]
    terr = [i for i in items if i[0] == 'terrain']
    if not meshes or not terr:
        log('needs both land meshes and a Terrain to compare'); return
    allv = np.concatenate([m[1] for m in meshes])
    res = 2.0
    xs = np.arange(allv[:, 0].min() - ox, allv[:, 0].max() - ox, res)
    zs = np.arange(allv[:, 2].min() - oz, allv[:, 2].max() - oz, res)
    X, Z = np.meshgrid(xs, zs)
    mh = np.full(X.shape, -np.inf)
    for m in meshes:
        raster(m[1] - np.array([ox, 0, oz]), m[2], xs[0], zs[0], res, mh)
    mland = mh > 0
    for t in terr:
        h, (sx, sy, sz) = t[1]
        M = t[2]

        def sample(A, b, ysc, yoff):
            L = np.linalg.inv(A) @ np.stack([(X - b[0]).ravel(), (Z - b[1]).ravel()])
            v = map_coordinates(h, [L[1] / sz, L[0] / sx], order=1, cval=np.nan).reshape(X.shape)
            return yoff + ysc * v / 32766 * sy
        H1 = None if (abs(M[1, 0]) > 1e-6 or abs(M[1, 2]) > 1e-6) else sample(M[[0, 2]][:, [0, 2]], M[[0, 2], 3] - np.array([ox, oz]), M[1, 1], M[1, 3])
        H2 = sample(np.eye(2), M[[0, 2], 3] - np.array([ox, oz]), 1.0, M[1, 3])
        log(t[-1])
        for lab, H in (('H1 parent rotation+scale applied (wrong)', H1), ('H2 position only (Unity rule)', H2)):
            if H is None:
                log(f'   {lab}: not applicable (parent is tilted)'); continue
            tl = np.nan_to_num(H, nan=-99) > 0
            iou = (tl & mland).sum() / max((tl | mland).sum(), 1)
            peak = float(np.nanmax(H)) if np.isfinite(H).any() else float('nan')
            log(f'   {lab}: terrain-above-sea cells={tl.sum()}  IoU with mesh land={iou:.2f}  peak={peak:.1f} m')
        log('   (compare H1 with H2: the correct rule overlaps the meshes far more. Absolute IoU depends on how much of the '
            'land the meshes cover; where meshes are only hills on a terrain base it stays modest.)')


def compose_grids(parts, res):
    """
    Composite several island grids (each island-local, i.e. world-aligned minus its own origin) into one grid in a shared
    frame.  parts = [(grid, origin_xz)].  Heights combine by maximum, polygons are translated and unioned.
    The composite frame is world coordinates minus the first island's origin.
    """
    import shapely
    from shapely import affinity
    ref = np.array(parts[0][1], float)
    boxes = []
    for g, o in parts:
        o = np.array(o, float) - ref
        boxes.append((o[0] + g['xs'][0], o[0] + g['xs'][-1], o[1] + g['zs'][0], o[1] + g['zs'][-1]))
    x0 = min(b[0] for b in boxes); x1 = max(b[1] for b in boxes); z0 = min(b[2] for b in boxes); z1 = max(b[3] for b in boxes)
    xs = np.arange(x0, x1 + res, res); zs = np.arange(z0, z1 + res, res)
    out = np.full((len(zs), len(xs)), np.nan, np.float32)
    A, S, PL, TR, BU = [], [], [], [], []
    for (g, o), b in zip(parts, boxes):
        o = np.array(o, float) - ref
        ix = np.where((xs >= b[0] - 1e-6) & (xs <= b[1] + 1e-6))[0]; iz = np.where((zs >= b[2] - 1e-6) & (zs <= b[3] + 1e-6))[0]
        if len(ix) == 0 or len(iz) == 0:
            continue
        sx = np.clip(np.round((xs[ix] - o[0] - g['xs'][0]) / g['res']).astype(int), 0, len(g['xs']) - 1)
        sz = np.clip(np.round((zs[iz] - o[1] - g['zs'][0]) / g['res']).astype(int), 0, len(g['zs']) - 1)
        src = g['Y'][np.ix_(sz, sx)]
        sub = out[np.ix_(iz, ix)]
        out[np.ix_(iz, ix)] = np.fmax(sub, src)
        tr = lambda geom: None if geom is None else affinity.translate(geom, o[0], o[1])
        a, s_, p = g['polys']
        A.append(tr(a)); S.append(tr(s_)); PL.append(tr(p))
        if g.get('veg'):
            TR.append(tr(g['veg'][0])); BU.append(tr(g['veg'][1]))
    un = lambda gs: shapely.union_all([x for x in gs if x is not None]) if any(x is not None for x in gs) else None
    Au, Su, Pu = un(A), un(S), un(PL)
    if Au is not None and Su is not None:
        Su = Su.difference(Au)
    veg = (un(TR), un(BU)) if TR or BU else None
    return dict(xs=xs, zs=zs, Y=out, polys=(Au, Su, Pu), items=[], res=res, veg=veg)


def cmd_compose(args):
    """Render several islands that belong together (e.g. the Lagoon ring) as ONE map in their true relative positions."""
    gd = GameData(args.game_data)
    global GRID_MAX_CELLS
    GRID_MAX_CELLS = args.max_cells
    parts = []
    for n in args.names:
        isl = gd.find(n)
        g = build_grid(gd, isl, scenery=args.scenery)
        if g is None:
            log('skip (no geometry):', n); continue
        M = _trs(_tf(isl['go']))
        parts.append((g, (M[0, 3], M[2, 3])))
        log(f'  {n}: grid {g["Y"].shape} res {g["res"]} origin ({M[0,3]:.0f}, {M[2,3]:.0f})')
    res = max(max(g['res'] for g, _ in parts), args.res)
    grid = compose_grids(parts, res)
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, slug(args.title) + '.jpg')
    info = render_chart(grid, args.title, out, args.size, args.quality, not args.no_key)
    log(f'OK composite of {len(parts)} islands -> {out} grid {grid["Y"].shape} res {res} fit={all(ok for _, ok in info["panel_fit"])}')


def cmd_sheet(args):
    """Contact sheet of every chart in a folder (quick visual review after a re-run)."""
    from PIL import Image, ImageDraw
    files = sorted(f for f in os.listdir(args.folder) if f.lower().endswith(('.jpg', '.png')))
    if not files:
        raise SystemExit('no images in ' + args.folder)
    T, cols = args.tile, args.cols
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * T, rows * (T + 16)), 'white')
    d = ImageDraw.Draw(sheet)
    for k, f in enumerate(files):
        im = Image.open(os.path.join(args.folder, f))
        im.thumbnail((T - 4, T - 4))
        x, y = (k % cols) * T + 2, (k // cols) * (T + 16)
        sheet.paste(im, (x, y + 16))
        d.text((x + 2, y + 2), os.path.splitext(f)[0][:48], fill='black')
    sheet.save(args.out, quality=80) if args.out.lower().endswith('.jpg') else sheet.save(args.out)
    log(f'wrote {args.out}  ({len(files)} charts, {sheet.size[0]}x{sheet.size[1]})')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mods', action='store_true', help='also load island asset bundles from BepInEx/plugins (e.g. Better Ports: island 67 Bottleneck)')
    ap.add_argument('--game-data', default=DEFAULT_GAME_DATA, help='path to Sailwind_Data (default: $SAILWIND_DATA or %(default)s)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    p = sub.add_parser('manifest'); p.add_argument('--check', action='store_true', help='compare with reference/islands_manifest.json')
    p = sub.add_parser('render')
    p.add_argument('names', nargs='*'); p.add_argument('--all', action='store_true')
    p.add_argument('--out', default='charts'); p.add_argument('--size', type=int, default=2000, help='long side in px (layout is 10000 px scaled)')
    p.add_argument('--quality', type=int, default=80, help='JPEG quality'); p.add_argument('--no-key', action='store_true', help='omit the key panel')
    p.add_argument('--title', help='override the title (single island)'); p.add_argument('--display-titles', action='store_true', help='use DISPLAY_TITLES (e.g. "Albacore Island")')
    p.add_argument('--jobs', type=int, default=6); p.add_argument('--dump-grid', help='also save the height grid (.npz) into this folder')
    p.add_argument('--scenery', action='store_true',
                   help='use the scenery scenes\' baked mesh combiners: fuller buildings/fences (red), trees and bushes (green), '
                        'rocks (ground). Output files get a _scenery suffix')
    p.add_argument('--max-cells', type=int, default=GRID_MAX_CELLS,
                   help='max raster cells per side; raster is 0.5 m but coarsened above this (default %(default)d). '
                        'Use ~8000 for 0.5 m on the biggest islands in 10k renders (needs several GB of RAM)')
    p = sub.add_parser('selftest'); p.add_argument('--update', action='store_true', help='rewrite the reference (only after an intended change!)')
    p = sub.add_parser('compose'); p.add_argument('names', nargs='+'); p.add_argument('--title', required=True)
    p.add_argument('--out', default='charts'); p.add_argument('--size', type=int, default=5000); p.add_argument('--quality', type=int, default=90)
    p.add_argument('--res', type=float, default=1.0, help='composite raster resolution in m (default 1.0)')
    p.add_argument('--max-cells', type=int, default=GRID_MAX_CELLS); p.add_argument('--scenery', action='store_true'); p.add_argument('--no-key', action='store_true')
    p = sub.add_parser('sheet'); p.add_argument('folder'); p.add_argument('--out', default='overview.jpg')
    p.add_argument('--tile', type=int, default=300); p.add_argument('--cols', type=int, default=8)
    p = sub.add_parser('explore')
    ep = p.add_subparsers(dest='what', required=True)
    q = ep.add_parser('tree'); q.add_argument('name'); q.add_argument('--depth', type=int, default=2)
    q = ep.add_parser('near'); q.add_argument('name'); q.add_argument('rect', nargs=4, type=float, metavar=('X0', 'X1', 'Z0', 'Z1'), help='island-local metres')
    q.add_argument('--min-foot', type=float, default=8.0); q.add_argument('--limit', type=int, default=40)
    q = ep.add_parser('terrain-check'); q.add_argument('name')
    ep.add_parser('layers')
    args = ap.parse_args()
    global MODS
    MODS = args.mods
    if args.cmd == 'render' and args.dump_grid:
        os.makedirs(args.dump_grid, exist_ok=True)
    {'list': cmd_list, 'manifest': cmd_manifest, 'render': cmd_render, 'selftest': cmd_selftest, 'sheet': cmd_sheet, 'compose': cmd_compose,
     'explore': cmd_explore}[args.cmd](args)


if __name__ == '__main__':
    # Always run as the module 'sailwind_charts' (numba's on-disk cache of the rasteriser is keyed by module name, so running
    # as __main__ in one run and as an import in another corrupts it).
    sys.path.insert(0, HERE)
    import sailwind_charts as _self
    _self.main()
