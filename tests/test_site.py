import json
import re

import pytest
from PIL import Image

from sailwind_charts.catalog import Catalog, Composite, Island
from sailwind_charts.site import build_site


def make_charts(folder, stems):
    for stem in stems:
        Image.new('RGB', (40, 30), 'blue').save(folder / f'{stem}.jpg')
        Image.new('RGBA', (10, 8), (255, 255, 255, 200)).save(folder / f'{stem}_key.png')
        (folder / f'{stem}.json').write_text(json.dumps(dict(title=stem, image_px=[40, 30], key=dict(left=0.6, top=0.7, width=0.3, px=[10, 8]))))


def catalog(regions=('North', 'South'), **site):
    islands = {'island a': Island(key='island a', name='Alpha', region='North'),
               'island b': Island(key='island b', name="Bob's Rock", region='South'),
               'island c': Island(key='island c', name='Hidden', skip=True)}
    return Catalog(islands, {'Pair': Composite(title='Pair', members=(), region='North')},
                   dict(title='Test Charts', regions=list(regions), collapsed=['South'], author='Ann', repo_url='https://example.org/repo', **site))


def test_site_is_built_with_buttons_sections_and_about(tmp_path):
    charts, out = tmp_path / 'charts', tmp_path / 'site'
    charts.mkdir()
    make_charts(charts, ['Alpha', 'Bobs_Rock', 'Pair'])
    build_site(catalog(), str(charts), str(out))
    page = (out / 'index.html').read_text()
    ids = re.findall(r'<section class="chart" id="(\w+)"', page)
    assert sorted(ids) == ['Alpha', 'Bobs_Rock', 'Pair']
    assert sorted(set(re.findall(r'nav a|<a href="#(\w+)">', page)) - {''}) == sorted(ids)        # every button points at a chart
    assert '<details class="region" open><summary>North' in page and '<details class="region"><summary>South' in page   # spoiler-style regions start closed
    assert page.index('North') < page.index('South') and 'Hidden' not in page
    assert 'https://example.org/repo' in page and 'Charts by Ann' in page and 'id="key-toggle"' in page
    for stem in ids:
        assert (out / 'charts' / f'{stem}.jpg').exists() and (out / 'charts' / f'{stem}_key.png').exists()


def test_island_without_a_valid_region_fails_unless_lenient(tmp_path):
    charts = tmp_path / 'charts'
    charts.mkdir()
    make_charts(charts, ['Alpha', 'Bobs_Rock', 'Pair'])
    with pytest.raises(SystemExit):
        build_site(catalog(regions=('North',)), str(charts), str(tmp_path / 'site'))
    build_site(catalog(regions=('North',)), str(charts), str(tmp_path / 'site'), lenient=True)
    assert 'Unassigned' in (tmp_path / 'site' / 'index.html').read_text()


def test_missing_key_files_are_reported(tmp_path):
    charts = tmp_path / 'charts'
    charts.mkdir()
    make_charts(charts, ['Alpha', 'Pair'])
    Image.new('RGB', (4, 4)).save(charts / 'Bobs_Rock.jpg')          # chart without key / json
    with pytest.raises(SystemExit):
        build_site(catalog(), str(charts), str(tmp_path / 'site'))
