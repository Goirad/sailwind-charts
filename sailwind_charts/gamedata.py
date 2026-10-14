"""Reading the Unity data of the game (levels, island objects, meshes, terrains) with UnityPy.  Read-only."""
import os
import re

import numpy as np

from .classify import (COLLISION_PROXY_RE, COMBINER_PREFIX, DETAIL_RE, MIN_SOLID_FOOTPRINT_M, SKIP_NAME_WORDS,
                       STRUCT_INCLUDE_RE, TERRAIN_DEPTH_LAYER, scenery_category)
from .util import log

DEFAULT_GAME_DATA = os.environ.get('SAILWIND_DATA', '/tmp/Sailwind/Sailwind_Data')


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

    def __init__(self, data_dir=DEFAULT_GAME_DATA, mods=False):
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
        if mods:
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

