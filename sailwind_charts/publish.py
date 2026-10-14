"""
Publish the built site to a `gh-pages` branch whose history is always a single commit, so the charts (binaries) never accumulate
in any history.  The site is copied into a throw-away git repository and force-pushed; the working repository is not touched.

    python -I charts.py publish --dry-run          # build the commit, print what would be pushed
    python -I charts.py publish --yes              # force-push site/ to <remote> gh-pages
"""
import os
import shutil
import subprocess
import sys
import tempfile

from .util import log

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args, cwd=None, check=True):
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f'git {" ".join(args)} failed: {r.stderr.strip() or r.stdout.strip()}')
    return r.stdout.strip() if r.returncode == 0 else ''


def resolve_remote_url(remote, fallback_repo_url):
    """The URL to push to: the named git remote of this repository, else the catalogue's repo_url."""
    url = _git('remote', 'get-url', remote, cwd=HERE, check=False)
    if url:
        return url
    if remote.startswith(('http://', 'https://', 'git@', '/', '.', 'file:')) or os.path.isdir(remote):
        return remote                                   # a URL or path given directly
    if fallback_repo_url:
        return fallback_repo_url if fallback_repo_url.endswith('.git') else fallback_repo_url + '.git'
    raise SystemExit(f'no git remote {remote!r} and no [site].repo_url in the catalogue: nothing to push to')


def source_revision():
    rev = _git('rev-parse', '--short', 'HEAD', cwd=HERE, check=False) or 'unknown'
    dirty = bool(_git('status', '--porcelain', cwd=HERE, check=False))
    return rev + ('-dirty' if dirty else '')


def publish(site_dir, branch, url, dry_run=False):
    if not os.path.exists(os.path.join(site_dir, 'index.html')):
        raise SystemExit(f'{site_dir}/index.html not found: build the site first (charts.py site)')
    rev = source_revision()
    name = _git('config', 'user.name', cwd=HERE, check=False) or 'sailwind-charts'
    email = _git('config', 'user.email', cwd=HERE, check=False) or 'sailwind-charts@users.noreply.github.com'
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, 'site')
        shutil.copytree(site_dir, work)
        open(os.path.join(work, '.nojekyll'), 'w').close()           # serve the files as they are
        _git('init', '-q', '-b', branch, cwd=work)
        _git('add', '-A', cwd=work)
        _git('-c', f'user.name={name}', '-c', f'user.email={email}', 'commit', '-q', '-m', f'Publish site built from {rev}', cwd=work)
        files = _git('ls-files', cwd=work).count('\n') + 1
        size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(work) if '.git' not in d.split(os.sep) for f in fs)
        log(f'{files} files, {size / 1e6:.1f} MB, built from {rev}; target {url} branch {branch}')
        if dry_run:
            log('dry run: nothing pushed')
            return
        _git('push', '--force', url, f'HEAD:refs/heads/{branch}', cwd=work)
        log(f'pushed (force) to {url} {branch}')


def confirm_or_exit(url, branch, yes):
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit('refusing to force-push without --yes (not an interactive terminal)')
    if input(f'Force-push the site to {url} branch {branch}, replacing its history? [y/N] ').strip().lower() != 'y':
        raise SystemExit('cancelled')
