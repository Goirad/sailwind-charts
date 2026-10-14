"""Exploration helpers: work out how a new or odd island is built (hierarchy, overlapping meshes, Terrain placement rule)."""
import re

import numpy as np

from .classify import STRUCT_CLASS_RE, is_structure
from .gamedata import _comp, _tf, _trs, terrain_placement
from .grid import raster
from .util import log


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


def cmd_explore(args, gd, catalog):
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
        _terrain_check(gd, isl, catalog.island(isl['name']).quirks)


def _terrain_check(gd, isl, quirks):
    """Compare two Terrain placement rules against the island's mesh land: (H1) apply parent rotation/scale (WRONG) vs
    (H2) position only (what Unity does).  A correct rule makes terrain land overlap mesh land (IoU well above 0)."""
    from scipy.ndimage import map_coordinates
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

