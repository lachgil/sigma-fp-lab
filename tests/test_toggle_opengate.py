import contextlib
import hashlib
import io
import unittest
from unittest.mock import patch

import toggle_opengate as toggle


class ToggleSafetyTests(unittest.TestCase):
    def setUp(self):
        # Synthetic RAM transport. No firmware, camera or external process.
        self.payload = bytes(toggle.CODE_BYTES)
        self.memory = {a: v[0] for a, v in toggle.PATCHES.items()}
        self.memory.update({toggle.HOOK: toggle.HOOK_ARM, toggle.ARMED: 0,
                            toggle.DARK_ADDR: toggle.DARK_STOCK})
        self.memory.update({toggle.CODE + off: 0 for off in range(0, len(self.payload), 4)})
        self.before = self.memory.copy()
        self.drop = None
        self.writes = []
        transport = patch.object(toggle, "fpsh", self.shell)
        digest = patch.object(toggle, "CODE_SHA256", hashlib.sha256(self.payload).hexdigest())
        transport.start()
        digest.start()
        self.addCleanup(transport.stop)
        self.addCleanup(digest.stop)

    def shell(self, command, operation, location, *values):
        assert command == "mem"
        address = int(location.split(",")[0], 16)
        if operation == "get":
            return f"D:0x{self.memory[address]:08X}"
        value = int(values[0], 16)
        self.writes.append((address, value))
        if address != self.drop:
            self.memory[address] = value
        return ""

    def run_cli(self, op):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(toggle.sys, "argv", ["toggle_opengate.py", op]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = toggle.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_dropped_table_write_never_enables_partial_state(self):
        self.drop = next(iter(toggle.PATCHES))
        result, stdout, stderr = self.run_cli("on")
        self.assertEqual(result, 1)
        self.assertNotIn("ENABLED", stdout)
        self.assertIn("Write failed", stderr)
        self.assertEqual(self.memory[toggle.ARMED], 0)
        self.assertEqual(self.memory[self.drop], self.before[self.drop])
        self.assertNotIn((toggle.ARMED, 1), self.writes)

    def test_unknown_payload_refuses_every_write(self):
        self.memory[toggle.CODE] = 1
        result, _, stderr = self.run_cli("on")
        self.assertEqual(result, 1)
        self.assertIn("Unknown or missing", stderr)
        self.assertEqual(self.writes, [])

    def test_shell_only_boot_does_not_install_a_branch(self):
        self.memory[toggle.HOOK] = toggle.HOOK_STOCK
        result, _, _ = self.run_cli("on")
        self.assertEqual(result, 1)
        self.assertEqual(self.writes, [])

    def test_unknown_table_value_is_not_overwritten(self):
        self.memory[next(iter(toggle.PATCHES))] = 0xDEADBEEF
        result, _, _ = self.run_cli("on")
        self.assertEqual(result, 1)
        self.assertEqual(self.writes, [])

    def test_enable_gain_disable_restores_data_without_code_writes(self):
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(self.memory[toggle.ARMED], 1)
        self.assertTrue(all(self.memory[a] == v[1] for a, v in toggle.PATCHES.items()))
        self.assertEqual(self.run_cli("dark-on")[0], 0)
        self.assertEqual(self.memory[toggle.DARK_ADDR], toggle.DARK_COMP)
        self.assertEqual(self.run_cli("off")[0], 0)
        self.assertEqual(self.memory, self.before)
        self.assertNotIn(toggle.HOOK, [a for a, _ in self.writes])
        self.assertFalse(any(toggle.CODE <= a < toggle.CODE + toggle.CODE_BYTES
                             for a, _ in self.writes))

    def test_dark_on_refuses_stock_mode(self):
        result, _, _ = self.run_cli("dark-on")
        self.assertEqual(result, 1)
        self.assertEqual(self.writes, [])

    def test_missing_memory_reply_is_a_cli_error_not_typeerror(self):
        with patch.object(toggle, "fpsh", return_value="camera disconnected"):
            result, _, stderr = self.run_cli("status")
        self.assertEqual(result, 1)
        self.assertIn("Invalid memory reply", stderr)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
