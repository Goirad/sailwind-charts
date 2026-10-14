"""Look of the charts: the options a chart is rendered with, and the palette."""
from dataclasses import dataclass

LAYOUT_PX = 10000               # all layout is designed for a 10,000 px long side, then scaled by DPI
SHELF_EDGE_DEPTH_M = 10.05      # the chart border is the bounding box of the shelf: everything shallower than this
CROP_STRUCTURE_MARGIN_M = 8.0   # the border also grows to contain every structure, plus this margin
POLE_DRAW_RADIUS_M = 0.9        # dock poles are drawn oversize so they read on the chart

BAND_COLORS = {'land': '#e3d7a3', '0-2': '#dcebf6', '2-5': '#a9cce6', '5-10': '#6fa8d3', '10+': '#4f8fc4'}
# 0-2 m and 2-5 m are flat bands; 5-10 m is a gradient from '5-10' (at 5 m) to '10+' (at 10 m),
# and below 10 m the ramp keeps darkening from '10+' to DEEP_COLOR at the deepest drawn depth.
DEEP_COLOR = '#2a5c93'
NODATA_COLOR = '#d4d4d4'
SCENERY_COLORS = {'trees': '#1b5e20', 'bushes': '#9ccc65'}
STRUCT_FILL, STRUCT_EDGE = '#c62828', '#4a0a0a'
SUBMERGED_FILL = '#f4b4b0'

# contour colours and widths (design px at 10,000 px)
C_FINE, C_MED, C_LAND, C_DEEP = '#17406b', '#0a2748', '#6b4a1f', '#cfe3f3'
W_FINE, W_MED, W_LAND, W_SHORE = 2.5, 5, 3, 6


@dataclass(frozen=True)
class Style:
    """How a chart is rendered.  Sizes are in final pixels unless stated otherwise."""
    size_px: int = 0                # fixed long side of the image in px; 0 = from the map's extent (px_per_m, within min_px..max_px)
    px_per_m: float = 4.0           # pixels per metre of map: about the data's own cell size (0.1-0.5 m), more would only interpolate
    min_px: int = 2000              # ...but small islands never get a smaller image than this
    max_px: int = 5000              # ...and big ones never a larger one
    quality: int = 85               # JPEG quality
    key: str = 'baked'              # 'baked' (in the image) | 'separate' (reserved, written as <name>_key.png + <name>.json) | 'none'
    margin_frac: float = 0.05       # sea buffer around the shelf crop: this fraction of its long side...
    margin_min_m: float = 15.0      # ...but at least this many metres (0 / 0 = none); only real grid data is used
    key_px: int | None = None       # key panel width in final px; None = 10% of the long side, between 350 and 500; 0 = full designed size
    max_depth_m: float = 20.0       # water is drawn down to this depth (the game's flat terrain floor); must exceed 10
    nodata_is_water: bool = False   # enclosed cells without data are open water, not grey "no data" (per-island quirk)
