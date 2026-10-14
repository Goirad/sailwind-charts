import numpy as np
import pytest

from sailwind_charts.grid import compose_grids, raster
from sailwind_charts.render import crop_frame, render_chart
from sailwind_charts.style import Style


def island_grid(n=400, res=1.0, plane_depth=None):
    """A synthetic cone island in a 20 m deep sea; optionally a flat plane at plane_depth outside a 10 m shelf halo."""
    xs = (np.arange(n) - n / 2) * res
    zs = xs.copy()
    X, Z = np.meshgrid(xs, zs)
    r = np.hypot(X, Z)
    Y = np.where(r < 40, 20 - r / 2, -np.minimum(0.25 * (r - 40), 20.0))
    if plane_depth:
        Y = np.where(r > 90, -plane_depth, Y)
    return dict(xs=xs, zs=zs, Y=Y.astype(np.float32), polys=(None, None, None), res=res, items=[], veg=None)


def test_raster_writes_the_highest_surface():
    out = np.full((10, 10), -np.inf)
    V = np.array([[0, 5.0, 0], [9, 5.0, 0], [0, 5.0, 9]])
    raster(V, np.array([[0, 1, 2]]), 0.0, 0.0, 1.0, out)
    assert out[1, 1] == pytest.approx(5.0) and np.isneginf(out[9, 9])


def test_margin_enlarges_the_crop():
    g = island_grid()
    tight = crop_frame(g, Style(margin_frac=0, margin_min_m=0))
    padded = crop_frame(g, Style(margin_frac=0.1, margin_min_m=0))
    assert padded.Y.shape[0] > tight.Y.shape[0] and padded.Y.shape[1] > tight.Y.shape[1]


def test_flat_plane_under_10m_does_not_set_the_crop():
    plain = crop_frame(island_grid(), Style(margin_frac=0, margin_min_m=0))
    planed = crop_frame(island_grid(plane_depth=9.7), Style(margin_frac=0, margin_min_m=0))
    assert planed.Y.shape[0] <= plain.Y.shape[0] + 12     # the big 9.7 m plane would otherwise fill the whole grid


def test_water_is_clamped_to_the_max_depth():
    fr = crop_frame(island_grid(), Style(max_depth_m=15))
    assert fr.Y.min() >= -15


def test_render_writes_an_image_and_panels_fit(tmp_path):
    out = tmp_path / 'cone.jpg'
    info = render_chart(island_grid(), 'Cone Island', str(out), Style(size_px=800))
    assert out.stat().st_size > 10_000
    assert all(ok for _, ok in info['panel_fit'])


def test_compose_takes_the_maximum_height():
    a, b = island_grid(100), island_grid(100)
    b['Y'] = b['Y'] + 5
    grid = compose_grids([(a, (0.0, 0.0)), (b, (0.0, 0.0))], 1.0)
    assert float(np.nanmax(grid['Y'])) == pytest.approx(float(b['Y'].max()))


def test_separate_key_is_written_with_its_placement(tmp_path):
    import json
    out = tmp_path / 'cone.jpg'
    info = render_chart(island_grid(), 'Cone Island', str(out), Style(size_px=800, key='separate'))
    meta = json.loads((tmp_path / 'cone.json').read_text())
    assert (tmp_path / 'cone_key.png').stat().st_size > 1000
    k = meta['key']
    assert 0 <= k['left'] < 1 and 0 <= k['top'] < 1 and k['left'] + k['width'] <= 1.01
    assert dict(info['panel_fit'])['key']


def near_edge_grid(shallow_edge=False):
    """A small island close to the east edge of a 100 m grid; the water is at the 20 m floor by the edge."""
    xs = np.arange(100.0) - 50
    X, Z = np.meshgrid(xs, xs)
    r = np.hypot(X - 25, Z)
    Y = np.where(r < 10, 10 - r, -np.minimum(2.0 * (r - 10), 20.0)).astype(np.float32)
    if shallow_edge:
        Y[:, -3:] = -9.0
    return dict(xs=xs, zs=xs.copy(), Y=Y, polys=(None, None, None), res=1.0, items=[], veg=None)


def test_margin_extends_into_open_sea_past_the_data():
    g = near_edge_grid()
    fr = crop_frame(g, Style(margin_frac=0.5, margin_min_m=0))
    assert fr.xs[-1] > g['xs'][-1]                  # the grid ends too soon: the margin continues as open sea
    assert fr.nan[:, -1].all()                      # ...drawn as "no data" at the floor colour, so there is no seam


def test_no_margin_is_invented_next_to_a_shallow_data_edge():
    fr = crop_frame(near_edge_grid(shallow_edge=True), Style(margin_frac=0.5, margin_min_m=0))
    assert fr.xs[-1] == pytest.approx(49.0)         # the data ends in shallow water: the chart stops there
