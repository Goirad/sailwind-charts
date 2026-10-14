"""The island catalogue (data/islands.toml): real names, status, per-island quirks and composites."""
import os
import tomllib
from dataclasses import dataclass, field

DEFAULT_CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'islands.toml')


@dataclass(frozen=True)
class Island:
    key: str                  # developer object name in the game data
    name: str = ''            # real (in-game) name; blank = unknown
    skip: bool = False
    mod: bool = False
    region: str = ''          # button group on the site (one of the catalogue's site regions)
    status: str = ''
    notes: str = ''
    quirks: dict = field(default_factory=dict)

    @property
    def title(self):
        return self.name or self.key


@dataclass(frozen=True)
class Composite:
    title: str
    members: tuple
    notes: str = ''
    region: str = ''
    res: float = 1.0          # raster cell size of the combined map in m
    max_cells: int = 0        # per-member raster cells per side (0 = the default); raise it so big members keep `res`
    max_px: int = 0           # image size cap for this map (0 = the default)


class Catalog:
    def __init__(self, islands, composites, site=None):
        self.site = site or {}          # [site]: title, author, repo_url, regions (ordered)
        self.islands = islands          # {key: Island}
        self.composites = composites    # {title: Composite}
        self.members = {m: c.title for c in composites.values() for m in c.members}

    @classmethod
    def load(cls, path=DEFAULT_CATALOG):
        if not os.path.exists(path):
            return cls({}, {}, {})
        with open(path, 'rb') as f:
            raw = tomllib.load(f)
        islands = {k: Island(key=k, **v) for k, v in raw.get('islands', {}).items()}
        composites = {t: Composite(title=t, members=tuple(v['members']), **{k: v[k] for k in ('notes', 'region', 'res', 'max_cells', 'max_px') if k in v}) for t, v in raw.get('composites', {}).items()}
        return cls(islands, composites, raw.get('site', {}))

    def island(self, key):
        """The catalogue entry of an island; an island that is not catalogued gets a blank one (title = object name)."""
        return self.islands.get(key) or Island(key=key)

    def to_render(self, game_keys, include_members=False):
        """(island keys, composite titles) that `render --all` draws: not skipped, and members only inside their composite."""
        keys = [k for k in game_keys if not self.island(k).skip and (include_members or k not in self.members)]
        return keys, list(self.composites)

    def problems(self, game_keys, with_mods):
        """Differences between the catalogue and the game data, as readable lines (empty = consistent)."""
        out = []
        game = set(game_keys)
        out += [f'in the game but not in the catalogue: {k}' for k in sorted(game - set(self.islands))]
        out += [f'in the catalogue but not in the game: {k}' for k in sorted(set(self.islands) - game)
                if with_mods or not self.islands[k].mod]
        out += [f'no real name yet: {k}' for k in sorted(game & set(self.islands))
                if not self.islands[k].name and not self.islands[k].skip and k not in self.members]
        out += [f'composite {c.title!r}: member not in the game: {m}' for c in self.composites.values() for m in c.members if m not in game]
        return out
