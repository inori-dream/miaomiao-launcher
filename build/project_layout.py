"""Resolve repository sources while retaining the flat release bundle layout."""
import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / 'src/runtime'
_ROOT_DOC_LINK = re.compile(
    rb'(\]\(<?)(?!https?://|docs/|backup-tool/|src/|/)([A-Za-z0-9._-]+\.md(?:#[^)>\s]*)?>?\))'
)


def runtime_directory(root):
    root = Path(root).resolve()
    runtime = root / 'src/runtime'
    return runtime if runtime.is_dir() else root


def release_version(root=PROJECT_ROOT):
    return json.loads(source_file(root, 'release-build.json').read_text(encoding='utf-8'))['version']


def english_readme(version):
    return (
        b'# OverField International Launcher ' + version.encode('utf-8')
        + b'\n\nEnglish Steam edition with signed automatic launcher updates. '
        b'The updater checks the formal GitHub release, downloads only this edition\'s verified small core package '
        b'and switches while game/services/capture are idle. Saves, settings, game files and runtime dependencies '
        b'are protected. Local managed-file modifications pause updates rather than being overwritten. '
        b'Official capture archives map exploration progress and Bless Tree in the account ZIP, while overall map exploration % and Bless Tree cannot currently be imported into the default Lolo server.\n\n'
        b'See docs/auto-update.md and docs/collector-1012.md. Android is a separate unmodified third-party APK '
        b'and is never installed or updated automatically. Read docs/android-backup.md before deciding to use it.\n'
    )


def release_document(data, root_document=False):
    """Translate repository links to the corresponding flat bundle links."""
    translated = re.sub(br'(\]\(<?(?:\.\./)*)src/runtime/backup-tool/',
                        br'\1backup-tool/', data)
    if root_document:
        def rewrite(match):
            target = match.group(2).decode('ascii', errors='ignore').split('#', 1)[0].rstrip('>')
            if target in {'README.md', 'RELEASE-NOTES.md', 'RELEASE-CHECKLIST.md'}:
                return match.group(0)
            return match.group(1) + b'docs/' + match.group(2)

        translated = _ROOT_DOC_LINK.sub(rewrite, translated)
    return translated


def source_file(root, name):
    """Locate a logical release filename in a source checkout or flat bundle."""
    root = Path(root).resolve()
    if (root / 'src/runtime').is_dir():
        candidates = (
            root / 'src/runtime' / name,
            root / name,
            root / 'src/launcher' / name,
            root / 'src/sdk' / name,
            root / 'build' / name,
            root / 'tools' / name,
            root / 'docs' / name,
            root / 'dist/bin' / name,
            root / 'dist' / name,
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
    for candidate in (root / name, root / 'app' / name):
        if candidate.exists():
            return candidate
    return root / name
