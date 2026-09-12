import contextlib
import io
import struct
import unittest
from unittest.mock import patch

import menu_text as menu


class MenuTextSafetyTests(unittest.TestCase):
    def setUp(self):
        self.base = 0xC0DA0000
        self.addr = self.base + 1
        self.memory = bytearray(b'!Test\x00\x00\x00\x00\x08\x00\x00KEEP')
        self.before = bytes(self.memory)
        self.drop = False
        self.writes = []
        transport = patch.object(menu, 'shl', self.shell)
        transport.start()
        self.addCleanup(transport.stop)

    def shell(self, command, operation, location, *values):
        address = int(location.split(',')[0], 0)
        offset = address - self.base
        if operation == 'get':
            size = int(location.split(',')[2])
            return '\n'.join(f'A:0x{address+i:x}, D:0x{struct.unpack_from("<I", self.memory, offset+i)[0]:08x}'
                             for i in range(0, size, 4))
        self.writes.append(address)
        if not self.drop:
            struct.pack_into('<I', self.memory, offset, int(values[0], 0))
        return ''

    def test_equal_length_replacement_and_restore_preserve_neighbors(self):
        menu.mem_write_string(self.addr, 'Demo', 5)
        self.assertEqual(self.memory, b'!Demo\x00\x00\x00\x00\x08\x00\x00KEEP')
        menu.mem_write_string(self.addr, 'Test', 5)
        self.assertEqual(self.memory, self.before)
        self.assertEqual(menu.mem_read(self.addr, 5), b'Test\x00')

    def test_following_zero_fields_do_not_expand_label_capacity(self):
        image = bytes(self.addr - menu.LOAD) + b'Test\x00\x00\x00\x00\x08'
        with patch.object(menu, 'image', return_value=image):
            address, capacity = menu.find('Test')[0]
        with self.assertRaises(ValueError):
            menu.mem_write_string(address, 'TooLong', capacity)
        self.assertEqual(self.memory, self.before)
        self.assertEqual(self.writes, [])

    def test_dropped_write_stops_before_touching_later_words(self):
        self.drop = True
        with self.assertRaises(RuntimeError):
            menu.mem_write_string(self.addr, 'Demo', 5)
        self.assertEqual(self.memory, self.before)
        self.assertEqual(self.writes, [self.base])

    def test_missing_or_wrong_address_reply_refuses_write(self):
        for reply in ('', f'A:0x{self.base+4:x}, D:0x00000000'):
            with self.subTest(reply=reply), patch.object(menu, 'shl', return_value=reply):
                with self.assertRaises(RuntimeError):
                    menu.mem_write_string(self.addr, 'Demo', 5)
        self.assertEqual(self.memory, self.before)

    def test_conflicting_second_copy_prevents_all_cli_writes(self):
        self.memory = bytearray(b'!Test\x00!!?Else\x00!!!')
        before = bytes(self.memory)
        with patch.object(menu, 'find', return_value=[(self.addr, 5), (self.base+9, 5)]), \
                patch.object(menu.sys, 'argv', ['menu_text.py', 'set', 'Test', 'Demo']), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                menu.main()
        self.assertEqual(self.memory, before)
        self.assertEqual(self.writes, [])


if __name__ == '__main__':
    unittest.main()
