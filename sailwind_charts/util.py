"""Small shared helpers."""
import re


def log(*a):
    print(*a, flush=True)


def slug(name):
    """File-name form of a title: letters and digits, underscores between words, apostrophes vanish (Al'Nilem -> AlNilem)."""
    return re.sub(r'[^A-Za-z0-9]+', '_', name.replace("'", '')).strip('_')
