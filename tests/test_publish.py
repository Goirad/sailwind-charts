import subprocess

import pytest

from sailwind_charts.publish import publish, resolve_remote_url


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def site(tmp_path):
    d = tmp_path / 'site'
    (d / 'charts').mkdir(parents=True)
    (d / 'index.html').write_text('<html>v1</html>')
    (d / 'charts' / 'a.jpg').write_bytes(b'\xff\xd8 jpg')
    return d


def test_publish_keeps_a_single_commit_history(tmp_path, site):
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'gh-pages', str(bare)], check=True)
    publish(str(site), 'gh-pages', str(bare))
    (site / 'index.html').write_text('<html>v2</html>')
    publish(str(site), 'gh-pages', str(bare))                                  # force-push: the old commit is replaced
    assert git('rev-list', '--count', 'gh-pages', cwd=bare) == '1'
    assert 'v2' in git('show', 'gh-pages:index.html', cwd=bare)
    assert set(git('ls-tree', '-r', '--name-only', 'gh-pages', cwd=bare).split()) == {'.nojekyll', 'charts/a.jpg', 'index.html'}


def test_dry_run_pushes_nothing(tmp_path, site):
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'gh-pages', str(bare)], check=True)
    publish(str(site), 'gh-pages', str(bare), dry_run=True)
    assert git('branch', '--list', cwd=bare) == ''


def test_publish_needs_a_built_site(tmp_path):
    with pytest.raises(SystemExit):
        publish(str(tmp_path / 'nothing'), 'gh-pages', 'x')


def test_remote_falls_back_to_the_catalogue_repo_url():
    assert resolve_remote_url('no-such-remote', 'https://github.com/o/r') == 'https://github.com/o/r.git'
    with pytest.raises(SystemExit):
        resolve_remote_url('no-such-remote', '')
