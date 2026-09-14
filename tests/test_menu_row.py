import pathlib
import sys

# the host tools live in tools/, one level up from here
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'tools'))

import json
import pathlib
import struct
import tempfile
import unittest
from unittest.mock import patch

import menu_resources as res
import menu_row
import menu_text


class RowSpliceTests(unittest.TestCase):
    """The NBU fields are byte-packed and usually unaligned, so a splice must
    rewrite only its own four bytes however they straddle word boundaries."""

    def setUp(self):
        self.base = 0xC2091448
        self.memory = bytearray(b'\xaa\xbb\xcc\xdd' * 4)
        self.before = bytes(self.memory)
        self.drop = False
        self.writes = []
        transport = patch.object(menu_text, 'shl', self.shell)
        transport.start()
        self.addCleanup(transport.stop)

    def shell(self, command, operation, location, *values):
        address = int(location.split(',')[0], 0)
        offset = address - self.base
        if operation == 'get':
            size = int(location.split(',')[2])
            return '\n'.join(
                f'A:0x{address+i:x}, D:0x{struct.unpack_from("<I", self.memory, offset+i)[0]:08x}'
                for i in range(0, size, 4))
        self.writes.append(address)
        if not self.drop:
            struct.pack_into('<I', self.memory, offset, int(values[0], 0))
        return ''

    def test_unaligned_splice_changes_only_its_own_four_bytes(self):
        menu_row.splice(self.base + 3, 0x0000A4F8)
        self.assertEqual(self.memory[3:7], b'\x00\x00\xa4\xf8')
        self.assertEqual(self.memory[:3], self.before[:3])
        self.assertEqual(self.memory[7:], self.before[7:])

    def test_aligned_splice_writes_one_word(self):
        menu_row.splice(self.base, 0x0000A4F8)
        self.assertEqual(self.memory[:4], b'\x00\x00\xa4\xf8')
        self.assertEqual(self.memory[4:], self.before[4:])
        self.assertEqual(self.writes, [self.base])

    def test_dropped_write_is_reported_not_silently_accepted(self):
        self.drop = True
        with self.assertRaises(RuntimeError):
            menu_row.splice(self.base + 3, 0x0000A4F8)
        self.assertEqual(self.memory, self.before)


class RowFieldTests(unittest.TestCase):
    def setUp(self):
        self.image = res.IMAGE.read_bytes()

    def test_every_visible_shoot_row_resolves_label_and_target(self):
        rows = ['B5_1', 'B5_4', 'B5_5', 'B5_7_CINE', 'B5_8', 'B5_9']
        for row in rows:
            with self.subTest(row=row):
                fields = menu_row.row_fields(self.image, 'MainB5', row)
                self.assertEqual(set(fields), {'label', 'target'})

    def test_zebra_row_reports_both_keys_that_open_its_page(self):
        # Hardware: Right opened the retargeted page while OK still opened the
        # stock one, because the row carries two controlAppState records.
        targets = menu_row.row_fields(self.image, 'MainB5', 'B5_8')['target']
        self.assertEqual([res.LOAD + field for field, _, _ in targets],
                         [0xC2091448, 0xC2091523])
        self.assertEqual({name for _, _, name in targets}, {'B5_8'})

    def test_the_back_key_record_is_never_offered_for_retargeting(self):
        # The row also holds a Menu/back controlAppState with an empty target.
        # Retargeting it would remove the way out of the page.
        targets = menu_row.row_fields(self.image, 'MainB5', 'B5_8')['target']
        self.assertNotIn(0xC20915E0, [res.LOAD + field for field, _, _ in targets])
        self.assertTrue(all(name for _, _, name in targets))

    def test_zebra_label_matches_the_address_confirmed_on_hardware(self):
        label = menu_row.row_fields(self.image, 'MainB5', 'B5_8')['label']
        self.assertEqual([res.LOAD + field for field, _, _ in label], [0xC2091857])
        self.assertEqual(label[0][2], '0711')

    def test_pool_offset_refuses_a_name_that_is_not_in_the_pool(self):
        with self.assertRaises(ValueError):
            menu_row.pool_offset(self.image, 'NoSuchSceneName')

    def test_unknown_scene_and_row_are_rejected(self):
        with self.assertRaises(ValueError):
            menu_row.row_fields(self.image, 'NoSuchScene', 'B5_8')
        with self.assertRaises(ValueError):
            menu_row.row_fields(self.image, 'MainB5', 'B5_404')


class ItemSlotTests(unittest.TestCase):
    """The item table binds a menu row to the byte it edits."""

    def setUp(self):
        self.image = res.IMAGE.read_bytes()
        self.slots = menu_row.item_slots(self.image)

    def test_both_accessor_encodings_are_decoded(self):
        # movw form, and the add-immediate form that an earlier decoder missed.
        self.assertEqual(self.slots[0x9C], 0x524)
        self.assertEqual(self.slots[0x21], 0x28)

    def test_slots_stay_inside_the_settings_store(self):
        self.assertGreater(len(self.slots), 190)
        self.assertTrue(all(0 <= offset < 0x3000 for offset in self.slots.values()))

    def test_zebra_ids_match_the_values_read_from_the_camera(self):
        # 0x9D read exactly 90 live, which is what pins base and offsets together.
        self.assertEqual(menu_row.STORE + self.slots[0x9D], 0xC31B37E4)
        self.assertEqual(menu_row.STORE + self.slots[0x21], 0xC31B32E4)

class RowJournalTests(unittest.TestCase):
    def setUp(self):
        self.image = res.IMAGE.read_bytes()
        self.fields = menu_row.row_fields(self.image, 'MainB5', 'B5_8')
        self.spliced = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name) / 'journal.json'
        patch.object(menu_row, 'JOURNAL', self.path).start()
        patch.object(menu_row, 'splice',
                     lambda address, offset: self.spliced.append((address, offset))).start()
        self.addCleanup(patch.stopall)

    def run_cli(self, argv, live, saved):
        if saved is not None:
            self.path.write_text(json.dumps(saved))
        with patch.object(menu_row.sys, 'argv', ['menu_row.py', *argv]), \
                patch.object(menu_row, 'mem_read', return_value=live):
            code = menu_row.main()
        return code, json.loads(self.path.read_text()) if self.path.exists() else None

    def test_set_patches_every_site_and_journals_them_all(self):
        sites = self.fields['target']
        self.assertGreater(len(sites), 1)
        stock = struct.pack('>I', sites[0][1])
        code, written = self.run_cli(['set', 'MainB5', 'B5_8', '--target', 'B5_9',
                                      '--allow-duplicate-target'], stock, None)
        self.assertEqual(code, 0)
        entries = written['MainB5/B5_8']['target']
        self.assertEqual([e['field'] for e in entries], [field for field, _, _ in sites])
        self.assertEqual([address for address, _ in self.spliced],
                         [res.LOAD + field for field, _, _ in sites])

    def test_set_refuses_when_the_live_word_is_already_modified(self):
        with self.assertRaises(SystemExit):
            self.run_cli(['set', 'MainB5', 'B5_8', '--target', 'B5_9'], b'\x00\x00\xa4\xf8', {})
        self.assertEqual(self.spliced, [])

    def test_restore_without_a_journal_entry_writes_nothing(self):
        with self.assertRaises(SystemExit):
            self.run_cli(['restore', 'MainB5', 'B5_8'], b'\x00\x00\xa4\xf8', {})
        self.assertEqual(self.spliced, [])

    def test_restore_puts_back_every_journalled_site(self):
        sites = self.fields['target']
        saved = {'MainB5/B5_8': {'target': [{'field': field, 'stock': stock, 'stock_name': name}
                                            for field, stock, name in sites]}}
        code, written = self.run_cli(['restore', 'MainB5', 'B5_8'], b'\x00\x00\xa4\xf8', saved)
        self.assertEqual(code, 0)
        self.assertEqual(self.spliced,
                         [(res.LOAD + field, stock) for field, stock, _ in sites])
        self.assertEqual(written, {})

    def test_a_destination_another_row_already_opens_is_refused(self):
        # B5_9's own row opens B5_9; doing this broke Menu/back on hardware.
        with self.assertRaises(SystemExit) as refused:
            self.run_cli(['set', 'MainB5', 'B5_8', '--target', 'B5_9'], None, None)
        self.assertIn('B5_9', str(refused.exception))
        self.assertEqual(self.spliced, [])

    def test_the_override_flag_allows_it_deliberately(self):
        sites = self.fields['target']
        stock = struct.pack('>I', sites[0][1])
        code, _ = self.run_cli(['set', 'MainB5', 'B5_8', '--target', 'B5_9',
                                '--allow-duplicate-target'], stock, None)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.spliced), len(sites))


if __name__ == '__main__':
    unittest.main()
