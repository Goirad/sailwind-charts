"""
Build the static site: one page, no JavaScript, no dependencies beyond the standard library.

    python -I charts.py site --charts charts --out site

Input is a folder of charts rendered with `render --all --key separate` (NAME.jpg, NAME_key.png, NAME.json) and the catalogue
(data/islands.toml: [site] title / author / repo_url / regions, and `region` on every island).  Output is site/index.html plus the
images.  The page works from file:// too.  Mechanics, all CSS:
  - each chart is a <section id=NAME>; the one named in the URL fragment (:target) is shown, the first one when there is none;
  - the key is a separate transparent image over the chart; a checkbox before <main> hides it (#key-toggle:not(:checked) ~ ...);
  - the chart is a fixed 1100 CSS px wide, so browser zoom / pinch magnify it (a width in % would cancel the zoom).
"""
import html
import json
import os
import shutil

from .util import log, slug

SHEET_WIDTH_PX = 1100


def collect_entries(catalog, charts_dir, lenient=False):
    """[(title, stem, region)] for every chart that is in the catalogue and not skipped, plus a list of problems."""
    problems, entries = [], []
    wanted = [(i.title, i.region, i.mod) for i in catalog.islands.values()
              if not i.skip and i.key not in catalog.members] + [(c.title, c.region, False) for c in catalog.composites.values()]
    regions = list(catalog.site.get('regions', []))
    for title, region, mod in wanted:
        stem = slug(title)
        if not os.path.exists(os.path.join(charts_dir, stem + '.jpg')):
            if not mod:
                problems.append(f'no chart for {title!r} ({stem}.jpg)')
            continue
        for ext in ('_key.png', '.json'):
            if not os.path.exists(os.path.join(charts_dir, stem + ext)):
                problems.append(f'{title!r}: {stem}{ext} missing (render with --key separate)')
        if region not in regions:
            if lenient:
                region = 'Unassigned'
            else:
                problems.append(f'{title!r}: region {region!r} is not one of [site].regions {regions}')
        entries.append((title, stem, region))
    return entries, problems


def group_by_region(entries, regions):
    order = list(regions) + [r for r in dict.fromkeys(e[2] for e in entries) if r not in regions]
    return [(r, sorted((e for e in entries if e[2] == r), key=lambda e: e[0].lower())) for r in order if any(e[2] == r for e in entries)]


def _css(groups):
    first = groups[0][1][0][1]
    rules = []
    for _, items in groups:
        for _, stem, _r in items:                      # highlight the button of the shown chart
            rules.append(f'body:has(#{stem}:target) nav .buttons a[href="#{stem}"]{{background:var(--accent);color:#fff;border-color:var(--accent)}}')
    rules.append(f'body:not(:has(.chart:target)) nav .buttons a[href="#{first}"]{{background:var(--accent);color:#fff;border-color:var(--accent)}}')
    return CSS.replace('/*ACTIVE*/', '\n'.join(rules))


def render_page(catalog, groups, sizes):
    site = catalog.site
    title = html.escape(site.get('title', 'Charts'))
    collapsed = set(site.get('collapsed', []))          # regions that start closed (spoilers): their buttons stay hidden until opened
    nav = ''.join(
        f'<details class="region"{"" if region in collapsed else " open"}><summary>{html.escape(region)}</summary><div class="buttons">'
        + ''.join(f'<a href="#{stem}">{html.escape(name)}</a>' for name, stem, _ in items) + '</div></details>'
        for region, items in groups)
    sections = []
    for _, items in groups:
        for name, stem, _r in items:
            meta = sizes[stem]
            w, h = meta['image_px']
            k = meta['key']
            sections.append(
                f'<section class="chart" id="{stem}"><div class="head"><h1>{html.escape(name)}</h1>'
                f'<a href="charts/{stem}.jpg" target="_blank" rel="noopener">Open full size</a></div>'
                f'<div class="pane"><div class="sheet"><img src="charts/{stem}.jpg" width="{w}" height="{h}" loading="lazy" alt="Bathymetry chart of {html.escape(name)}">'
                f'<img class="key" src="charts/{stem}_key.png" loading="lazy" alt="Chart key" '
                f'style="left:{k["left"] * 100:.3f}%;top:{k["top"] * 100:.3f}%;width:{k["width"] * 100:.3f}%"></div></div></section>')
    repo, author = site.get('repo_url', ''), site.get('author', '')
    about = ['<p>Bathymetry charts of the islands of Sailwind, generated from the game\'s own data. North is up; depths are below sea level.</p>']
    if repo:
        about.append(f'<p><a href="{html.escape(repo)}">Source code and data</a></p>')
    if author:
        about.append(f'<p>Charts by {html.escape(author)}.</p>')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{_css(groups)}</style></head>
<body>
<input type="checkbox" id="key-toggle" checked>
<nav><div class="site">{title}</div>{nav}</nav>
<header><label class="switch" for="key-toggle"><span class="track"><span class="knob"></span></span><span class="label">Key</span></label>
<details class="about"><summary>About</summary><div>{''.join(about)}</div></details></header>
<main>{''.join(sections)}</main>
</body></html>
'''


CSS = '''
:root{--bg:#f6f8fa;--fg:#14202b;--muted:#5b6b7a;--line:#c9d3dc;--accent:#1f5f99;--card:#fff;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#10171e;--fg:#e6edf3;--muted:#9fb0c0;--line:#2b3946;--accent:#4a90d0;--card:#17212b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;display:grid;grid-template-columns:270px 1fr;grid-template-rows:auto 1fr;min-height:100vh}
header{grid-column:2;grid-row:1;display:flex;align-items:center;justify-content:flex-end;gap:1.5rem;padding:.7rem 1.2rem;border-bottom:1px solid var(--line);background:var(--card);position:relative}
.site{font-weight:700;font-size:1.1rem;margin-bottom:1rem}
#key-toggle{position:absolute;opacity:0;pointer-events:none}
.switch{display:flex;align-items:center;gap:.6rem;cursor:pointer;font-weight:600;padding:.35rem .8rem;border:2px solid var(--accent);border-radius:999px;user-select:none}
.switch .track{width:46px;height:24px;border-radius:12px;background:var(--line);position:relative;transition:background .15s}
.switch .knob{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:left .15s}
.switch .label::after{content:": hidden"}
#key-toggle:checked ~ header .switch .track{background:var(--accent)}
#key-toggle:checked ~ header .switch .knob{left:25px}
#key-toggle:checked ~ header .switch .label::after{content:": shown"}
#key-toggle:focus-visible ~ header .switch{outline:3px solid var(--accent);outline-offset:2px}
#key-toggle:not(:checked) ~ main .key{display:none}
.about summary{cursor:pointer;font-weight:600;padding:.35rem .8rem;border:1px solid var(--line);border-radius:6px;list-style:none}
.about[open] summary{background:var(--accent);color:#fff;border-color:var(--accent)}
.about div{position:absolute;right:1.2rem;top:100%;width:min(24rem,90vw);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.2rem 1rem;box-shadow:0 8px 24px rgba(0,0,0,.2);z-index:10}
.about a,.head a{color:var(--accent)}
nav{grid-column:1;grid-row:1/3;padding:1rem 1.2rem;border-right:1px solid var(--line);background:var(--card);position:sticky;top:0;height:100vh;overflow:auto}
.region{margin-bottom:.9rem}
.region summary{cursor:pointer;font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:.2rem 0;list-style:none}
.region summary::before{content:"\\25B8";display:inline-block;width:1.1em}
.region[open] summary::before{content:"\\25BE"}
.region .buttons{margin-top:.4rem}
.buttons{display:flex;flex-wrap:wrap;gap:.35rem}
nav .buttons a{padding:.3rem .7rem;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);text-decoration:none;font-size:.92rem}
nav .buttons a:hover{border-color:var(--accent)}
main{grid-column:2;grid-row:2;padding:1rem 1.2rem;min-width:0}
.chart{display:none}
.chart:target{display:block}
main:not(:has(.chart:target)) .chart:first-child{display:block}
.head{display:flex;align-items:baseline;gap:1rem;margin-bottom:.6rem}
.head h1{margin:0;font-size:1.5rem}
.pane{overflow:auto;width:fit-content;max-width:100%;padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--card)}
.sheet{position:relative;width:__W__px}
.sheet img{display:block;width:100%;height:auto}
.sheet img.key{position:absolute}
/*ACTIVE*/
'''.replace('__W__', str(SHEET_WIDTH_PX))


def build_site(catalog, charts_dir, out_dir, lenient=False):
    entries, problems = collect_entries(catalog, charts_dir, lenient)
    if not entries:
        problems.append('no charts found in ' + charts_dir)
    if problems:
        for p in problems:
            log('PROBLEM:', p)
        raise SystemExit(f'{len(problems)} problem(s); nothing written (use --lenient to put unassigned islands under "Unassigned")')
    groups = group_by_region(entries, catalog.site.get('regions', []))
    os.makedirs(os.path.join(out_dir, 'charts'), exist_ok=True)
    sizes = {}
    for _, items in groups:
        for _name, stem, _r in items:
            sizes[stem] = json.load(open(os.path.join(charts_dir, stem + '.json')))
            for ext in ('.jpg', '_key.png'):
                shutil.copy(os.path.join(charts_dir, stem + ext), os.path.join(out_dir, 'charts', stem + ext))
    with open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(render_page(catalog, groups, sizes))
    log(f'wrote {out_dir}/index.html: {len(entries)} charts in {len(groups)} group(s)')
    return entries
