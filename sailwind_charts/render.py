"""
Drawing a chart from a height grid.

    render_chart(grid, title, out_path, style)
        crop_frame      choose the map window: the 10 m shelf (plus structures and a margin), and find "no data" holes
        surface_image   colour the water depth bands and the hillshaded land
        draw_contours   depth / shoreline / land contours
        draw_structures structures, vegetation and dock poles as vector polygons
        panels          title, north arrow, scale bar and key, each placed in the emptiest corner

All layout is designed for a 10,000 px long side ("design px") and scaled by the save DPI, so any size keeps proportions.
"""
import json
import os
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from .style import (BAND_COLORS, C_DEEP, C_FINE, C_LAND, C_MED, CROP_STRUCTURE_MARGIN_M, DEEP_COLOR, LAYOUT_PX, NODATA_COLOR,
                    POLE_DRAW_RADIUS_M, SCENERY_COLORS, SHELF_EDGE_DEPTH_M, STRUCT_EDGE, STRUCT_FILL, SUBMERGED_FILL, W_FINE,
                    W_LAND, W_MED, W_SHORE, Style)

PT = 0.72          # points per px at dpi=100
MIN_NODATA_CELLS = 1600


# --------------------------------------------------------------------------------------------------
# The map window
# --------------------------------------------------------------------------------------------------
@dataclass
class Frame:
    """The cropped grid that is drawn.  Y is height above sea level (water is clamped at -max_depth)."""
    Y: np.ndarray
    nan: np.ndarray        # cells without data
    nodata: np.ndarray     # enclosed holes of `nan` that are drawn grey
    xs: np.ndarray
    zs: np.ndarray
    res: float
    ext: list              # [x0, x1, z0, z1] in metres, cell edges
    pxm: float             # design px per metre
    wpx: int               # canvas size in design px
    hpx: int

    @property
    def width_m(self):
        return self.ext[1] - self.ext[0]

    @property
    def height_m(self):
        return self.ext[3] - self.ext[2]

    @property
    def depth(self):
        return -self.Y


def _shelf_mask(Y, res):
    """Cells that set the crop: water shallower than the shelf edge, without flat depth-cache planes."""
    from scipy import ndimage
    shelf = Y > -SHELF_EDGE_DEPTH_M
    # A dominant flat plane just under 10 m (Gold Rock: 9.7 m) is a depth-cache skirt, not shelf: it must not set the crop.
    d = np.round(-Y[shelf & (Y < 0)], 1)
    if d.size:
        values, counts = np.unique(d, return_counts=True)
        if counts.max() > 0.3 * d.size:
            shelf &= np.abs(-Y - values[counts.argmax()]) > 0.06
            k = max(1, int(5.0 / res))             # ...and neither may the thin transition stripes around that plane
            shelf = ndimage.binary_opening(shelf, structure=np.ones((2 * k + 1, 2 * k + 1)))
    return shelf


def _extend_into_open_sea(Y, nan, xs, zs, res, lack, floor):
    """
    Add the margin the grid cannot supply (cells per side in `lack`) as open sea at the floor depth, but only on a side where the
    data already ends in floor-depth water: there is no seam.  On a side where the data ends in something else (Gold Rock's flat
    9.7 m plane) the chart stops at the data instead.
    """
    cells = {}
    strips = dict(W=Y[:, 0], E=Y[:, -1], S=Y[0, :], N=Y[-1, :])
    for side, n in lack.items():
        if n > 0 and np.mean(strips[side] <= floor + 0.5) >= 0.95:
            cells[side] = n
    if not cells:
        return Y, nan, xs, zs
    w, e, s, n = (cells.get(k, 0) for k in 'WESN')
    Y = np.pad(Y, ((s, n), (w, e)), constant_values=floor)
    nan = np.pad(nan, ((s, n), (w, e)), constant_values=True)
    xs = np.r_[xs[0] - res * np.arange(w, 0, -1), xs, xs[-1] + res * np.arange(1, e + 1)]
    zs = np.r_[zs[0] - res * np.arange(s, 0, -1), zs, zs[-1] + res * np.arange(1, n + 1)]
    return Y, nan, xs, zs


def crop_frame(grid, style):
    """Crop the grid to the shelf (grown to contain all structures, plus the margin) and describe the window."""
    from scipy import ndimage
    xs, zs = grid['xs'], grid['zs']
    A, S, PL = grid['polys']
    res = float(xs[1] - xs[0])
    nan = np.isnan(grid['Y'])
    Y = np.where(nan, -200, grid['Y'].astype(np.float32))
    Y = np.maximum(Y, -style.max_depth_m)       # nothing is drawn below the terrain floor: mesh skirts there are not what is seen

    jj, ii = np.where(_shelf_mask(Y, res))
    if jj.size == 0:           # nothing above the shelf threshold (e.g. only structures): use the whole grid
        jj = np.array([0, Y.shape[0] - 1]); ii = np.array([0, Y.shape[1] - 1])
    j0, j1, i0, i1 = jj.min(), jj.max(), ii.min(), ii.max()
    bounds = [g.bounds for g in (A, S, PL) if g is not None and not g.is_empty]
    if bounds:
        m = CROP_STRUCTURE_MARGIN_M
        bx0 = min(b[0] for b in bounds) - m; by0 = min(b[1] for b in bounds) - m
        bx1 = max(b[2] for b in bounds) + m; by1 = max(b[3] for b in bounds) + m
        i0 = min(i0, max(int((bx0 - xs[0]) / res), 0)); i1 = max(i1, min(int((bx1 - xs[0]) / res) + 1, len(xs) - 1))
        j0 = min(j0, max(int((by0 - zs[0]) / res), 0)); j1 = max(j1, min(int((by1 - zs[0]) / res) + 1, len(zs) - 1))
    pad = 0
    if style.margin_frac or style.margin_min_m:       # a margin of sea around the crop
        pad = int(round(max(style.margin_min_m, style.margin_frac * max(xs[i1] - xs[i0], zs[j1] - zs[j0])) / res))
    lack = dict(W=pad - i0, S=pad - j0, E=pad - (len(xs) - 1 - i1), N=pad - (len(zs) - 1 - j1))    # margin the grid cannot supply
    i0, i1, j0, j1 = max(i0 - pad, 0), min(i1 + pad, len(xs) - 1), max(j0 - pad, 0), min(j1 + pad, len(zs) - 1)
    Y = Y[j0:j1 + 1, i0:i1 + 1]; nan = nan[j0:j1 + 1, i0:i1 + 1]; xs = xs[i0:i1 + 1]; zs = zs[j0:j1 + 1]
    Y, nan, xs, zs = _extend_into_open_sea(Y, nan, xs, zs, res, lack, -style.max_depth_m)
    ext = [xs[0] - res / 2, xs[-1] + res / 2, zs[0] - res / 2, zs[-1] + res / 2]
    pxm = LAYOUT_PX / max(ext[1] - ext[0], ext[3] - ext[2])

    # enclosed holes in the mesh data are "no data", not deep water (unless the island says they are open water)
    lab, n = ndimage.label(nan)
    sizes = np.bincount(lab.ravel())
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    nodata = np.isin(lab, [k for k in range(1, n + 1) if k not in border and sizes[k] >= MIN_NODATA_CELLS])
    if style.nodata_is_water:
        nodata[:] = False
    return Frame(Y, nan, nodata, xs, zs, res, ext, pxm,
                 int(round((ext[1] - ext[0]) * pxm)), int(round((ext[3] - ext[2]) * pxm)))


# --------------------------------------------------------------------------------------------------
# Surface and contours
# --------------------------------------------------------------------------------------------------
def surface_image(fr, style):
    """RGB image of the frame: depth bands (a gradient from 5 m down to the deepest drawn depth) and hillshaded land."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource, to_rgb
    depth, land, Y = fr.depth, fr.Y >= 0, fr.Y
    cols = [to_rgb(BAND_COLORS[k]) for k in ('land', '0-2', '2-5', '5-10', '10+')]
    band = np.digitize(depth, [0, 2, 5, 9.95])
    img = np.zeros(Y.shape + (3,), np.float32)
    for i, c in enumerate(cols):
        img[band == i] = c
    m510 = (depth >= 5) & (depth < 10)
    t = np.clip((depth - 5) / 5, 0, 1)[m510][:, None]
    img[m510] = np.array(cols[3]) * (1 - t) + np.array(cols[4]) * t
    land_tint = plt.cm.YlOrBr(np.clip(Y / max(Y.max(), 1), 0, 1) * 0.55 + 0.05)[..., :3]
    # below the shelf edge the ramp keeps darkening to DEEP_COLOR; cells without data take the colour of the usual 20 m floor
    deep_depth = np.where(fr.nan, min(style.max_depth_m, 20.0), depth)
    deep = deep_depth >= 9.95
    t = np.clip((deep_depth - 10) / (style.max_depth_m - 10), 0, 1)[deep][:, None]
    img[deep] = np.array(cols[4]) * (1 - t) + np.array(to_rgb(DEEP_COLOR)) * t
    img[land] = 0.55 * np.array(to_rgb(BAND_COLORS['land'])) + 0.45 * land_tint[land]
    img[fr.nodata] = to_rgb(NODATA_COLOR)
    shade = LightSource(315, 40).hillshade(np.where(land, Y, 0).astype(np.float64), vert_exag=2, dx=fr.res, dy=fr.res)
    return np.clip(img * (0.75 + 0.35 * shade[..., None]), 0, 1)


def _flat_floor(depth, nan):
    """The depth of a flat floor below 10 m (Terrain islands: exactly 20 m), or None.  Such a floor gets no contour."""
    d = np.round(depth[(depth > 10) & ~nan], 1)
    if d.size == 0:
        return None
    values, counts = np.unique(d, return_counts=True)
    return values[counts.argmax()] if counts.max() > 0.2 * d.size else None


def draw_contours(ax, fr, style, lwpt):
    Ym = np.ma.masked_where(fr.nan, fr.Y)
    Dm = np.ma.masked_where(fr.nan, fr.depth)

    def contour(z, levels, color, px, alpha=1):
        ax.contour(fr.xs, fr.zs, z, levels=levels, colors=color, linewidths=px * lwpt, alpha=alpha, zorder=2)
    contour(Dm, [1, 2, 3, 4, 6, 7, 8, 9], C_FINE, W_FINE, 0.9)
    contour(Dm, [5], C_MED, W_MED)
    if Dm.max() > 10:        # deeper water: a light contour every 5 m, 10 m bold
        flat = _flat_floor(fr.depth, fr.nan)
        levels = [m for m in range(15, int(style.max_depth_m) + 1, 5) if flat is None or abs(m - flat) > 0.25]
        contour(Dm, levels, C_DEEP, W_FINE, 0.55)
        contour(Dm, [10], C_DEEP, W_MED, 0.8)
    if fr.Y.max() > 10:
        contour(Ym, np.arange(10, fr.Y.max(), 10), C_LAND, W_LAND)
    contour(Ym, [0], 'k', W_SHORE)


# --------------------------------------------------------------------------------------------------
# Structures, vegetation, poles
# --------------------------------------------------------------------------------------------------
def _patches(geom, **kw):
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    out = []
    if geom is None or geom.is_empty:
        return out
    for g in getattr(geom, 'geoms', [geom]):
        if g.geom_type != 'Polygon' or g.is_empty:
            continue
        verts, codes = [], []
        for ring in [g.exterior] + list(g.interiors):
            c = np.asarray(ring.coords)
            verts += list(c)
            codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
        out.append(PathPatch(Path(verts, codes), **kw))
    return out


def draw_structures(ax, grid, fr, lwpt):
    A, S, PL = grid['polys']
    size = max(fr.width_m, fr.height_m)
    pole_r = POLE_DRAW_RADIUS_M if size >= 250 else max(0.25, POLE_DRAW_RADIUS_M * size / 250)   # keep poles small on tiny islands
    veg = grid.get('veg')                                   # (trees, bushes) polygons
    if veg:
        for geom, kind in ((veg[1], 'bushes'), (veg[0], 'trees')):            # bushes under trees, both under structures
            for p in _patches(geom, facecolor=SCENERY_COLORS[kind], edgecolor='none', alpha=0.7, zorder=5):
                ax.add_patch(p)
    for p in _patches(A, facecolor=STRUCT_FILL, edgecolor=STRUCT_EDGE, lw=3 * lwpt, joinstyle='round', zorder=6):
        ax.add_patch(p)
    if S is not None and not S.is_empty:
        for p in _patches(S, facecolor=SUBMERGED_FILL, edgecolor=STRUCT_FILL, hatch='////', lw=3 * lwpt, zorder=6):
            ax.add_patch(p)
    if PL is not None:
        for p in _patches(PL.buffer(pole_r), facecolor='#ffffff', edgecolor=STRUCT_EDGE, lw=4 * lwpt, zorder=8):
            ax.add_patch(p)
        for p in _patches(PL, facecolor=STRUCT_FILL, edgecolor='none', zorder=9):
            ax.add_patch(p)


# --------------------------------------------------------------------------------------------------
# Panels: title, north arrow, scale bar, key
# --------------------------------------------------------------------------------------------------
MARGIN, PAD = 100, 70              # design px: panel distance from the edge, and padding inside a panel


class Canvas:
    """The matplotlib axes in design px: places boxes and text, measures text.  (x, y) are design px from the bottom-left."""

    def __init__(self, fig, ax, fr, lwpt):
        self.fig, self.ax, self.fr, self.lwpt = fig, ax, fr, lwpt
        self.renderer = fig.canvas.get_renderer()
        self.frame_style = dict(facecolor='white', edgecolor='k', lw=4 * lwpt, alpha=0.92)

    def X(self, px):
        return self.fr.ext[0] + px / self.fr.pxm

    def Z(self, px):
        return self.fr.ext[2] + px / self.fr.pxm

    def box(self, x, y, w, h, **kw):
        from matplotlib.patches import Rectangle
        self.ax.add_patch(Rectangle((self.X(x), self.Z(y)), w / self.fr.pxm, h / self.fr.pxm, zorder=10, **kw))

    def text(self, x, y, s, size, **kw):
        return self.ax.text(self.X(x), self.Z(y), s, fontsize=size * PT, zorder=12, **kw)

    def measure(self, s, size, **kw):
        t = self.ax.text(self.X(0), self.Z(0), s, fontsize=size * PT, **kw)
        bb = t.get_window_extent(self.renderer)
        t.remove()
        return bb.width, bb.height

    def extent(self, text_artist):
        bb = text_artist.get_window_extent(self.renderer)
        return bb.x0, bb.y0, bb.x1, bb.y1


def _fits(rects, panel):
    x0, y0, x1, y1 = panel
    return all(r[0] >= x0 - 1 and r[1] >= y0 - 1 and r[2] <= x1 + 1 and r[3] <= y1 + 1 for r in rects)


class TitlePanel:
    name, corner = 'title', 'TL'

    def __init__(self, cv, title):
        self.title = title
        tw, th = cv.measure(title, 190, fontweight='bold')
        self.w, self.h = tw + 2 * PAD, th + 2 * PAD * 0.8

    def draw(self, cv, x, y, corner):
        cv.box(x, y, self.w, self.h, **cv.frame_style)
        t = cv.text(x + PAD, y + self.h / 2, self.title, 190, fontweight='bold', va='center', ha='left')
        return _fits([cv.extent(t)], (x, y, x + self.w, y + self.h))


class NorthArrow:
    name, corner = 'north', 'TR'
    w, h = 240, 520

    def draw(self, cv, x, y, corner):
        cx = x + (self.w - 120 if corner[1] == 'R' else 120)
        cv.ax.annotate('N', xy=(cv.X(cx), cv.Z(y + self.h)), xytext=(cv.X(cx), cv.Z(y + self.h - 420)), ha='center', va='top',
                       fontsize=150 * PT, fontweight='bold', zorder=12,
                       arrowprops=dict(arrowstyle='-|>', color='k', lw=12 * cv.lwpt, mutation_scale=70))
        return True


class ScaleBar:
    name, corner = 'scale', 'BL'
    LABEL = 95

    def __init__(self, cv):
        width_m = cv.fr.width_m
        self.metres = max(v for v in (10, 20, 25, 50, 100, 200, 250, 500, 1000) if v <= 0.3 * width_m) if width_m >= 34 else 10
        self.bar_w = self.metres * cv.fr.pxm
        w0, _ = cv.measure('0', self.LABEL, fontweight='bold')
        w5, h5 = cv.measure('%d m' % self.metres, self.LABEL, fontweight='bold')
        self.bar_off = max(PAD, w0 / 2 + PAD * 0.5)
        self.w = self.bar_off + self.bar_w + max(PAD, w5 / 2 + PAD * 0.5)
        self.h = PAD * 0.6 + 50 + 25 + h5 + PAD * 0.6

    def draw(self, cv, x, y, corner):
        bar_x = x + self.bar_off
        cv.box(x, y, self.w, self.h, **cv.frame_style)
        for k in range(5):
            cv.box(bar_x + k * self.bar_w / 5, y + PAD * 0.6, self.bar_w / 5, 50, facecolor='k' if k % 2 == 0 else 'white',
                   edgecolor='k', lw=3 * cv.lwpt)
        ly = y + PAD * 0.6 + 50 + 25
        t0 = cv.text(bar_x, ly, '0', self.LABEL, ha='center', va='bottom', fontweight='bold')
        t1 = cv.text(bar_x + self.bar_w, ly, '%d m' % self.metres, self.LABEL, ha='center', va='bottom', fontweight='bold')
        return _fits([cv.extent(t0), cv.extent(t1)], (x, y, x + self.w, y + self.h))


@dataclass
class KeyRow:
    kind: str            # title | band | note | line | nodata | sub | pole
    text: str
    height: float        # design px (already scaled)
    size: float = 0      # font size for title / note rows
    swatch: str = ''     # band key, or line colour
    lw: float = 0        # line width for line rows


KEY_TITLE_DROP = 60        # extra space above the key's title row (in key units), so its padding matches the left and bottom ones


class KeyPanel:
    """The key.  Its size is chosen so it ends up about `style.key_px` final pixels wide."""
    name, corner = 'key', 'BR'

    def __init__(self, cv, fr, style, grid, final_scale):
        S = grid['polys'][1]
        sel = fr.depth[(fr.depth > 0) & (fr.depth < SHELF_EDGE_DEPTH_M) & ~fr.nan]
        values, counts = np.unique(np.round(sel, 1), return_counts=True)
        self.flat_depth = values[counts.argmax()] if (sel.size and counts.max() > 0.3 * len(sel)) else None
        self.max_depth = style.max_depth_m
        self.has_nodata = bool(fr.nodata.any())
        self.has_sub = S is not None and not S.is_empty
        self.cv = cv
        self.scale = 1.0
        if style.key_px:           # shrink the key so it ends up about key_px final pixels wide
            self.scale = min(1.0, style.key_px / (final_scale * self._layout(1.0)[1]))
        self.rows, self.w, self.h = self._layout(self.scale)

    def _rows(self, ks):
        h = lambda v: v * ks
        rows = [KeyRow('title', 'DEPTH BELOW SEA LEVEL', h(170), size=100 * ks),
                KeyRow('band', '0 – 2 m', h(150), swatch='0-2'), KeyRow('band', '2 – 5 m', h(150), swatch='2-5'),
                KeyRow('band', '5 – 10 m', h(150), swatch='5-10'), KeyRow('band', '10 – %d m' % self.max_depth, h(150), swatch='10+'),
                KeyRow('note', 'Map edge: where the shelf drops below 10 m', h(110 if self.flat_depth else 190), size=80 * ks)]
        if self.flat_depth:
            rows.append(KeyRow('note', 'Open seabed on the shelf is a flat %.1f m' % self.flat_depth, h(190), size=80 * ks))
        rows += [KeyRow('line', 'Depth contour, every 1 m (thin)', h(150), swatch=C_FINE, lw=W_FINE),
                 KeyRow('line', 'Depth contour, every 5 m (bold)', h(150), swatch=C_MED, lw=W_MED),
                 KeyRow('line', 'Depth contour below 10 m, every 5 m', h(150), swatch=C_DEEP, lw=W_FINE),
                 KeyRow('line', 'Shoreline (0 m)', h(150), swatch='k', lw=W_SHORE),
                 KeyRow('line', 'Land contour, every 10 m', h(190), swatch=C_LAND, lw=W_LAND)]
        if self.has_nodata:
            rows.append(KeyRow('nodata', 'No data (not modelled)', h(150)))
        if self.has_sub:
            rows.append(KeyRow('sub', 'Submerged structure', h(150)))
        rows.append(KeyRow('pole', 'Dock pole (drawn oversize) - hazard', 0))
        return rows

    def _layout(self, ks):
        """(rows, width, height) of the panel for a size factor ks (1 = designed size)."""
        cv = self.cv
        rows = self._rows(ks)
        KS, KP, SW = 88 * ks, 80 * ks, 260 * ks
        TX = KP + SW + 70 * ks
        widest = 0
        for r in rows:
            if r.kind == 'title': widest = max(widest, KP + cv.measure(r.text, r.size, fontweight='bold')[0])
            elif r.kind == 'note': widest = max(widest, KP + cv.measure(r.text, r.size, style='italic')[0])
            else: widest = max(widest, TX + cv.measure(r.text, KS)[0])
        return rows, widest + KP, KP + KEY_TITLE_DROP * ks + sum(r.height for r in rows[:-1]) + 110 * ks + KP * 0.4

    def draw(self, cv, x, y, corner):
        ks = self.scale
        KS, KP, SW = 88 * ks, 80 * ks, 260 * ks
        TX = KP + SW + 70 * ks
        lwk = max(ks, 0.5)             # line-width factor for swatches
        from matplotlib.colors import to_rgb
        from matplotlib.patches import Circle
        cols = [to_rgb(BAND_COLORS[k]) for k in ('5-10', '10+')]
        cv.box(x, y, self.w, self.h, **cv.frame_style)
        yy = y + self.h - KP * 0.9 - KEY_TITLE_DROP * ks      # the title sits as far below the border as the text is from the left one
        rects = []
        say = lambda *a, **kw: rects.append(cv.extent(cv.text(*a, **kw)))
        for r in self.rows:
            if r.kind == 'title':
                say(x + KP, yy, r.text, r.size, fontweight='bold', va='center')
            elif r.kind == 'band':
                if r.swatch in ('5-10', '10+'):
                    c_a, c_b = (cols[0], cols[1]) if r.swatch == '5-10' else (cols[1], to_rgb(DEEP_COLOR))
                    n = 40
                    for q in range(n):
                        t = q / (n - 1)
                        cv.box(x + KP + q * SW / n, yy - 55 * ks, SW / n + 1, 110 * ks,
                               facecolor=tuple(np.array(c_a) * (1 - t) + np.array(c_b) * t), edgecolor='none')
                    cv.box(x + KP, yy - 55 * ks, SW, 110 * ks, facecolor='none', edgecolor='k', lw=2 * cv.lwpt * lwk)
                else:
                    cv.box(x + KP, yy - 55 * ks, SW, 110 * ks, facecolor=BAND_COLORS[r.swatch], edgecolor='k', lw=2 * cv.lwpt * lwk)
                say(x + TX, yy, r.text, KS, va='center')
            elif r.kind == 'note':
                say(x + KP, yy, r.text, r.size, va='center', style='italic')
            elif r.kind == 'line':
                if r.swatch == C_DEEP:      # light contour: needs a deep-water backdrop to be visible on the white key
                    cv.box(x + KP, yy - 30 * ks, SW, 60 * ks, facecolor=DEEP_COLOR, edgecolor='none')
                cv.ax.plot([cv.X(x + KP), cv.X(x + KP + SW)], [cv.Z(yy), cv.Z(yy)], color=r.swatch, lw=r.lw * cv.lwpt * lwk,
                           zorder=12, solid_capstyle='butt')
                say(x + TX, yy, r.text, KS, va='center')
            elif r.kind == 'nodata':
                cv.box(x + KP, yy - 55 * ks, SW, 110 * ks, facecolor=NODATA_COLOR, edgecolor='k', lw=2 * cv.lwpt * lwk)
                say(x + TX, yy, r.text, KS, va='center')
            elif r.kind == 'sub':
                cv.box(x + KP, yy - 55 * ks, SW, 110 * ks, facecolor=SUBMERGED_FILL, edgecolor=STRUCT_FILL, lw=3 * cv.lwpt * lwk,
                       hatch='////')
                say(x + TX, yy, r.text, KS, va='center')
            elif r.kind == 'pole':
                cx = x + KP + SW / 2
                cv.ax.add_patch(Circle((cv.X(cx), cv.Z(yy)), 55 * ks / cv.fr.pxm, facecolor='white', edgecolor=STRUCT_EDGE,
                                       lw=4 * cv.lwpt * lwk, zorder=12))
                cv.ax.add_patch(Circle((cv.X(cx), cv.Z(yy)), 28 * ks / cv.fr.pxm, facecolor=STRUCT_FILL, edgecolor='none', zorder=13))
                say(x + TX, yy, r.text, KS, va='center')
            yy -= r.height
        return _fits(rects, (x, y, x + self.w, y + self.h))


def _place_panels(cv, panels):
    """Give every panel its preferred corner unless that corner covers land / no-data; otherwise the emptiest free corner."""
    fr = cv.fr
    busy = (fr.Y >= -0.5) | fr.nodata

    def overlap(x0, y0, w, h):
        a0 = max(int(x0 / fr.pxm / fr.res), 0); a1 = min(int((x0 + w) / fr.pxm / fr.res) + 1, busy.shape[1])
        b0 = max(int(y0 / fr.pxm / fr.res), 0); b1 = min(int((y0 + h) / fr.pxm / fr.res) + 1, busy.shape[0])
        cells = busy[b0:b1, a0:a1]
        return float(cells.mean()) if cells.size else 0.0

    def rect_at(corner, w, h):
        return ((MARGIN if corner[1] == 'L' else fr.wpx - MARGIN - w), (MARGIN if corner[0] == 'B' else fr.hpx - MARGIN - h))

    used, where = set(), {}
    for p in panels:
        if p.corner not in used and overlap(*rect_at(p.corner, p.w, p.h), p.w, p.h) <= 0.02:
            c = p.corner
        else:
            c = min([k for k in ('TL', 'TR', 'BL', 'BR') if k not in used], key=lambda k: overlap(*rect_at(k, p.w, p.h), p.w, p.h))
        used.add(c)
        where[p.name] = (c, rect_at(c, p.w, p.h))
    return where


# --------------------------------------------------------------------------------------------------
# The whole chart
# --------------------------------------------------------------------------------------------------
KEY_PNG_PAD = 20       # design px around a separate key so the frame line is not clipped


def _write_separate_key(cv, key, xy, base, title, scale):
    """Write the key as a transparent PNG (<base>_key.png) and where it belongs in the chart (<base>.json, as fractions of the image)."""
    import matplotlib.pyplot as plt
    w, h = key.w + 2 * KEY_PNG_PAD, key.h + 2 * KEY_PNG_PAD
    pxm = cv.fr.pxm
    fr = SimpleNamespace(ext=[0, w / pxm, 0, h / pxm], pxm=pxm)
    fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.set_xlim(fr.ext[0], fr.ext[1]); ax.set_ylim(fr.ext[2], fr.ext[3]); ax.set_aspect('auto')
    kcv = Canvas(fig, ax, fr, cv.lwpt)
    ok = key.draw(kcv, KEY_PNG_PAD, KEY_PNG_PAD, 'BR')
    fig.savefig(base + '_key.png', dpi=100 * scale, transparent=True)
    plt.close(fig)
    wpx, hpx = cv.fr.wpx, cv.fr.hpx
    x, y = xy
    with open(base + '.json', 'w') as f:
        json.dump(dict(title=title, image_px=[round(wpx * scale), round(hpx * scale)],
                       key=dict(left=(x - KEY_PNG_PAD) / wpx, top=(hpx - (y + key.h + KEY_PNG_PAD)) / hpx, width=w / wpx,
                                px=[round(w * scale), round(h * scale)])), f, indent=1)
    return ok


def render_chart(grid, title, out_path, style=Style()):
    """Render a height grid to an image.  Returns {panel_fit, canvas_design_px, crop_cells} for checking."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    scale = style.size_px / LAYOUT_PX
    lwpt = PT * max(1.0, 1.0 / (scale * 2.0))          # line widths get a boost at small sizes so they stay visible
    fr = crop_frame(grid, style)

    fig = plt.figure(figsize=(fr.wpx / 100, fr.hpx / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(surface_image(fr, style), origin='lower', extent=fr.ext, interpolation='antialiased', zorder=0)
    draw_contours(ax, fr, style, lwpt)
    draw_structures(ax, grid, fr, lwpt)
    ax.set_xlim(fr.ext[0], fr.ext[1]); ax.set_ylim(fr.ext[2], fr.ext[3]); ax.set_aspect('auto')

    cv = Canvas(fig, ax, fr, lwpt)
    title_panel, north, scale_bar = TitlePanel(cv, title), NorthArrow(), ScaleBar(cv)
    panels = [north] + ([KeyPanel(cv, fr, style, grid, scale)] if style.key != 'none' else []) + [scale_bar, title_panel]
    placement = _place_panels(cv, panels)
    # draw in a fixed order (title, north, scale, key): later panels sit on top; a separate key is only reserved here
    fit = []
    for p in sorted(panels, key=lambda p: ('title', 'north', 'scale', 'key').index(p.name)):
        corner, (x, y) = placement[p.name]
        if p.name == 'key' and style.key == 'separate':
            continue
        fit.append((p.name, p.draw(cv, x, y, corner)))

    kwargs = dict(quality=style.quality, subsampling=0, optimize=False) if out_path.lower().endswith(('.jpg', '.jpeg')) else {}
    fig.savefig(out_path, dpi=100 * scale, pil_kwargs=kwargs)
    plt.close(fig)
    if style.key == 'separate':
        key = next(p for p in panels if p.name == 'key')
        fit.append(('key', _write_separate_key(cv, key, placement['key'][1], os.path.splitext(out_path)[0], title, scale)))
    return dict(panel_fit=fit, canvas_design_px=(fr.wpx, fr.hpx), crop_cells=fr.Y.shape)
