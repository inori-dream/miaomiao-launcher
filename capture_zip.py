"""Bounded, non-extracting validation and delivery of account ZIP files."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import unicodedata
import uuid
import zipfile
import zlib

from privacy_check import reject_secrets, scan_text

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_MEMBER_UNCOMPRESSED = 128 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_COMPRESSION_RATIO = 200
COPY_CHUNK = 1024 * 1024
_RESPONSE_NAME = re.compile(r'_s2c_\d+_([A-Za-z0-9_]+)\.json$')
_RAW_CAPTURE_SUFFIXES = {'.etl', '.pcap', '.pcapng', '.cap'}
_WINDOWS_DEVICES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                    *(f'LPT{i}' for i in range(1, 10))}


def _unsafe(message: str) -> ValueError:
    # All messages are fixed text; member names and payloads are never echoed.
    return ValueError(message)


def uint32(value, *, allow_zero: bool = False, label: str = 'UID') -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _unsafe(f'{label} is invalid.')
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit():
            raise _unsafe(f'{label} is invalid.')
        value = int(value)
    if value < (0 if allow_zero else 1) or value > 0xFFFFFFFF:
        raise _unsafe(f'{label} is invalid.')
    return value


def captured_uid(player: dict) -> int:
    return uint32(player.get('player_label') or player.get('player_id') or 0,
                  allow_zero=True, label='Account UID')


def _duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _unsafe('Archive JSON contains duplicate fields.')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise _unsafe('Archive JSON contains a non-finite number.')


def _validate_json_shape(root) -> None:
    stack = [(root, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 200_000 or depth > 100:
            raise _unsafe('Archive JSON is too complex.')
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)


def load_json_bytes(data: bytes):
    try:
        value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=_duplicate_pairs,
                           parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise _unsafe('Archive contains invalid UTF-8 JSON.') from error
    _validate_json_shape(value)
    reject_secrets(value)
    return value


def validate_member(item: zipfile.ZipInfo) -> None:
    name = item.filename
    if not name or '\x00' in name or '\\' in name or ':' in name:
        raise _unsafe('Account ZIP contains an unsafe path.')
    windows = PureWindowsPath(name)
    posix = PurePosixPath(name)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise _unsafe('Account ZIP contains an unsafe path.')
    raw_parts = name.split('/')
    if raw_parts and raw_parts[-1] == '':
        raw_parts.pop()
    if not raw_parts or any(part in ('', '.', '..') for part in raw_parts):
        raise _unsafe('Account ZIP contains an unsafe path.')
    for part in raw_parts:
        if part.endswith((' ', '.')) or part.split('.', 1)[0].upper() in _WINDOWS_DEVICES:
            raise _unsafe('Account ZIP contains an unsafe Windows path.')
    mode = item.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind == stat.S_IFLNK:
        raise _unsafe('Account ZIP cannot contain symbolic links.')
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise _unsafe('Account ZIP contains a special file.')
    is_directory = name.endswith('/')
    if (kind == stat.S_IFDIR and not is_directory) or (is_directory and kind not in (0, stat.S_IFDIR)):
        raise _unsafe('Account ZIP contains an inconsistent file type.')
    if item.flag_bits & 1:
        raise _unsafe('Encrypted account ZIP files are not supported.')
    suffix = posix.suffix.lower()
    if suffix in _RAW_CAPTURE_SUFFIXES:
        raise _unsafe('Raw network captures cannot be imported or delivered.')
    if suffix == '.json' and ('_c2s_' in name or '/requests/' in name.lower()
                              or name.lower().startswith('requests/')):
        raise _unsafe('Client requests are not allowed in an account ZIP.')
    if is_directory:
        if item.file_size:
            raise _unsafe('Account ZIP contains an invalid directory entry.')
        return
    if item.file_size > MAX_MEMBER_UNCOMPRESSED:
        raise _unsafe('An account ZIP member is too large.')
    if item.file_size > MAX_COMPRESSION_RATIO * item.compress_size + COPY_CHUNK:
        raise _unsafe('Account ZIP compression ratio is unsafe.')


def _validate_raw_central_names(path: Path) -> None:
    """Reject names Python's ZipInfo normalizes before callers can inspect them."""
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 65_557))
        tail = stream.read()
        end = tail.rfind(b'PK\x05\x06')
        if end < 0:
            return  # ZipFile will report the malformed/empty archive.
        if end + 22 > len(tail):
            raise _unsafe('Account ZIP integrity validation failed.')
        eocd = tail[end:end + 22]
        count = int.from_bytes(eocd[10:12], 'little')
        central_size = int.from_bytes(eocd[12:16], 'little')
        central_offset = int.from_bytes(eocd[16:20], 'little')
        if count == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
            raise _unsafe('ZIP64 account archives are not supported.')
        if count > MAX_MEMBERS or central_offset + central_size > size:
            raise _unsafe('Account ZIP integrity validation failed.')
        stream.seek(central_offset)
        for _index in range(count):
            header = stream.read(46)
            if len(header) != 46 or not header.startswith(b'PK\x01\x02'):
                raise _unsafe('Account ZIP integrity validation failed.')
            name_length = int.from_bytes(header[28:30], 'little')
            extra_length = int.from_bytes(header[30:32], 'little')
            comment_length = int.from_bytes(header[32:34], 'little')
            name = stream.read(name_length)
            if len(name) != name_length:
                raise _unsafe('Account ZIP integrity validation failed.')
            # ZipInfo replaces os.sep with '/', including while reading a ZIP on
            # Windows.  Inspect central-directory bytes before that normalization.
            if any(byte in name for byte in (0, 0x5C, 0x3A)):
                raise _unsafe('Account ZIP contains an unsafe path.')
            stream.seek(extra_length + comment_length, os.SEEK_CUR)


def validate_archive(archive: zipfile.ZipFile, path: Path | None = None) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise _unsafe('Account ZIP contains too many members.')
    if path is not None:
        _validate_raw_central_names(path)
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise _unsafe('Account ZIP is too large.')
    names = set()
    total = 0
    for item in infos:
        validate_member(item)
        identity = unicodedata.normalize('NFKC', item.filename).casefold().rstrip('/')
        if identity in names:
            raise _unsafe('Account ZIP contains duplicate or ambiguous members.')
        names.add(identity)
        total += item.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise _unsafe('Account ZIP expands to too much data.')
    if sum(item.compress_size for item in infos) > MAX_ARCHIVE_BYTES:
        raise _unsafe('Account ZIP compressed data is too large.')


@contextmanager
def open_secure_zip(path):
    path = Path(path)
    if not path.is_file():
        raise _unsafe('No readable account ZIP was provided.')
    try:
        with zipfile.ZipFile(path) as archive:
            validate_archive(archive, path)
            yield archive
    except zipfile.BadZipFile as error:
        raise _unsafe('Account ZIP integrity validation failed.') from error


def read_member(archive: zipfile.ZipInfo | zipfile.ZipFile, item: zipfile.ZipInfo | None = None) -> bytes:
    """Read one member with a real byte limit, not just central-directory metadata."""
    info = item if item is not None else archive
    limit = min(MAX_MEMBER_UNCOMPRESSED, MAX_TOTAL_UNCOMPRESSED)
    try:
        with archive.open(info) as stream:
            data = stream.read(limit + 1)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
        raise _unsafe('Account ZIP member integrity validation failed.') from error
    if len(data) > limit:
        raise _unsafe('An account ZIP member expands beyond the safety limit.')
    if len(data) != info.file_size:
        raise _unsafe('Account ZIP member size does not match its directory entry.')
    if len(data) > MAX_COMPRESSION_RATIO * info.compress_size + COPY_CHUNK:
        raise _unsafe('Account ZIP compression ratio is unsafe.')
    return data


def _scan_binary(data: bytes) -> None:
    # Scan high-confidence credential shapes in protobuf/raw members too.  Keep
    # overlap so a marker split over a read boundary cannot evade the scan.
    overlap = b''
    for offset in range(0, len(data), COPY_CHUNK):
        chunk = overlap + data[offset:offset + COPY_CHUNK]
        if scan_text(chunk.decode('utf-8', errors='ignore')):
            raise _unsafe('Account ZIP contains sensitive credentials.')
        overlap = chunk[-512:]
    reject_secrets(data)


def validate_archive_contents(archive: zipfile.ZipFile) -> None:
    total = 0
    for item in archive.infolist():
        if item.is_dir():
            continue
        data = read_member(archive, item)
        total += len(data)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise _unsafe('Account ZIP expands to too much data.')
        if item.filename.lower().endswith('.json'):
            load_json_bytes(data)
        else:
            _scan_binary(data)


def find_capture_prefix(archive: zipfile.ZipFile) -> str:
    names = [item.filename for item in archive.infolist()]
    candidates = [prefix for prefix in ('', 'export/')
                  if any(name.startswith(prefix + 'json/') for name in names)]
    if len(candidates) != 1:
        raise _unsafe('Account ZIP must contain exactly one json/ data directory.')
    return candidates[0]


def response_items(archive: zipfile.ZipFile, prefix: str):
    for item in archive.infolist():
        if item.is_dir() or not item.filename.startswith(prefix + 'json/'):
            continue
        if '_s2c_' in item.filename and _RESPONSE_NAME.search(item.filename):
            yield item


def read_capture_entries(archive: zipfile.ZipFile, prefix: str) -> dict:
    entries = {}
    for item in response_items(archive, prefix):
        match = _RESPONSE_NAME.search(item.filename)
        value = load_json_bytes(read_member(archive, item))
        if not isinstance(value, dict):
            raise _unsafe('Account response JSON must contain an object.')
        entries.setdefault(match.group(1), []).append(value)
    return entries


def read_capture_summary(archive: zipfile.ZipFile, prefix: str) -> dict:
    candidates = [item for item in archive.infolist()
                  if not item.is_dir() and item.filename in
                  {prefix + 'summary.json', 'summary.json'}]
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise _unsafe('Account ZIP contains ambiguous summary metadata.')
    summary = load_json_bytes(read_member(archive, candidates[0]))
    if not isinstance(summary, dict):
        raise _unsafe('Account ZIP summary must contain an object.')
    return summary


def archive_sha256(path) -> str:
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(COPY_CHUNK), b''):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise _unsafe('Account ZIP is too large.')
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_archive(path, task_directory: Path) -> tuple[Path, str]:
    """Copy an untrusted ZIP into its task directory, then validate that copy."""
    path = Path(path)
    task_directory = Path(task_directory)
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise _unsafe('No readable account ZIP was provided.')
    destination = task_directory / f'source-{uuid.uuid4().hex}.zip'
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as source, destination.open('xb') as output:
        for chunk in iter(lambda: source.read(COPY_CHUNK), b''):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise _unsafe('Account ZIP is too large.')
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    with open_secure_zip(destination) as copied:
        validate_archive_contents(copied)
    if archive_sha256(destination) != digest.hexdigest():
        raise _unsafe('Account ZIP changed while it was being copied.')
    return destination, digest.hexdigest()


def inspect_account_zip(path):
    path = Path(path)
    if not path.is_file():
        raise _unsafe('No deliverable account ZIP was provided.')
    with open_secure_zip(path) as archive:
        validate_archive_contents(archive)
        from capture_import import read_capture_archive
        player, _ = read_capture_archive(archive, allow_missing_uid=False)
        uid = captured_uid(player)
        if uid <= 0:
            raise _unsafe('Account ZIP has no verifiable UID.')
        return {'uid': uid, 'name': player.get('player_name', ''), 'bytes': path.stat().st_size}


def file_hash(path):
    return archive_sha256(path)


def export_account_zip(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).absolute()
    if destination.suffix.lower() != '.zip':
        raise _unsafe('Delivery files must use the .zip extension.')
    if not destination.parent.is_dir():
        raise _unsafe('Choose an existing ZIP delivery directory.')
    same_destination = source == destination.resolve()
    task = destination.parent / '.account-export-tasks' / uuid.uuid4().hex
    task.mkdir(parents=True)
    try:
        copied, digest = snapshot_archive(source, task)
        metadata = inspect_account_zip(copied)
        if same_destination:
            return dict(metadata, path=str(destination), sha256=digest)
        os.replace(copied, destination)
        return dict(metadata, path=str(destination), sha256=digest)
    finally:
        shutil.rmtree(task, ignore_errors=True)
        try:
            task.parent.rmdir()
        except OSError:
            pass
