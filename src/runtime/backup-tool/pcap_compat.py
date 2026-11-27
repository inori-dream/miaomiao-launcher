"""Stream PktMon captures into a corrected copy, never changing the original.

Only Ethernet-labelled interfaces consisting entirely of verified native Wi-Fi
and/or bare IP are eligible. Mixed eligible frames get separate interfaces with
all original IDB options retained. Packet buffers are not retained between reads.
"""
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile

LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_WIFI = 105
MAX_BLOCK_SIZE = 64 * 1024 * 1024


class InvalidCapture(ValueError):
    """Unsupported or malformed capture: keep the input unchanged."""


def _is_ipv4(packet: bytes) -> bool:
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return False
    header_length = (packet[0] & 0x0F) * 4
    if header_length < 20 or header_length > len(packet):
        return False
    total = int.from_bytes(packet[2:4], 'big')
    return header_length <= total <= len(packet)


def _is_ipv6(packet: bytes) -> bool:
    if len(packet) < 40 or packet[0] >> 4 != 6:
        return False
    payload = int.from_bytes(packet[4:6], 'big')
    return payload == 0 or 40 + payload <= len(packet)


def _is_bare_ip(packet: bytes) -> bool:
    return _is_ipv4(packet) or _is_ipv6(packet)


def _is_wifi_data(packet: bytes) -> bool:
    if len(packet) < 32:
        return False
    control = int.from_bytes(packet[:2], 'little')
    if control & 0x000F != 0x0008:
        return False
    offset = 24 + (6 if control & 0x0300 == 0x0300 else 0)
    if control & 0x0080:
        offset += 2 + (4 if control & 0x8000 else 0)
    return (packet[offset:offset + 6] == b'\xaa\xaa\x03\x00\x00\x00'
            and packet[offset + 6:offset + 8] in (b'\x08\x00', b'\x86\xdd', b'\x08\x06'))


def _classify_frame(packet: bytes) -> str:
    if _is_wifi_data(packet):
        return 'wifi'
    return 'raw' if _is_bare_ip(packet) else 'other'


def _blocks(path):
    """Yield one validated block at a time, including section-local endianness."""
    endian = None
    with Path(path).open('rb') as source:
        while True:
            position = source.tell()
            header = source.read(12)
            if not header:
                return
            if len(header) != 12:
                raise InvalidCapture('Truncated block header')
            if header[:4] == b'\x0a\x0d\x0d\x0a':
                endian = {b'\x4d\x3c\x2b\x1a': '<', b'\x1a\x2b\x3c\x4d': '>'}.get(header[8:12])
                if endian is None:
                    raise InvalidCapture('Invalid byte-order magic')
            elif endian is None:
                raise InvalidCapture('Not a PCAPNG capture')
            kind, size = struct.unpack(endian + 'II', header[:8])
            if size < 12 or size % 4 or size > MAX_BLOCK_SIZE:
                raise InvalidCapture('Invalid block size')
            block = header + source.read(size - 12)
            if len(block) != size or struct.unpack_from(endian + 'I', block, size - 4)[0] != size:
                raise InvalidCapture('Truncated block or invalid trailer')
            if kind == 0x0a0d0d0a and size < 28:
                raise InvalidCapture('Truncated section header')
            if kind == 1 and size < 20:
                raise InvalidCapture('Truncated interface description')
            if kind == 6:
                if size < 32:
                    raise InvalidCapture('Truncated enhanced packet block')
                captured, original = struct.unpack_from(endian + 'II', block, 20)
                if captured > original or ((captured + 3) & ~3) > size - 32:
                    raise InvalidCapture('Invalid packet length')
            elif kind in (2, 3):
                raise InvalidCapture('Unsupported obsolete/simple packet block')
            yield position, kind, endian, block


def _validate_options(block, start, endian):
    """Validate option bounds before cloning an IDB, retaining unknown options."""
    end = len(block) - 4
    while start < end:
        if end - start < 4:
            raise InvalidCapture('Truncated IDB option')
        code, length = struct.unpack_from(endian + 'HH', block, start)
        start += 4
        if code == 0:
            if length or any(block[start:end]):
                raise InvalidCapture('Invalid option terminator')
            return
        start += (length + 3) & ~3
        if start > end:
            raise InvalidCapture('IDB option length exceeds block')


def _scan(path):
    """First pass stores section/interface metadata only, never packet lists."""
    sections = []
    current = None
    def finish(end):
        if current is not None:
            declared = struct.unpack_from(current['endian'] + 'q', current['shb'], 16)[0]
            actual = end - current['start'] - len(current['shb'])
            if declared < -1 or (declared >= 0 and declared != actual):
                raise InvalidCapture('Incorrect section length')
    for position, kind, endian, block in _blocks(path):
        if kind == 0x0a0d0d0a:
            finish(position)
            current = {'start': position, 'endian': endian, 'shb': block, 'interfaces': []}
            sections.append(current)
        elif kind == 1:
            _validate_options(block, 16, endian)
            current['interfaces'].append({'block': block,
                'eligible': struct.unpack_from(endian + 'H', block, 8)[0] == LINKTYPE_ETHERNET,
                'counts': {'wifi': 0, 'raw': 0, 'other': 0}})
        elif kind == 6:
            iid, _, _, captured, _ = struct.unpack_from(endian + 'IIIII', block, 8)
            if iid >= len(current['interfaces']):
                raise InvalidCapture('Packet references an undefined interface')
            interface = current['interfaces'][iid]
            if interface['eligible']:
                interface['counts'][_classify_frame(block[28:28 + captured])] += 1
        elif kind == 5:
            if len(block) < 24 or struct.unpack_from(endian + 'I', block, 8)[0] >= len(current['interfaces']):
                raise InvalidCapture('Invalid interface statistics block')
    finish(Path(path).stat().st_size)
    return sections


def _with_linktype(block, endian, linktype):
    """Clone the complete IDB, including time resolution/offset and other options."""
    result = bytearray(block)
    struct.pack_into(endian + 'H', result, 8, linktype)
    return bytes(result)


def _plan(sections):
    used = set()
    for section in sections:
        interfaces = section['interfaces']
        primary, remap, extra = {}, {}, []
        for iid, interface in enumerate(interfaces):
            counts = interface['counts']
            if not interface['eligible'] or counts['other'] or not (counts['wifi'] or counts['raw']):
                continue
            first = 'wifi' if counts['wifi'] >= counts['raw'] else 'raw'
            second = 'raw' if first == 'wifi' else 'wifi'
            primary[iid] = LINKTYPE_WIFI if first == 'wifi' else LINKTYPE_RAW
            used.add(first)
            if counts[second]:
                remap[iid, second] = len(interfaces) + len(extra)
                linktype = LINKTYPE_WIFI if second == 'wifi' else LINKTYPE_RAW
                extra.append(_with_linktype(interface['block'], section['endian'], linktype))
                used.add(second)
        section.update(primary=primary, remap=remap, extra=extra)
    return used


def _write(path, target, sections):
    """Second pass emits cloned IDBs before packets; existing interface IDs stay fixed."""
    index = -1
    for _, kind, endian, block in _blocks(path):
        if kind == 0x0a0d0d0a:
            index += 1
            if index >= len(sections):
                raise InvalidCapture('Source sections changed between passes')
            section = sections[index]
            if section['primary']:
                if struct.unpack_from(endian + 'q', block, 16)[0] >= 0:
                    shb = bytearray(block)
                    old_length = struct.unpack_from(endian + 'q', shb, 16)[0]
                    struct.pack_into(endian + 'q', shb, 16, old_length + sum(map(len, section['extra'])))
                    block = bytes(shb)
                target.write(block)
                for iid, interface in enumerate(section['interfaces']):
                    idb = interface['block']
                    if iid in section['primary']:
                        idb = _with_linktype(idb, endian, section['primary'][iid])
                    target.write(idb)
                for idb in section['extra']:
                    target.write(idb)
                continue
        elif kind == 1 and section['primary']:
            continue
        elif kind == 6 and section['primary']:
            iid, _, _, captured, _ = struct.unpack_from(endian + 'IIIII', block, 8)
            if iid in section['primary']:
                cls = _classify_frame(block[28:28 + captured])
                new_iid = section['remap'].get((iid, cls), iid)
                clear = cls == 'wifi' and block[29] & 0x40
                if new_iid != iid or clear:
                    changed = bytearray(block)
                    struct.pack_into(endian + 'I', changed, 8, new_iid)
                    if clear:
                        changed[29] &= ~0x40
                    block = bytes(changed)
        target.write(block)


def prepare_capture(path: Path) -> Path:
    """Return a corrected atomic copy, or the original on any unsupported input."""
    path = Path(path)
    if path.stem.endswith('.link-fixed'):
        return path
    before = path.stat()
    signature = (before.st_size, before.st_mtime_ns)
    try:
        sections = _scan(path)
        used = _plan(sections)
    except InvalidCapture:
        return path
    if not used:
        return path
    corrected = path.with_name(path.stem + '.link-fixed.pcapng')
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as target:
            temporary = Path(target.name)
            _write(path, target, sections)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != signature:
            return path  # A live writer changed the source between the two passes.
        temporary.replace(corrected)
    except InvalidCapture:
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    kinds = []
    if 'wifi' in used:
        kinds.append('Wi-Fi 帧被标为以太网')
    if 'raw' in used:
        kinds.append('隧道裸 IP 包被标为以太网')
    print(f"[兼容] 检测到{'、'.join(kinds)}，已生成修正副本：{corrected}")
    return corrected
