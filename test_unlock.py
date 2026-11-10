import hashlib
import struct
import unittest
from diag import build_helper, parse_response
from unlock import cleared, soc_family, validate_backup


class UnlockTests(unittest.TestCase):
    def test_soc_family_variants_match(self):
        for raw in (b'SDXPINN', b'SDXPINN\n', b'SDXPINN-0-(null)\n'):
            self.assertEqual(soc_family(raw), b'SDXPINN')
        self.assertEqual(soc_family(b'SDX75-1'), b'SDX75')
        self.assertEqual(soc_family(b''), b'')

    def test_only_voice_bit_changes(self):
        for first in range(256):
            before = bytes([first]) + bytes(range(127))
            after = cleared(before)
            self.assertEqual(after[1:], before[1:])
            self.assertEqual(before[0] ^ after[0], first & 1)
            self.assertEqual(cleared(after), after)

    def test_backup_device_and_integrity(self):
        value = bytes(range(128))
        backup = dict(schema=1, item=5280, device='test-device', data_hex=value.hex(),
                      sha256=hashlib.sha256(value).hexdigest())
        self.assertEqual(validate_backup(backup, 'test-device'), value)
        with self.assertRaises(ValueError):
            validate_backup(backup, 'different-device')
        backup['data_hex'] = bytes(128).hex()
        with self.assertRaises(ValueError):
            validate_backup(backup, 'test-device')

    def test_transport_rejects_corruption(self):
        packet = struct.pack('<BH', 0x26, 5280) + bytes(130)
        frame = b'\x02\x00\x00\x00\x7e\x01' + struct.pack('<H', 133) + packet + b'\x7e'
        raw = bytearray(4096)
        struct.pack_into('<qq', raw, 0, len(packet), len(frame))
        raw[32:32 + len(frame)] = frame
        self.assertEqual(parse_response(raw, packet), bytes(128))
        for position in (32, 36, 38, 40, 41, 171, 173):
            corrupted = bytearray(raw)
            corrupted[position] ^= 1
            with self.assertRaises(RuntimeError):
                parse_response(corrupted, packet)
        with self.assertRaises(RuntimeError):
            parse_response(raw[:-1], packet)
        struct.pack_into('<q', raw, 8, -11)
        with self.assertRaises(RuntimeError):
            parse_response(raw, packet)

    def test_generated_elf(self):
        packet = struct.pack('<BH', 0x26, 5280) + bytes(130)
        elf = build_helper(packet)
        self.assertEqual(len(elf), 8192)
        self.assertEqual(elf[:4], b'\x7fELF')
        self.assertEqual(struct.unpack_from('<H', elf, 18)[0], 183)
        self.assertEqual(elf[0x1B20:0x1B20 + len(packet)], packet)
        with self.assertRaises(ValueError):
            build_helper(bytes(1217))


if __name__ == '__main__':
    unittest.main()
