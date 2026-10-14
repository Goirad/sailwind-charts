"""Command line: list, names, manifest, render, compose, selftest, sheet, explore."""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .catalog import DEFAULT_CATALOG, Catalog
from .gamedata import DEFAULT_GAME_DATA, GameData, _comp, _tf, _trs
from .grid import GRID_MAX_CELLS, build_grid, compose_grids, grid_summary
from .render import render_chart
from .style import Style
from .util import log, slug

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(HERE, 'charts.py')
REFERENCE_DIR = os.path.join(HERE, 'reference')
SELFTEST_ISLAND = 'island 4 A (fish island)'     # shown in-game as "Albacore Island"
SELFTEST_STYLE = Style(size_px=2000, quality=80, key='none')


# --------------------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------------------
def _style(args, quirks=None):
    return Style(size_px=args.size, quality=args.quality, key=args.key, margin_frac=args.margin,
                 key_px=args.key_width, max_depth_m=args.max_depth,
                 nodata_is_water=bool((quirks or {}).get('nodata_is_water')))


def _resolve(gd, catalog, name):
    """Object name of an island given either its object name or its catalogue (real) name."""
    keys = [i['name'] for i in gd.islands()]
    if name in keys:
        return name
    named = [k for k in keys if catalog.island(k).name.lower() == name.lower()]
    if len(named) == 1:
        return named[0]
    raise SystemExit(f'island {name!r} not found. Use the list command to see names.')


# --------------------------------------------------------------------------------------------------
# list / names / manifest
# --------------------------------------------------------------------------------------------------
def cmd_list(args, gd, catalog):
    log(f'{"object name":42s} {"real name":22s} {"parent":22s} children  scene  note')
    for isl in gd.islands():
        m = _island_number(isl['name'])
        scene = '-' if (isl['nested'] or m is None) else ('yes' if gd.scene(m) is not None else 'MISSING')
        entry = catalog.island(isl['name'])
        notes = [n for n, on in (('nested', isl['nested']), ('MOD', isl.get('mod')), ('inactive', not isl['active']),
                                 ('skip', entry.skip), ('quirk', bool(entry.quirks)), ('composite', isl['name'] in catalog.members)) if on]
        log(f'{isl["name"]:42s} {entry.name:22s} {isl["parent"]:22s} {isl["children"]:8d}  {scene:6s} {" ".join(notes)}')


def _island_number(name):
    import re
    m = re.match(r'island (\d+)', name)
    return int(m.group(1)) if m else None


def cmd_names(args, gd, catalog):
    """Check data/islands.toml against the game: new islands, stale entries, islands still without a real name."""
    problems = catalog.problems([i['name'] for i in gd.islands()], with_mods=args.mods)
    for p in problems:
        log(p)
    log(f'{len(catalog.islands)} islands, {len(catalog.composites)} composites in the catalogue;', f'{len(problems)} problem(s)')
    sys.exit(1 if problems else 0)


def manifest_dict(gd):
    out = {}
    for isl in gd.islands():
        t = _tf(isl['go'])
        n_terrain = sum(1 for ch in t.m_Children if _comp(ch.deref().parse_as_object().m_GameObject.deref().parse_as_object(), 'Terrain'))
        out[isl['name']] = dict(parent=isl['parent'], children=isl['children'], terrains=n_terrain, nested=isl['nested'], active=isl['active'])
    return out


def cmd_manifest(args, gd, catalog):
    path = os.path.join(REFERENCE_DIR, 'islands_manifest.json')
    cur = manifest_dict(gd)
    if args.check:
        ref = json.load(open(path))
        added = sorted(set(cur) - set(ref)); removed = sorted(set(ref) - set(cur))
        changed = sorted(n for n in set(cur) & set(ref) if cur[n] != ref[n])
        log('NEW islands (render and review these):', added or 'none')
        log('REMOVED islands:', removed or 'none')
        for n in changed:
            log('CHANGED:', n, ref[n], '->', cur[n])
        sys.exit(1 if (added or removed or changed) else 0)
    json.dump(cur, open(path, 'w'), indent=1, sort_keys=True)
    log('wrote', path, len(cur), 'islands')


# --------------------------------------------------------------------------------------------------
# render / compose
# --------------------------------------------------------------------------------------------------
def _build(args, gd, key, catalog):
    return build_grid(gd, gd.find(key), quirks=catalog.island(key).quirks, scenery=not args.no_scenery,
                      max_cells=args.max_cells, adaptive_res=not args.fixed_res)


def _render_one(args, gd, catalog, key):
    t0 = time.time()
    grid = _build(args, gd, key, catalog)
    if grid is None:
        log(f'SKIP {key}: no geometry')
        return False
    if args.dump_grid:
        np.savez(os.path.join(args.dump_grid, slug(key) + '_grid.npz'), xs=grid['xs'], zs=grid['zs'], Y=grid['Y'])
    title = args.title or catalog.island(key).title
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, slug(title) + '.jpg')
    info = render_chart(grid, title, out, _style(args, catalog.island(key).quirks))
    log(f'OK   {key}  -> {out}  ({time.time() - t0:.0f}s)  fit={all(ok for _, ok in info["panel_fit"])}')
    return True


def _flags(args):
    """The options a worker process needs to render the same way as this one."""
    return (['--out', args.out, '--size', str(args.size), '--quality', str(args.quality), '--max-cells', str(args.max_cells),
             '--margin', str(args.margin), '--key-width', str(args.key_width), '--max-depth', str(args.max_depth)]
            + ['--key', args.key] + (['--no-scenery'] if args.no_scenery else [])
            + (['--fixed-res'] if args.fixed_res else []))


def cmd_render(args, gd, catalog):
    if args.all:
        keys, composites = catalog.to_render([i['name'] for i in gd.islands()], include_members=args.members)
    else:
        keys, composites = [_resolve(gd, catalog, n) for n in args.names], []
    if not keys and not composites:
        raise SystemExit('give island names or --all')
    if args.title and len(keys) != 1:
        raise SystemExit('--title only makes sense with one island')
    if len(keys) == 1 and not composites:
        _render_one(args, gd, catalog, keys[0])
        return
    # many islands: one subprocess each (keeps memory bounded, isolates failures), --jobs at a time
    base = [sys.executable, '-I', LAUNCHER, '--game-data', args.game_data, '--catalog', args.catalog] + (['--mods'] if args.mods else [])
    jobs = [base + ['compose', '--group', t] + _flags(args) for t in composites]          # composites are the biggest jobs: first
    jobs += [base + ['render', k] + _flags(args) + (['--dump-grid', args.dump_grid] if args.dump_grid else []) for k in keys]

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or [''])[-1]
        if r.returncode != 0:
            tail = f'FAIL {cmd[len(base) + 1]}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}'
        log(tail)
        return r.returncode == 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        ok = list(ex.map(run, jobs))
    log(f'{sum(ok)}/{len(jobs)} rendered in {time.time() - t0:.0f}s')


def cmd_compose(args, gd, catalog):
    """Render several islands that belong together (e.g. the Lagoon ring) as ONE map in their true relative positions."""
    if args.group:
        if args.group not in catalog.composites:
            raise SystemExit(f'unknown composite {args.group!r}; known: {", ".join(catalog.composites)}')
        members, title = list(catalog.composites[args.group].members), args.title or args.group
    else:
        if not args.title:
            raise SystemExit('--title is required without --group')
        members, title = [_resolve(gd, catalog, n) for n in args.names], args.title
    args.fixed_res = True        # a composite has its own resolution (--res); the members keep their own
    parts = []
    for key in members:
        grid = _build(args, gd, key, catalog)
        if grid is None:
            log('skip (no geometry):', key); continue
        M = _trs(_tf(gd.find(key)['go']))
        parts.append((grid, (M[0, 3], M[2, 3])))
        log(f'  {key}: grid {grid["Y"].shape} res {grid["res"]} origin ({M[0, 3]:.0f}, {M[2, 3]:.0f})')
    res = max(max(g['res'] for g, _ in parts), args.res)
    grid = compose_grids(parts, res)
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, slug(title) + '.jpg')
    info = render_chart(grid, title, out, _style(args))
    log(f'OK composite {title!r} of {len(parts)} islands -> {out} grid {grid["Y"].shape} res {res} fit={all(ok for _, ok in info["panel_fit"])}')


# --------------------------------------------------------------------------------------------------
# selftest / sheet
# --------------------------------------------------------------------------------------------------
def cmd_selftest(args, gd, catalog):
    """Albacore Island (terrain-only land, rotated+scaled parent, pier, no key) must reproduce the stored fingerprint and image."""
    from PIL import Image
    ref_img = os.path.join(REFERENCE_DIR, 'albacore_2k_q80_nokey.jpg')
    ref_json = os.path.join(REFERENCE_DIR, 'albacore_summary.json')
    grid = build_grid(gd, gd.find(SELFTEST_ISLAND), quirks=catalog.island(SELFTEST_ISLAND).quirks, adaptive_res=True)
    got = grid_summary(grid)
    if args.update:       # deliberate re-baselining after an intentional change to the pipeline
        json.dump(got, open(ref_json, 'w'), indent=1)
        render_chart(grid, SELFTEST_ISLAND, ref_img, SELFTEST_STYLE)
        log('reference updated:', ref_img)
        return
    bad = []
    for k, v in json.load(open(ref_json)).items():
        same = (got[k] == v) if isinstance(v, (list, int)) else math.isclose(got[k], v, rel_tol=1e-9, abs_tol=1e-6)
        if not same:
            bad.append((k, v, got[k]))
    log('grid summary:', 'MATCH' if not bad else 'MISMATCH')
    for b in bad:
        log('   ', b)
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'albacore.jpg')
        render_chart(grid, SELFTEST_ISLAND, out, SELFTEST_STYLE)
        a = np.asarray(Image.open(out).convert('RGB')).astype(int)
        b = np.asarray(Image.open(ref_img).convert('RGB')).astype(int)
        if a.shape != b.shape:
            log('image shape differs', a.shape, b.shape); bad.append('image shape')
        else:
            d = np.abs(a - b)
            log(f'image diff vs reference: max={d.max()} mean={d.mean():.6f} differing pixels={(d.sum(2) > 0).sum()}')
            if d.max() > 2:
                bad.append('image differs')
    log('SELFTEST', 'PASS' if not bad else 'FAIL')
    sys.exit(0 if not bad else 1)


def cmd_sheet(args, gd, catalog):
    """Contact sheet of every chart in a folder (quick visual review after a re-run)."""
    from PIL import Image, ImageDraw
    files = sorted(f for f in os.listdir(args.folder) if f.lower().endswith(('.jpg', '.png')))
    if not files:
        raise SystemExit('no images in ' + args.folder)
    T, cols = args.tile, args.cols
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * T, rows * (T + 16)), 'white')
    d = ImageDraw.Draw(sheet)
    for k, f in enumerate(files):
        im = Image.open(os.path.join(args.folder, f))
        im.thumbnail((T - 4, T - 4))
        x, y = (k % cols) * T + 2, (k // cols) * (T + 16)
        sheet.paste(im, (x, y + 16))
        d.text((x + 2, y + 2), os.path.splitext(f)[0][:48], fill='black')
    sheet.save(args.out, quality=80) if args.out.lower().endswith('.jpg') else sheet.save(args.out)
    log(f'wrote {args.out}  ({len(files)} charts, {sheet.size[0]}x{sheet.size[1]})')


# --------------------------------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------------------------------
def _add_render_options(p):
    p.add_argument('--out', default='charts')
    p.add_argument('--size', type=int, default=2000, help='long side of the image in px (default %(default)s)')
    p.add_argument('--quality', type=int, default=80, help='JPEG quality (default %(default)s)')
    p.add_argument('--key', choices=('baked', 'separate', 'none'), default=Style.key,
                   help="key panel: 'baked' into the image, 'separate' (the image reserves its corner; the key is written as NAME_key.png "
                        "with placement in NAME.json, for the site) or 'none' (default %(default)s)")
    p.add_argument('--margin', type=float, default=Style.margin_frac,
                   help='sea buffer around the chart as a fraction of its long side, at least %g m (default %%(default)s; 0 = none)' % Style.margin_min_m)
    p.add_argument('--key-width', type=int, default=Style.key_px, help='key width in final pixels (default %(default)s; 0 = full size)')
    p.add_argument('--max-depth', type=float, default=Style.max_depth_m, help='draw water down to this depth in m (default %(default)s, must exceed 10)')
    p.add_argument('--no-scenery', action='store_true', help="skip the scenery scenes' baked trees, bushes, fences, rocks")
    p.add_argument('--fixed-res', action='store_true', help='always use the 0.5 m raster (default: finer for small islands)')
    p.add_argument('--max-cells', type=int, default=GRID_MAX_CELLS,
                   help='max raster cells per side; coarser raster for huge islands (default %(default)d; ~8000 keeps 0.5 m at 10k px, needs several GB)')


def build_parser():
    ap = argparse.ArgumentParser(prog='charts.py', description='Nautical-style bathymetry charts for the islands of Sailwind.')
    ap.add_argument('--mods', action='store_true', help='also load island asset bundles from BepInEx/plugins (e.g. Better Ports: island 67 Bottleneck)')
    ap.add_argument('--game-data', default=DEFAULT_GAME_DATA, help='path to Sailwind_Data (default: $SAILWIND_DATA or %(default)s)')
    ap.add_argument('--catalog', default=DEFAULT_CATALOG, help='island catalogue (default: %(default)s)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='list the islands found in the game data')
    sub.add_parser('names', help='check data/islands.toml against the game (new / stale / unnamed islands)')
    p = sub.add_parser('manifest', help='write / compare reference/islands_manifest.json'); p.add_argument('--check', action='store_true')
    p = sub.add_parser('render', help='render charts (--all: every catalogued island, composites as one map)')
    p.add_argument('names', nargs='*', help='object names or real names'); p.add_argument('--all', action='store_true')
    p.add_argument('--title', help='override the title (single island)')
    p.add_argument('--jobs', type=int, default=12); p.add_argument('--dump-grid', help='also save the height grid (.npz) into this folder')
    p.add_argument('--members', action='store_true', help='with --all: also render the members of composites on their own')
    _add_render_options(p)
    p = sub.add_parser('compose', help='render several islands as one map in their true relative positions')
    p.add_argument('names', nargs='*'); p.add_argument('--title'); p.add_argument('--group', help='a composite from the catalogue')
    p.add_argument('--res', type=float, default=1.0, help='composite raster resolution in m (default %(default)s)')
    _add_render_options(p)
    p = sub.add_parser('site', help='build the static site from charts rendered with --key separate')
    p.add_argument('--charts', default='charts', help='folder with NAME.jpg, NAME_key.png, NAME.json (default %(default)s)')
    p.add_argument('--out', default='site'); p.add_argument('--lenient', action='store_true',
                   help='put islands without a valid region under "Unassigned" instead of failing')
    p = sub.add_parser('selftest', help='regression test: Albacore Island must reproduce the stored reference')
    p.add_argument('--update', action='store_true', help='rewrite the reference (only after an intended change!)')
    p = sub.add_parser('sheet', help='contact sheet of all charts in a folder'); p.add_argument('folder'); p.add_argument('--out', default='overview.jpg')
    p.add_argument('--tile', type=int, default=300); p.add_argument('--cols', type=int, default=8)
    p = sub.add_parser('explore', help='work out how an island is built')
    ep = p.add_subparsers(dest='what', required=True)
    q = ep.add_parser('tree'); q.add_argument('name'); q.add_argument('--depth', type=int, default=2)
    q = ep.add_parser('near'); q.add_argument('name'); q.add_argument('rect', nargs=4, type=float, metavar=('X0', 'X1', 'Z0', 'Z1'), help='island-local metres')
    q.add_argument('--min-foot', type=float, default=8.0); q.add_argument('--limit', type=int, default=40)
    q = ep.add_parser('terrain-check'); q.add_argument('name')
    ep.add_parser('layers')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == 'sheet':            # these need no game data
        return cmd_sheet(args, None, None)
    if args.cmd == 'site':
        from .site import build_site
        build_site(Catalog.load(args.catalog), args.charts, args.out, args.lenient)
        return 0
    if getattr(args, 'max_depth', 11) <= 10:
        raise SystemExit('--max-depth must exceed 10 (the shelf edge)')
    gd = GameData(args.game_data, mods=args.mods)
    catalog = Catalog.load(args.catalog)
    if args.cmd == 'render' and args.dump_grid:
        os.makedirs(args.dump_grid, exist_ok=True)
    if args.cmd == 'explore':
        from .explore import cmd_explore
        return cmd_explore(args, gd, catalog)
    return {'list': cmd_list, 'names': cmd_names, 'manifest': cmd_manifest, 'render': cmd_render, 'compose': cmd_compose,
            'selftest': cmd_selftest, 'sheet': cmd_sheet}[args.cmd](args, gd, catalog)
