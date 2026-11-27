"""Make the runtime sources importable when running unittest as a package."""
from pathlib import Path
import sys


_project = Path(__file__).resolve().parent.parent
for _directory in (_project / 'src/runtime', _project / 'src/runtime/backup-tool'):
    sys.path.insert(0, str(_directory))
