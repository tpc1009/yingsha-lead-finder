"""Entry point run by bundled Python with -I -B (no external Python paths)."""
from pathlib import Path
import platform
import sys

resources = Path(__file__).resolve().parent
architecture = platform.machine()
if architecture not in {'arm64', 'x86_64'}:
    raise SystemExit('Unsupported Mac architecture: ' + architecture)
sys.path[:0] = [str(resources / 'app'), str(resources / 'deps' / architecture)]

from yingsha_leads.__main__ import main

main()
