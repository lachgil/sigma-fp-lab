"""The double-press gate, run as ARM code in an emulator.

`test_arguments_reach_their_target_untouched` is the one that earns its place.
Two earlier attempts at intercepting keys corrupted an argument -- one used r2
to hold a forwarding address, one counted events in r0 -- and pressing OK put
the camera into record and froze it. Routing tests pass in both cases; only a
register-level check catches it.
"""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / 'reference/fpSup/fp_usb_shell'))

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE           # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,   # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_SP,
                               UC_ARM_REG_LR)
from armasm import assemble, symbols                                     # noqa: E402

SOURCE = pathlib.Path(__file__).resolve().parent.parent / 'src/keygate.S'
CODE, STATE = 0x44F79A40, 0x44F7AA40
CARD_HANDLE, STOCK = 0x44F99B80, 0xC0265800
RETURN, STACK = 0x100000, 0x10F000
OBSERVER = 0xC2F18618

RIGHT, UP, DOWN, OK, MENU, AEL = 0x0C, 0x14, 0x18, 0x1C, 0x0A, 0x24
REQUEST_SLOT, START_FN, STOP_FN = 0x44F7BA78, 0xC03722E8, 0xC0372330
CURSOR_WORD, OUR_ROW = 0x44F7CA00, 10
CLOCK = 0xC002B920
ST_LAST, ST_PENDING_AT = 0x34, 0x38
ST_OPEN, ST_PENDING = 0x00, 0x04


class KeyGateTests(unittest.TestCase):
    def setUp(self):
        defines = (f'STATE_ADDR={hex(STATE)}',
                   f'CARD_HANDLE_ADDR={hex(CARD_HANDLE)}')
        self.syms = symbols(SOURCE, defines)
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in ((0x44F00000, 0x200000), (0xC0000000, 0x800000),
                              (0x100000, 0x10000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(CODE, assemble(SOURCE, defines))
        self.seen = []
        self.now = 100000
        self.uc.hook_add(UC_HOOK_CODE, self._boundary)

    def _boundary(self, uc, address, size, _):
        if address == CLOCK:
            uc.reg_write(UC_ARM_REG_R0, self.now)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            return
        if address not in (CARD_HANDLE, STOCK):
            return
        self.seen.append(('card' if address == CARD_HANDLE else 'camera',
                          [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                                    UC_ARM_REG_R2, UC_ARM_REG_R3)]))
        uc.reg_write(UC_ARM_REG_R0, 1)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def word(self, address):
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def put(self, address, value):
        self.uc.mem_write(address, struct.pack('<I', value))

    def press(self, key):
        for register, value in ((UC_ARM_REG_R0, OBSERVER), (UC_ARM_REG_R1, key),
                                (UC_ARM_REG_R2, 0xAAAA5555),
                                (UC_ARM_REG_R3, 0x1234ABCD)):
            self.uc.reg_write(register, value)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN)
        self.uc.emu_start(CODE + self.syms['keygate'], RETURN, count=200000)
        self.assertEqual(self.uc.reg_read(UC_ARM_REG_PC), RETURN)
        self.assertEqual(self.uc.reg_read(UC_ARM_REG_SP), STACK)

    def route(self, key):
        self.seen.clear()
        self.press(key)
        return [who for who, _ in self.seen]

    def test_arguments_reach_their_target_untouched(self):
        for opened in (False, True):
            self.put(STATE + ST_OPEN, 1 if opened else 0)
            for key in (RIGHT, UP, DOWN, OK, MENU, AEL, RIGHT + 1, UP + 1):
                with self.subTest(open=opened, key=hex(key)):
                    self.seen.clear()
                    self.press(key)
                    for _, args in self.seen:
                        self.assertEqual(args[0], OBSERVER)
                        self.assertEqual(args[1], key)
                        self.assertEqual(args[2], 0xAAAA5555)
                        self.assertEqual(args[3], 0x1234ABCD)

    def test_a_single_right_still_reaches_the_camera(self):
        self.assertEqual(self.route(RIGHT), ['camera'])
        self.assertEqual(self.word(STATE + ST_OPEN), 0)

    def test_two_quick_rights_open_the_menu(self):
        self.route(RIGHT)
        self.now += 200
        self.assertEqual(self.route(RIGHT), [], 'the second press is ours')
        self.assertEqual(self.word(STATE + ST_OPEN), 1)

    def test_a_press_and_its_release_do_not_use_up_the_window(self):
        """Two events per press: counting events closed the window too early."""
        self.route(RIGHT)
        self.now += 50
        self.route(RIGHT + 1)
        self.now += 100
        self.assertEqual(self.route(RIGHT), [])
        self.assertEqual(self.word(STATE + ST_OPEN), 1)

    def test_slow_rights_just_pan_the_zoom(self):
        self.route(RIGHT)
        self.now += 2000
        self.assertEqual(self.route(RIGHT), ['camera'])
        self.assertEqual(self.word(STATE + ST_OPEN), 0)

    def test_another_key_cancels_a_pending_press(self):
        self.route(RIGHT)
        self.now += 50
        self.route(OK)
        self.now += 50
        self.assertEqual(self.route(RIGHT), ['camera'])
        self.assertEqual(self.word(STATE + ST_OPEN), 0)

    def test_it_closes_itself_after_the_idle_time(self):
        self.route(RIGHT); self.now += 200; self.route(RIGHT)
        self.assertEqual(self.word(STATE + ST_OPEN), 1)
        self.now += 6000
        self.assertEqual(self.route(OK), ['camera'], 'the key is the camera\'s')
        self.assertEqual(self.word(STATE + ST_OPEN), 0)

    def test_using_it_keeps_it_open(self):
        self.route(RIGHT); self.now += 200; self.route(RIGHT)
        for _ in range(5):
            self.now += 4000
            self.route(RIGHT)
        self.assertEqual(self.word(STATE + ST_OPEN), 1)

    def test_closed_menu_leaves_up_to_the_camera(self):
        self.assertEqual(self.route(UP), ['camera'])

    def test_open_menu_drives_the_card(self):
        self.put(STATE + ST_OPEN, 1)
        self.uc.mem_write(STATE + ST_LAST, struct.pack('<I', self.now))
        self.assertEqual(self.route(RIGHT), ['card'])
        self.assertEqual(self.route(UP), ['card'])

    def test_open_menu_leaves_the_camera_keys_alone(self):
        self.put(STATE + ST_OPEN, 1)
        self.uc.mem_write(STATE + ST_LAST, struct.pack('<I', self.now))
        self.assertEqual(self.route(OK), ['camera'])

    def test_menu_and_ael_no_longer_close_it(self):
        for key in (MENU, AEL):
            with self.subTest(key=hex(key)):
                self.put(STATE + ST_OPEN, 1)
                self.uc.mem_write(STATE + ST_LAST, struct.pack('<I', self.now))
                self.route(key)
                self.assertEqual(self.word(STATE + ST_OPEN), 1)

    def test_the_renderer_runs_on_every_key_and_keeps_the_arguments(self):
        """Repainting happens here now; a clobbered argument froze the camera."""
        renderer = 0xC0300000
        self.uc.mem_write(STATE + 0x28, struct.pack('<I', renderer))
        calls = []

        def watch(uc, address, size, _):
            if address == renderer:
                calls.append([uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                                       UC_ARM_REG_R2, UC_ARM_REG_R3)])
                uc.reg_write(UC_ARM_REG_R0, 0x99999999)   # a renderer may clobber
                uc.reg_write(UC_ARM_REG_R1, 0x88888888)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        self.uc.hook_add(UC_HOOK_CODE, watch)
        self.seen.clear()
        self.press(OK)
        self.assertEqual(len(calls), 1)
        self.assertEqual([who for who, _ in self.seen], ['camera'])
        _, args = self.seen[0]
        self.assertEqual(args, [OBSERVER, OK, 0xAAAA5555, 0x1234ABCD])

    def wire_false_colour(self, cursor):
        for offset, value in ((0x18, REQUEST_SLOT), (0x1C, START_FN),
                              (0x20, STOP_FN), (0x2C, CURSOR_WORD),
                              (0x30, OUR_ROW)):
            self.uc.mem_write(STATE + offset, struct.pack('<I', value))
        self.uc.mem_write(CURSOR_WORD, struct.pack('<I', cursor))
        self.put(STATE + ST_OPEN, 1)
        self.uc.mem_write(STATE + ST_LAST, struct.pack('<I', self.now))

    def test_up_on_our_row_latches_false_colour(self):
        """It toggles like any other row: scroll to it, press UP."""
        self.wire_false_colour(cursor=OUR_ROW)
        self.assertEqual(self.route(UP), [], 'the card must not see this one')
        self.assertEqual(self.word(REQUEST_SLOT), START_FN)
        self.uc.mem_write(REQUEST_SLOT, struct.pack('<I', 0))
        self.route(UP)
        self.assertEqual(self.word(REQUEST_SLOT), STOP_FN)

    def test_up_on_a_card_row_still_toggles_the_card(self):
        self.wire_false_colour(cursor=3)
        self.assertEqual(self.route(UP), ['card'])
        self.assertEqual(self.word(REQUEST_SLOT), 0)

    def test_old_down_key_is_no_longer_taken(self):
        self.wire_false_colour(cursor=OUR_ROW)
        self.assertEqual(self.route(DOWN), ['camera'])

    def test_down_latches_false_colour_while_open(self):
        """Superseded: kept to prove DOWN is no longer special-cased."""
        self.wire_false_colour(cursor=OUR_ROW)
        self.assertEqual(self.route(DOWN), ['camera'])

    def test_down_reaches_the_camera_while_closed(self):
        self.assertEqual(self.route(DOWN), ['camera'])

    def test_up_falls_through_when_nothing_is_wired(self):
        self.put(STATE + ST_OPEN, 1)
        self.uc.mem_write(STATE + ST_LAST, struct.pack('<I', self.now))
        self.assertEqual(self.route(UP), ['card'])

    def test_ok_always_reaches_the_camera(self):
        """The press that froze the camera three times, both states."""
        self.assertEqual(self.route(OK), ['camera'])
        self.put(STATE + ST_OPEN, 1)
        self.assertEqual(self.route(OK), ['camera'])


if __name__ == '__main__':
    unittest.main()
