"""Build a portable dual-architecture macOS app; never copy live user data."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
CACHE = ROOT / '.build'
PYTHON = '3.13.9'
ARCHES = {'arm64': 'aarch64', 'x86_64': 'x86_64'}
MACH_MAGICS = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def run(args):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(' '.join(map(str, args[:4])) + '\n' + result.stderr[-5000:])
    return result.stdout.strip()


def icon_png(path, size):
    # A simple code-drawn Y mark, with no external image dependency.
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            nx, ny = x / size, y / size
            mark = (0.22 < ny < 0.52 and (abs(nx - (0.25 + (ny - 0.22) * .84)) < .055 or abs(nx - (0.75 - (ny - 0.22) * .84)) < .055)) or (0.48 <= ny < 0.78 and abs(nx - .5) < .055)
            raw.extend((255, 254, 251) if mark else (218, 107, 66))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = output / '颖纱客户线索工具.app'
    if app.exists(): raise SystemExit('Refusing to replace an existing app: ' + str(app))
    uv = shutil.which('uv')
    if not uv: raise SystemExit('Build machine needs uv; recipient does not.')
    CACHE.mkdir(exist_ok=True)
    requirements = CACHE / 'requirements.txt'
    run([uv, 'export', '--frozen', '--no-dev', '--no-emit-project', '--format', 'requirements-txt', '--output-file', requirements])
    for arch, uv_arch in ARCHES.items():
        print('Preparing bundled Python and wheels:', arch, flush=True)
        target = f'cpython-{PYTHON}-macos-{uv_arch}-none'
        run([uv, 'python', 'install', target, '--install-dir', CACHE / 'python', '--no-bin', '--system-certs'])
        run([uv, 'pip', 'install', '--target', CACHE / 'deps' / arch, '--python-platform', f'{uv_arch}-apple-darwin', '--python-version', '3.13', '--only-binary', ':all:', '--require-hashes', '--link-mode', 'copy', '-r', requirements, '--system-certs', '--quiet'])

    contents = app / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    (resources / 'app').mkdir()
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store')
    shutil.copytree(ROOT / 'src/yingsha_leads', resources / 'app/yingsha_leads', ignore=ignore)
    shutil.copy2(SOURCE / 'bootstrap.py', resources / 'bootstrap.py')
    for arch, uv_arch in ARCHES.items():
        shutil.copytree(CACHE / 'python' / f'cpython-{PYTHON}-macos-{uv_arch}-none', resources / 'runtime' / arch, symlinks=True, ignore=ignore)
        shutil.copytree(CACHE / 'deps' / arch, resources / 'deps' / arch, symlinks=True, ignore=ignore)
        # uv rewrites the dylib ID to its installation directory; restore a portable ID.
        run(['/usr/bin/install_name_tool', '-id', '@rpath/libpython3.13.dylib', resources / 'runtime' / arch / 'lib/libpython3.13.dylib'])
    shutil.copy2(requirements, resources / 'requirements.txt')
    shutil.copy2(SOURCE / '使用说明.txt', output / '使用说明.txt')
    shutil.copy2(SOURCE / '使用说明.txt', resources / '使用说明.txt')
    shutil.copy2(SOURCE / 'PYTHON-LICENSE.txt', resources / 'PYTHON-LICENSE.txt')
    (resources / 'THIRD-PARTY-NOTICES.txt').write_text(
        'Bundled CPython 3.13.9 from Astral python-build-standalone (PSF license).\n'
        'https://github.com/astral-sh/python-build-standalone\n'
        'Third-party package licenses are preserved in each deps/<arch>/*.dist-info directory.\n'
        'Python license: https://docs.python.org/3.13/license.html\n', encoding='utf-8')
    iconset = CACHE / 'Yingsha.iconset'
    iconset.mkdir(exist_ok=True)
    for size in [16, 32, 128, 256, 512]:
        icon_png(iconset / f'icon_{size}x{size}.png', size)
        icon_png(iconset / f'icon_{size}x{size}@2x.png', size * 2)
    run(['/usr/bin/iconutil', '-c', 'icns', iconset, '-o', resources / 'AppIcon.icns'])
    info = {
        'CFBundleName': '颖纱客户线索工具', 'CFBundleDisplayName': '颖纱客户线索工具',
        'CFBundleIdentifier': 'com.yingsha.leadfinder', 'CFBundleExecutable': 'YingshaLauncher',
        'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '0.2.0',
        'CFBundleVersion': '20261028.1', 'CFBundleIconFile': 'AppIcon',
        'LSMinimumSystemVersion': '14.0', 'LSUIElement': True,
        'NSHighResolutionCapable': True, 'LSMultipleInstancesProhibited': True,
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    }
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    run(['xcrun', 'clang', '-fobjc-arc', '-arch', 'arm64', '-arch', 'x86_64', '-mmacosx-version-min=14.0', '-framework', 'Cocoa', SOURCE / 'Launcher.m', '-o', contents / 'MacOS/YingshaLauncher'])
    binaries = []
    for path in app.rglob('*'):
        if path.is_symlink():
            if not path.resolve().is_relative_to(app): raise RuntimeError('External symlink: ' + str(path))
        elif path.is_file():
            with path.open('rb') as handle: magic = handle.read(4)
            if magic in MACH_MAGICS:
                relative = path.relative_to(app).as_posix()
                architectures = run(['/usr/bin/lipo', '-archs', path]).split()
                required = next((a for a in ARCHES if f'/{a}/' in '/' + relative), None)
                if required and required not in architectures: raise RuntimeError('Wrong architecture: ' + relative)
                if required is None and not all(a in architectures for a in ARCHES): raise RuntimeError('Launcher must be universal')
                linked = run(['/usr/bin/otool', '-L', path])
                for row in linked.splitlines()[1:]:
                    reference = row.strip().split(' (')[0]
                    if reference.startswith('/') and not reference.startswith(('/usr/lib/', '/System/Library/')):
                        raise RuntimeError('Nonportable dynamic library: ' + reference)
                binaries.append((path, architectures))
    print('Architecture and library audit:', len(binaries), 'Mach-O files', flush=True)
    manifest = {'python': PYTHON, 'architectures': list(ARCHES), 'created': datetime.datetime.now().isoformat(timespec='seconds'),
                'lock_sha256': hashlib.sha256((ROOT / 'uv.lock').read_bytes()).hexdigest(),
                'binaries': {str(p.relative_to(app)): a for p, a in binaries}, 'signing': 'ad-hoc; not Apple notarized'}
    (resources / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    # Sign inner libraries/executables first, then the containing app.
    for binary, _ in sorted(binaries, key=lambda pair: len(pair[0].parts), reverse=True):
        run(['/usr/bin/codesign', '--force', '--sign', '-', binary])
    run(['/usr/bin/codesign', '--force', '--sign', '-', app])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    print('Built:', app, flush=True)


if __name__ == '__main__': main()
