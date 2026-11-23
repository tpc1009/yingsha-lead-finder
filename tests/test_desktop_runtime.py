import json
import os
import subprocess

from yingsha_leads.browser import BrowserBridge


def test_finder_launch_finds_openclaw_and_node_outside_path(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    directory = tmp_path / '.npm-global/bin'
    directory.mkdir(parents=True)
    # The CLI uses env node, like the actual npm installation.
    cli = directory / 'openclaw'
    cli.write_text('#!/usr/bin/env node\n')
    cli.chmod(0o755)
    node = directory / 'node'
    node.write_text('#!/bin/sh\nprintf \'{"ok":true,"tabs":[]}\\n\'\n')
    node.chmod(0o755)
    # Exclude this development machine's installations, but run real subprocesses.
    import shutil
    original_which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: original_which(name, path=os.pathsep.join(p for p in (path or os.environ['PATH']).split(os.pathsep) if p.startswith(str(tmp_path)) or p in ['/usr/bin', '/bin'])))
    status = BrowserBridge().probe()
    assert status['connected'] is False
    assert '未接入可采集标签页' in status['message']


def test_nvm_installation_is_discovered_without_running_shell_profiles(tmp_path):
    from yingsha_leads.desktop_runtime import cli_environment
    directory = tmp_path / '.nvm/versions/node/v24.10.0/bin'
    directory.mkdir(parents=True)
    env = cli_environment({'PATH': '/usr/bin:/bin'}, home=tmp_path)
    assert str(directory) in env['PATH'].split(os.pathsep)
    assert env['PATH'].split(os.pathsep)[:2] == ['/usr/bin', '/bin']


def test_bridge_does_not_expose_subprocess_output_on_failure(monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, 'private-output', 'private-output')
    monkeypatch.setattr(subprocess, 'run', failed)
    status = BrowserBridge().probe()
    assert not status['connected']
    assert 'private-output' not in json.dumps(status)
