#!/usr/bin/env python3
"""Extract only the fingerprinted fp 5.02 analysis image. Never deploy or repack.

The output length follows fpSup's opengate reference fingerprint, not a fully
reverse-engineered segment boundary. This is not a general DFI unpacker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

INPUT_SHA256 = 'c9fa76f32f523bb2dc9a3fbda39418c31573cab6a1aa5c56d28c371862066ed8'
MAIN_SHA256 = '92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'
MAIN_BYTES = 49_482_954
STOCK = {
    0xC0B59A28: 0x40888,
    0xC0BE5888: 0x6A, 0xC0BE5A28: 0x6A, 0xC0BE5BC8: 0x6A,
    0xC0BD9A34: 0x640, 0xC0BE1684: 0x640,
    0xC0BD9EFC: 0x640, 0xC0BE1B4C: 0x640,
    0xC043A19C: 0xE1A00004,
}


def decode_reference(source):
    ring = bytearray(4096)
    cursor = 4078
    output = bytearray()
    offset = 0
    while offset < len(source):
        flags = source[offset]
        offset += 1
        for bit in range(8):
            literal = flags & (1 << bit)
            needed = 1 if literal else 2
            if offset + needed > len(source):
                raise ValueError('Compressed stream ended before reference length')
            if literal:
                value = source[offset]
                offset += 1
                output.append(value)
                ring[cursor] = value
                cursor = (cursor + 1) & 4095
            else:
                low, high = source[offset:offset + 2]
                offset += 2
                position = low | ((high & 0xF0) << 4)
                for index in range((high & 15) + 3):
                    value = ring[(position + index) & 4095]
                    output.append(value)
                    ring[cursor] = value
                    cursor = (cursor + 1) & 4095
                    if len(output) == MAIN_BYTES:
                        return output
            if len(output) == MAIN_BYTES:
                return output
    raise ValueError('Reference output length not reached')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    firmware = args.firmware.read_bytes()
    if hashlib.sha256(firmware).hexdigest() != INPUT_SHA256:
        raise SystemExit('Refusing unsupported input: exact fp 5.02 fingerprint required')
    section_sum = sum(memoryview(firmware)[0x100:0x175E500]) & 0xFFFFFFFF
    if section_sum != 2768501654:
        raise SystemExit('Primary section checksum mismatch')
    image = decode_reference(memoryview(firmware)[0x300:0x175E500])
    if hashlib.sha256(image).hexdigest() != MAIN_SHA256:
        raise SystemExit('Decoded image does not match fpSup reference fingerprint')
    for address, expected in STOCK.items():
        if struct.unpack_from('<I', image, address - 0xC0000000)[0] != expected:
            raise SystemExit(f'Stock word mismatch at {address:#x}')
    if any(memoryview(image)[0x72F800:0x72FA10]):
        raise SystemExit('Open-gate code cave is not zero-filled')
    if args.out.resolve() == args.firmware.resolve():
        raise SystemExit('Output must not replace input firmware')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Existing analysis images may be verified but never silently overwritten.
    if args.out.exists():
        if args.out.read_bytes() != image:
            raise SystemExit('Refusing to overwrite a different existing output')
    else:
        with args.out.open('xb') as stream:
            stream.write(image)
    print(json.dumps({'output': str(args.out), 'bytes': len(image),
                      'sha256': MAIN_SHA256, 'stock_words_verified': len(STOCK),
                      'code_cave_zero': True, 'camera_access': False}, indent=2))


if __name__ == '__main__':
    main()
