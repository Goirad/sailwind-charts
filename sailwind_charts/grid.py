"""Island geometry -> height grid (top-down maximum, island-local metres) and vector footprints; composites of several grids."""
import math

import numpy as np

from .classify import POLE_RE, is_structure
from .gamedata import terrain_placement
from .util import log

FAR_ITEM_LIMIT_M = 2500.0       # drop items further than this from the island origin (stray scenery)
GRID_RES_M = 0.5                # base raster resolution
GRID_MAX_CELLS = 5000           # ...coarsened so the longest side never exceeds this many cells
MIN_GRID_RES_M = 0.1
ADAPTIVE_CELLS = 2500                   # aim for at least this many raster cells across the island when it is small


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



def build_grid(gd, isl, quirks=None, scenery=False, max_cells=GRID_MAX_CELLS, adaptive_res=False):
    """
    Build the island's height grid (top-down maximum of all land geometry, island-local metres, y = height above
    sea level) plus the vector footprints of structures.  Returns a dict, or None if the island has no geometry.
    """
    import shapely
    from scipy.ndimage import map_coordinates
    quirks = quirks or {}
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
    base = GRID_RES_M
    if adaptive_res:        # small islands get a finer raster, otherwise 0.5 m cells show as big steps at chart scale
        base = min(GRID_RES_M, max(MIN_GRID_RES_M, max(x1 - x0, z1 - z0) / ADAPTIVE_CELLS))
    res = max(base, max(x1 - x0, z1 - z0) / max_cells)
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

