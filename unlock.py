#!/usr/bin/env python3
"""Back up, unlock and verify U60 Pro NV 5280 without changing other bits."""
import argparse
import datetime
import getpass
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import urllib.request
from diag import transact


def enable_debug(host):
    ipaddress.IPv4Address(host)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(session, obj, method, params):
        request = urllib.request.Request('http://' + host + '/ubus/',
            data=json.dumps([{'jsonrpc': '2.0', 'id': 1, 'method': 'call',
                              'params': [session, obj, method, params]}]).encode(),
            headers={'Content-Type': 'application/json', 'Referer': 'http://' + host + '/',
                     'Origin': 'http://' + host, 'Z-Mode': '0', 'Z-Tag': method})
        with opener.open(request, timeout=20) as response:
            result = json.load(response)[0]['result']
        if result[0] != 0:
            raise RuntimeError('Router rejected ' + method)
        return result[1] if len(result) > 1 else {}
    anonymous = '0' * 32
    info = call(anonymous, 'zwrt_web', 'web_login_info', {})
    salt = info.get('zte_web_sault') or info.get('zte_web_salt')
    if not salt:
        raise RuntimeError('Unsupported router login protocol')
    password = getpass.getpass('Router web password (not saved): ')
    digest = lambda s: hashlib.sha256(s.encode()).hexdigest().upper()
    login = call(anonymous, 'zwrt_web', 'web_login', {'password': digest(digest(password) + salt)})
    session = login.get('ubus_rpc_session')
    if not session or session == anonymous or str(login.get('result')) not in ('0', 'success'):
        raise RuntimeError('Login failed; no debug request sent')
    call(session, 'zwrt_bsp.usb', 'set', {'mode': 'debug'})
    print('Debug mode requested. Reconnect USB if necessary, then run inspect.')


def soc_family(raw):
    """Same device reports 'SDXPINN' or 'SDXPINN-0-(null)' depending on boot; keep the family."""
    return raw.strip().split(b'-', 1)[0]


def cleared(data):
    if len(data) != 128:
        raise ValueError('Expected 128-byte NV value')
    return bytes([data[0] & 0xFE]) + data[1:]


def validate_backup(saved, device):
    data = bytes.fromhex(saved['data_hex'])
    if saved.get('schema') != 1 or saved.get('item') != 5280 or len(data) != 128:
        raise ValueError('Invalid backup format')
    if saved.get('device') != device:
        raise ValueError('Backup belongs to a different device or firmware')
    if saved.get('sha256') != hashlib.sha256(data).hexdigest():
        raise ValueError('Backup integrity check failed')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default='adb', help='Path to Android platform-tools adb')
    parser.add_argument('--serial', help='Select one ADB device; never saved in plaintext')
    parser.add_argument('--router', default='192.168.0.1')
    parser.add_argument('--backup-dir', type=Path, default=Path('private-backups'))
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('enable-adb')
    sub.add_parser('inspect')
    sub.add_parser('unlock')
    restore = sub.add_parser('restore'); restore.add_argument('backup', type=Path)
    verify = sub.add_parser('verify'); verify.add_argument('backup', type=Path)
    args = parser.parse_args()
    if args.action == 'enable-adb':
        enable_debug(args.router)
        return
    prefix = [args.adb] + (['-s', args.serial] if args.serial else [])
    def adb(*arguments):
        result = subprocess.run(prefix + list(arguments), capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError('ADB operation failed; check USB connection and device selection')
        return result.stdout
    serial = adb('get-serialno').strip()
    if not serial or serial == b'unknown':
        raise RuntimeError('Select exactly one available ADB device')
    if adb('shell', 'uname -m').strip() != b'aarch64' or adb('shell', 'id -u').strip() != b'0':
        raise RuntimeError('Requires ARM64 Linux router with root ADB')
    model = adb('shell', 'cat /proc/device-tree/model 2>/dev/null || true').replace(b'\0', b'').strip()
    sku = soc_family(adb('shell', 'cat /sys/devices/soc0/sku 2>/dev/null || true'))
    if sku != b'SDXPINN':
        raise RuntimeError('Unsupported SoC SKU; only the tested SDXPINN profile is supported')
    firmware = adb('shell', 'cat /etc/openwrt_release 2>/dev/null || true').strip()
    device = hashlib.sha256(serial + b'\0' + model + b'\0' + sku + b'\0' + firmware).hexdigest()
    boot = adb('shell', 'cat /proc/sys/kernel/random/boot_id').decode().strip()
    current = transact(adb, 0x26)
    print('Profile: ARM64 / SDXPINN / root ADB; NV reply validated')
    print('Voice disable bit:', current[0] & 1)
    if args.action == 'inspect':
        return
    saved = None
    if args.action in ('restore', 'verify'):
        saved = json.loads(args.backup.read_text(encoding='utf-8'))
        original = validate_backup(saved, device)
        if args.action == 'verify':
            if current != cleared(original):
                raise RuntimeError('NV does not match the expected unlocked value')
            if saved.get('boot_id') == boot:
                raise RuntimeError('Value matches, but reboot not observed. Reboot manually and verify again.')
            print('PASS: all 128 bytes match expected unlock after a different boot.')
            return
        target = original
    else:
        # Fail closed on unknown layouts. This is the observed profile, not a universal modem rule.
        if current[:8] not in (bytes.fromhex('0102000000000000'), bytes.fromhex('0002000000000000')):
            raise RuntimeError('Unknown NV prefix; refusing write. No force option is provided.')
        target = cleared(current)
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    backup = args.backup_dir / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    record = {'schema': 1, 'item': 5280, 'device': device, 'boot_id': boot,
              'data_hex': current.hex(), 'sha256': hashlib.sha256(current).hexdigest()}
    with backup.open('x', encoding='utf-8') as output:
        json.dump(record, output, indent=2)
        output.flush()
        import os
        os.fsync(output.fileno())
    print('Private backup:', backup)
    if current != target:
        transact(adb, 0x27, target)
        if transact(adb, 0x26) != target:
            raise RuntimeError('Write readback mismatch; retain backup and stop further writes')
    print('Exact readback confirmed. No reboot or call was initiated.')
    if args.action == 'unlock':
        print('Reboot manually, reconnect ADB, then: python unlock.py verify ' + str(backup))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        # Do not dump authentication responses, subprocess output, or device identifiers.
        print('Stopped:', str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__)
        raise SystemExit(1)
