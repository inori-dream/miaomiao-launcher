"""Repair PktMon PCAPNG files that label raw Wi-Fi or bare-IP frames as Ethernet.

Convert only validated 802.11 data + LLC/SNAP frames and bare IP packets on mixed
interfaces where pcap_compat cannot rewrite the interface linktype alone.
Original captures remain unchanged and packet options/timestamps are preserved.
"""
import os
from pathlib import Path
import struct
import tempfile
from pcap_compat import _is_bare_ip


def wifi_to_ethernet(packet):
    if len(packet) < 32:
        return None
    control = int.from_bytes(packet[:2], 'little')
    if control & 3 or (control >> 2) & 3 != 2:
        return None
    to_ds = bool(control & 0x100)
    from_ds = bool(control & 0x200)
    header = 24 + (6 if to_ds and from_ds else 0)
    qos = bool(control & 0x80)
    if qos:
        if len(packet) < header + 2 or packet[header] & 0x80:
            return None  # A-MSDU requires subframe decoding, never guess.
        header += 2
        if control & 0x8000:
            header += 4
    snap = packet[header:header + 8]
    if len(snap) != 8 or snap[:3] != b'\xaa\xaa\x03' or snap[3:6] not in (b'\0\0\0', b'\0\0\xf8'):
        return None
    if snap[6:8] not in (b'\x08\x00', b'\x86\xdd', b'\x08\x06'):
        return None
    if to_ds and from_ds:
        destination, source = packet[16:22], packet[24:30]
    elif to_ds:
        destination, source = packet[16:22], packet[10:16]
    elif from_ds:
        destination, source = packet[4:10], packet[16:22]
    else:
        destination, source = packet[4:10], packet[10:16]
    return destination + source + snap[6:8] + packet[header + 8:]


def normalize_pcapng(source):
    source = Path(source)
    if source.suffix.lower() != '.pcapng':
        return source, 0
    temporary = None
    converted = 0
    interfaces = []
    endian = '<'
    try:
        with source.open('rb') as incoming, tempfile.NamedTemporaryFile(dir=source.parent, suffix='.normalizing', delete=False) as outgoing:
            temporary = Path(outgoing.name)
            while True:
                header = incoming.read(8)
                if not header:
                    break
                if len(header) != 8:
                    raise ValueError('Truncated PCAPNG block header.')
                if header[:4] == b'\x0a\x0d\x0d\x0a':
                    magic = incoming.read(4)
                    if magic == b'\x4d\x3c\x2b\x1a':
                        endian = '<'
                    elif magic == b'\x1a\x2b\x3c\x4d':
                        endian = '>'
                    else:
                        raise ValueError('Invalid PCAPNG byte-order marker.')
                    size = struct.unpack(endian + 'I', header[4:])[0]
                    if size < 28 or size > 64 * 1024 * 1024 or size % 4:
                        raise ValueError('Invalid PCAPNG section size.')
                    block = header + magic + incoming.read(size - 12)
                    interfaces = []
                else:
                    kind, size = struct.unpack(endian + 'II', header)
                    if size < 12 or size > 64 * 1024 * 1024 or size % 4:
                        raise ValueError('Invalid PCAPNG block size.')
                    block = header + incoming.read(size - 8)
                if len(block) != size or struct.unpack_from(endian + 'I', block, size - 4)[0] != size:
                    raise ValueError('Truncated or inconsistent PCAPNG block.')
                kind = struct.unpack_from(endian + 'I', block)[0]
                if kind == 1:
                    if size < 20:
                        raise ValueError('Invalid PCAPNG interface block.')
                    interfaces.append(struct.unpack_from(endian + 'H', block, 8)[0])
                elif kind == 6:
                    if size < 32:
                        raise ValueError('Invalid PCAPNG packet block.')
                    interface, high, low, captured, original = struct.unpack_from(endian + 'IIIII', block, 8)
                    padded = (captured + 3) & ~3
                    if interface >= len(interfaces) or 28 + padded > size - 4:
                        raise ValueError('Invalid PCAPNG packet length/interface.')
                    packet = block[28:28 + captured]
                    normal = len(packet) >= 14 and packet[12:14] in (b'\x08\x00', b'\x86\xdd', b'\x08\x06', b'\x81\x00', b'\x88\xa8')
                    fixed = wifi_to_ethernet(packet) if interfaces[interface] == 1 and not normal else None
                    if fixed is None and interfaces[interface] == 1 and not normal and _is_bare_ip(packet):
                        fixed = b'\0' * 12 + (b'\x08\x00' if packet[0] >> 4 == 4 else b'\x86\xdd') + packet
                    if fixed is not None:
                        removed = len(packet) - len(fixed)
                        body = struct.pack(endian + 'IIIII', interface, high, low, len(fixed), max(len(fixed), original - removed))
                        body += fixed + b'\0' * ((-len(fixed)) % 4) + block[28 + padded:size - 4]
                        new_size = 12 + len(body)
                        block = struct.pack(endian + 'II', 6, new_size) + body + struct.pack(endian + 'I', new_size)
                        converted += 1
                outgoing.write(block)
        if not converted:
            temporary.unlink()
            temporary = None
            return source, 0
        destination = source.with_name(source.stem + '.normalized.pcapng')
        os.replace(temporary, destination)
        temporary = None
        return destination, converted
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
