"""Create and verify the distributable DMG from an already signed app."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(args):
    subprocess.run([str(value) for value in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    image = output / '颖纱客户线索工具-Mac-0.2.0.dmg'
    if image.exists():
        raise SystemExit('Refusing to replace an existing installer: ' + str(image))
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    readme = app / 'Contents/Resources/使用说明.txt'
    with tempfile.TemporaryDirectory(prefix='yingsha-dmg-stage-') as temporary:
        staging = Path(temporary)
        shutil.copytree(app, staging / app.name, symlinks=True)
        (staging / 'Applications').symlink_to('/Applications')
        shutil.copy2(readme, staging / '使用说明.txt')
        run(['/usr/bin/hdiutil', 'create', '-volname', '颖纱客户线索工具',
             '-srcfolder', staging, '-format', 'UDZO', image])
    run(['/usr/bin/hdiutil', 'verify', image])
    shutil.copy2(readme, output / '使用说明.txt')
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {image.name}\n', encoding='utf-8')
    print(f'Installer: {image}\nSize: {image.stat().st_size / 1024 / 1024:.1f} MiB\nSHA256: {digest}')


if __name__ == '__main__':
    main()
