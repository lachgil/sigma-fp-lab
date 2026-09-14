"""The key hook, run as ARM code in an emulator.

The test that matters is `test_arguments_reach_the_camera_untouched`. An earlier
version of this hook used r2 to hold the address it was forwarding to, which
handed the camera's own key handler a corrupted third argument: pressing OK put
the camera into record and froze it -- record light on, no UI, no buttons, no
USB. Nothing in the routing logic was wrong, so only a register-level check
catches it.
"""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'tools'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / 'reference/fpSup/fp_usb_shell'))

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE           # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,   # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_SP,
                               UC_ARM_REG_LR)
from armasm import assemble, symbols                                     # noqa: E402

SOURCE = pathlib.Path(__file__).resolve().parent.parent / 'src/keyhook.S'
CODE, STATE = 0x44F79A40, 0x44F7AA40
CARD, STOCK = 0xC0700000, 0xC0265800
RETURN, STACK = 0x100000, 0x10F000
CONTROL = 0xC2F18618

MENU, RIGHT, REPEAT, UP, DOWN, OK, QS, AEL = (0x0A, 0x0C, 0x0F, 0x14,
                                              0x18, 0x1C, 0x22, 0x24)
ST_OPEN = 0x0C


class KeyHookTests(unittest.TestCase):
    def setUp(self):
        defines = (f'STATE_ADDR={hex(STATE)}',)
        self.syms = symbols(SOURCE, defines)
        blob = assemble(SOURCE, defines)
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in ((0x44F00000, 0x200000), (0xC0200000, 0x600000),
                              (0x100000, 0x10000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(CODE, blob)
        self.put(STATE + 0x04, CARD)
        self.put(STATE + 0x08, STOCK)
        self.seen = []
        self.uc.hook_add(UC_HOOK_CODE, self._boundary)

    def _boundary(self, uc, address, size, _):
        if address not in (CARD, STOCK):
            return
        self.seen.append(('card' if address == CARD else 'stock',
                          [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                                    UC_ARM_REG_R2, UC_ARM_REG_R3)]))
        uc.reg_write(UC_ARM_REG_R0, 1)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def put(self, address, value):
        self.uc.mem_write(address, struct.pack('<I', value))

    def word(self, address):
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def press(self, key, r2=0xAAAA5555, r3=0x1234ABCD):
        for register, value in ((UC_ARM_REG_R0, CONTROL), (UC_ARM_REG_R1, key),
                                (UC_ARM_REG_R2, r2), (UC_ARM_REG_R3, r3)):
            self.uc.reg_write(register, value)
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN)
        self.uc.emu_start(CODE + self.syms['keyhook'], RETURN, count=200000)
        self.assertEqual(self.uc.reg_read(UC_ARM_REG_PC), RETURN)
        self.assertEqual(self.uc.reg_read(UC_ARM_REG_SP), STACK)

    def test_arguments_reach_the_camera_untouched(self):
        """Every argument, not just the two we look at, is passed straight on."""
        for key in (RIGHT, UP, OK, DOWN, MENU, QS, AEL, REPEAT):
            with self.subTest(key=hex(key)):
                self.seen.clear()
                self.press(key, r2=0xAAAA5555, r3=0x1234ABCD)
                for _, args in self.seen:
                    self.assertEqual(args[0], CONTROL)
                    self.assertEqual(args[1], key)
                    self.assertEqual(args[2], 0xAAAA5555)
                    self.assertEqual(args[3], 0x1234ABCD)

    def test_closed_menu_leaves_right_and_up_to_the_camera(self):
        for key in (RIGHT, RIGHT + 1, UP, UP + 1):
            with self.subTest(key=hex(key)):
                self.seen.clear()
                self.press(key)
                self.assertEqual([who for who, _ in self.seen], ['stock'])

    def test_closed_menu_still_lets_the_card_see_other_keys(self):
        self.seen.clear()
        self.press(OK)
        self.assertEqual([who for who, _ in self.seen], ['card'])

    def test_ael_opens_and_closes_without_reaching_the_camera(self):
        self.press(AEL)
        self.assertEqual(self.word(STATE + ST_OPEN), 1)
        self.press(AEL)
        self.assertEqual(self.word(STATE + ST_OPEN), 0)
        self.assertEqual(self.seen, [], 'the camera must not see an exposure lock')

    def test_open_menu_gives_right_and_up_to_the_card(self):
        self.press(AEL)
        self.seen.clear()
        self.press(RIGHT)
        self.press(UP)
        self.assertEqual([who for who, _ in self.seen], ['card', 'card'])

    def test_open_menu_leaves_the_rest_of_the_camera_working(self):
        self.press(AEL)
        self.seen.clear()
        self.press(OK)
        self.assertEqual([who for who, _ in self.seen], ['stock'])

    def test_menu_key_also_closes_it(self):
        self.press(AEL)
        self.press(MENU)
        self.assertEqual(self.word(STATE + ST_OPEN), 0)

    def test_a_missing_card_handler_falls_back_to_the_camera(self):
        self.put(STATE + 0x04, 0)
        self.seen.clear()
        self.press(OK)
        self.assertEqual([who for who, _ in self.seen], ['stock'])


if __name__ == '__main__':
    unittest.main()
