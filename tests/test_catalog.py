from sailwind_charts.catalog import Catalog


def test_catalog_is_consistent():
    cat = Catalog.load()
    assert cat.islands and cat.composites
    for comp in cat.composites.values():
        for member in comp.members:
            assert member in cat.islands, f'{member} (member of {comp.title}) has no catalogue entry'
    names = [i.name for i in cat.islands.values() if i.name]
    assert len(names) == len(set(names)), 'real names must be unique (they become file names)'


def test_to_render_skips_members_and_skipped():
    cat = Catalog.load()
    keys = list(cat.islands)
    render, composites = cat.to_render(keys)
    assert not set(render) & set(cat.members)
    assert not any(cat.islands[k].skip for k in render)
    assert set(composites) == set(cat.composites)
    with_members, _ = cat.to_render(keys, include_members=True)
    assert set(cat.members) <= set(with_members) | {k for k in cat.members if cat.islands[k].skip}


def test_unknown_island_gets_a_blank_entry():
    entry = Catalog.load().island('island 999 nowhere')
    assert entry.title == 'island 999 nowhere' and not entry.quirks and not entry.skip
