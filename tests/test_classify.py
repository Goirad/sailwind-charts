import numpy as np

from sailwind_charts.classify import is_structure, scenery_category
from sailwind_charts.util import slug


def item(name, width=10.0):
    V = np.array([[0, 0, 0], [width, 1, 0], [0, 1, width]], float)
    return ('mesh', V, np.array([[0, 1, 2]]), 'L24:' + name)


def test_structure_by_name():
    assert is_structure(item('east_dock_base'), {})
    assert is_structure(item('pier'), {})
    assert not is_structure(item('island_terrain_original'), {})


def test_names_lie_mountain_shipyard_is_land():
    assert not is_structure(item('mountain_shipyard'), {})


def test_small_cube_is_a_building_but_large_cube_is_land():
    assert is_structure(item('Cube_001', 30), {})
    assert not is_structure(item('Cube_001', 200), {})


def test_land_names_quirk_overrides_the_name_rules():
    assert not is_structure(item('Cube_001', 30), {'land_names': ['Cube_001']})


def test_scenery_categories():
    assert scenery_category('palm tree') == 'tree'
    assert scenery_category('bush_a') == 'bush'
    assert scenery_category('rocks_small') == 'rock'
    assert scenery_category('buildings_arab') == 'struct'
    assert scenery_category('fruit') is None


def test_slug_drops_apostrophes():
    assert slug("Al'Nilem") == 'AlNilem'
    assert slug('Gold Rock City') == 'Gold_Rock_City'
