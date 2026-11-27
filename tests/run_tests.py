"""Run repository tests with explicit source paths for embedded Python."""
import argparse
import json
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system-only', action='store_true',
                        help='Run the launcher tests that require mitmproxy.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    for directory in (root / 'tests', root, root / 'src/runtime',
                      root / 'src/runtime/backup-tool'):
        sys.path.insert(0, str(directory))
    if args.system_only:
        names = ['test_launcher']
    else:
        names = [path.stem for path in sorted((root / 'tests').glob('test_*.py'))
                 if path.stem != 'test_launcher']
        names += ['test_exploration_integration', 'test_tcp_gap', 'test_scene_backup']
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromNames(names))
    print(json.dumps({'testsRun': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'passed': result.wasSuccessful()}, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
