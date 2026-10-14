"""Which game objects count as land, structures, trees...  Everything here is decided by object / material NAME (see DATA_LAYOUT.md)."""
import re

import numpy as np

TERRAIN_DEPTH_LAYER = 14        # Unity layer "TerrainDepth": geometry the ocean's depth cache draws
MIN_SOLID_FOOTPRINT_M = 20.0    # a solid (collider) scenery mesh must be at least this wide to count

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


def is_structure(item, quirks):
    """Is this mesh a man-made structure (drawn red, as a vector polygon) rather than ground?"""
    n = item[-1].split(':', 1)[1]
    if n.endswith('#floor'):                         # dry-dock floor split off a structure mesh: ground
        return False
    if n in quirks.get('land_names', ()):             # per-island override: a mesh the name rules would call a structure
        return False
    if LAND_NAME_RE.match(n):                        # e.g. mountain_shipyard is land despite the word "shipyard"
        return False
    if quirks.get('extra_struct_regex') and item[-1].startswith('L24:') and re.search(quirks['extra_struct_regex'], n):
        return True
    if STRUCT_CLASS_RE.search(n):
        return True
    V = item[1]
    return n.lower().startswith('cube') and max(np.ptp(V[:, 0]), np.ptp(V[:, 2])) < SMALL_CUBE_MAX_M

