"""Minimal ARM64 Linux QRTR helper, built from source at runtime (no vendor blobs)."""
import re
import struct
import tempfile
from pathlib import Path


def parse_diag_endpoint(raw):
    """Return the unique QRTR node/port for the modem DIAG command service."""
    matches = []
    for line in raw.decode('utf-8', 'replace').splitlines():
        if 'DIAG service (MODEM:CMD)' not in line:
            continue
        fields = line.split()
        if len(fields) >= 5 and fields[0] == '4097':
            matches.append((int(fields[3]), int(fields[4])))
    if len(matches) != 1 or not all(0 < value < 65536 for value in matches[0]):
        raise RuntimeError('Cannot uniquely discover modem DIAG command endpoint')
    return matches[0]


def discover_diag_endpoint(adb):
    command = ('rm -f /tmp/u60-qrtr-lookup; '
               'qrtr-lookup > /tmp/u60-qrtr-lookup 2>/dev/null & p=$!; '
               'sleep 2; kill $p 2>/dev/null; cat /tmp/u60-qrtr-lookup; '
               'rm -f /tmp/u60-qrtr-lookup')
    return parse_diag_endpoint(adb('shell', command))


def build_helper(packet, node=3, port=24):
    if not 0 < len(packet) <= 0x4C0:
        raise ValueError('Invalid request size')
    base, entry = 0x401000, 0x4000B0
    data = bytearray(0x1000)
    if not 0 < node < 65536 or not 0 < port < 65536:
        raise ValueError('Invalid QRTR endpoint')
    struct.pack_into('<HHII', data, 0xB00, 42, 0, node, port)
    data[0xB20:0xB20 + len(packet)] = packet
    struct.pack_into('<qq', data, 0xFE0, 3, 0)
    code = []
    def mov(r, value):
        code.append(0xD2800000 | (value << 5) | r)
    def reg(r, source):
        code.append(0xAA000000 | (source << 16) | (31 << 5) | r)
    def addr(r, target):
        delta = target - (entry + len(code) * 4)
        code.append(0x10000000 | ((delta & 3) << 29) | (((delta >> 2) & 0x7FFFF) << 5) | r)
    def syscall(number):
        mov(8, number)
        code.append(0xD4000001)
    def store_result(target):
        addr(1, target)
        code.append(0xF9000000 | (1 << 5))
    mov(0, 42); mov(1, 2); mov(2, 0); syscall(198)
    reg(19, 0)
    reg(0, 19); mov(1, 1); mov(2, 20); addr(3, base + 0xFE0); mov(4, 16); syscall(208)
    reg(0, 19); addr(1, base + 0xB20); mov(2, len(packet)); mov(3, 0)
    addr(4, base + 0xB00); mov(5, 12); syscall(206); store_result(base)
    reg(0, 19); addr(1, base + 32); mov(2, 0x900)
    mov(3, 0); mov(4, 0); mov(5, 0); syscall(207); store_result(base + 8)
    mov(0, 1); addr(1, base); mov(2, len(data)); syscall(64)
    mov(0, 0); syscall(93)
    blob = bytearray(0x2000)
    blob[:64] = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + bytes(9),
                           2, 183, 1, entry, 64, 0, 0, 64, 56, 2, 64, 0, 0)
    blob[64:120] = struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, 0x1000, 0x1000, 0x1000)
    blob[120:176] = struct.pack('<IIQQQQQQ', 1, 6, 0x1000, base, base, 0x1000, 0x1000, 0x1000)
    encoded = struct.pack('<' + 'I' * len(code), *code)
    blob[176:176 + len(encoded)] = encoded
    blob[0x1000:] = data
    return bytes(blob)


def parse_response(raw, packet):
    if len(raw) != 4096:
        raise RuntimeError('Incomplete diagnostic transport output')
    sent, count = struct.unpack_from('<qq', raw)
    if sent != len(packet) or count != 142:
        raise RuntimeError('Diagnostic transport rejected request or timed out')
    frame = raw[32:32 + count]
    if frame[:2] != b'\x02\x00' or frame[4:6] != b'\x7e\x01' or frame[-1] != 0x7E or struct.unpack_from('<H', frame, 6)[0] != 133:
        raise RuntimeError('Unsupported diagnostic framing')
    reply = frame[8:-1]
    if reply[:3] != packet[:3] or struct.unpack_from('<H', reply, 131)[0] != 0:
        raise RuntimeError('NV response mismatch or modem rejected operation')
    return reply[3:131]


def transact(adb, command, data=bytes(128)):
    if command not in (0x26, 0x27) or len(data) != 128:
        raise ValueError('Only NV 5280 read/write supported')
    packet = struct.pack('<BH', command, 5280) + data + bytes(2)
    node, port = discover_diag_endpoint(adb)
    # Unique directory avoids interfering with other diagnostic sessions.
    remote = adb('shell', 'mktemp -d /tmp/u60-nv.XXXXXX').decode().strip()
    if not re.fullmatch(r'/tmp/u60-nv\.[A-Za-z0-9]+', remote):
        raise RuntimeError('Cannot create temporary device directory')
    try:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'diag'
            executable.write_bytes(build_helper(packet, node=node, port=port))
            adb('push', str(executable), remote + '/diag')
        adb('shell', 'chmod 700 ' + remote + '/diag')
        return parse_response(adb('exec-out', remote + '/diag'), packet)
    finally:
        try:
            adb('shell', 'rm -f ' + remote + '/diag; rmdir ' + remote)
        except Exception:
            pass  # Connection loss must not hide the original error.
