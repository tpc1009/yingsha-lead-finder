"""Discover existing CLI installations when launched from Finder's minimal PATH."""
import os
from pathlib import Path
import re


def cli_environment(environ=None, home=None):
    env = dict(os.environ if environ is None else environ)
    home = Path.home() if home is None else Path(home)
    paths = env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin').split(os.pathsep)
    paths.extend(str(home / relative) for relative in (
        '.npm-global/bin', '.local/bin', '.volta/bin', '.fnm/aliases/default/bin',
        '.local/share/fnm/aliases/default/bin', '.nvm/current/bin',
    ))
    paths.extend(['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'])
    versions = home / '.nvm/versions/node'
    if versions.is_dir():
        # Read installation directory names only, never shell profiles or credentials.
        installations = sorted(versions.glob('v*/bin'), key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.parent.name)), reverse=True)
        paths.extend(str(p) for p in installations if p.is_dir())
    env['PATH'] = os.pathsep.join(dict.fromkeys(p for p in paths if p and Path(p).is_absolute()))
    return env
