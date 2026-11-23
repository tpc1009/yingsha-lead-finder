"""Verify the relocated app with an empty temporary database and a Finder-like PATH."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen


def wait_for(check, description, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = check()
            if result: return result
        except (OSError, ValueError): pass
        time.sleep(.15)
    raise AssertionError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    args = parser.parse_args()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    def get(path):
        with urlopen(base + path, timeout=2) as response: return response.read()
    with tempfile.TemporaryDirectory(prefix='yingsha-mac-app-') as temporary:
        directory = Path(temporary)
        relocated = directory / 'Moved 应用 with spaces.app'
        shutil.copytree(args.app, relocated, symlinks=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(relocated)], check=True)
        command = [str(relocated / 'Contents/MacOS/YingshaLauncher'), '--no-open', '--port', str(port), '--data-dir', str(directory / 'data')]
        env = dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin', PYTHONHOME='/not-installed', PYTHONPATH='/not-installed')
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        second = None
        try:
            status = wait_for(lambda: json.loads(get('/api/status')), 'Relocated app failed to start without external Python')
            assert status['app'] == 'yingsha-lead-finder'
            assert Path(status['data_dir']) == directory / 'data'
            assert status['stats'] == {'leads': 0, 'pending': 0, 'running': 0}
            assert json.loads(get('/api/jobs'))['jobs'] == []
            assert json.loads(get('/api/facebook/sessions'))['sessions'] == []
            assert b'facebook-collect-step' in get('/')
            assert b'facebook-batch-loading' in get('/static/app.js')
            assert b'facebook-loading-spinner' in get('/static/style.css')
            request = Request(base + '/api/search', data=json.dumps({'product':'abaya fabric','buyer_type':'服装品牌','country':'UAE'}).encode(), headers={'Content-Type':'application/json','X-CSRF-Token':status['csrf_token']}, method='POST')
            with urlopen(request, timeout=5) as response: queries = json.load(response)['queries']
            assert queries[0]['url'].startswith('https://www.facebook.com/search/pages?')
            assert get('/api/export?format=xlsx').startswith(b'PK')
            # Attaching a second launcher must not own or terminate the first service.
            second = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(2)
            assert second.poll() is None
            second.terminate(); second.wait(timeout=10)
            assert json.loads(get('/api/status'))['app'] == 'yingsha-lead-finder'
            process.terminate(); process.wait(timeout=45)
            def stopped():
                try: get('/api/status'); return False
                except OSError: return True
            wait_for(stopped, 'Owned server did not exit with native app', timeout=10)
            with socket.socket() as listener:
                # Closed HTTP connections can remain in TIME_WAIT after a clean exit.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(('127.0.0.1', port))
            print('PASS: relocated signed app, minimal PATH, empty data, search, static UI, XLSX, repeated launch, graceful exit.')
        finally:
            for item in [second, process]:
                if item is not None and item.poll() is None:
                    item.terminate()
                    try: item.wait(timeout=45)
                    except subprocess.TimeoutExpired: item.kill(); item.wait()


if __name__ == '__main__': main()
