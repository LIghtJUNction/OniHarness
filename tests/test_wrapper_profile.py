import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import steam_instance_entry as wrapper


class WrapperProfileTests(unittest.TestCase):
    def test_ordinary_and_managed_only_launches_do_not_force_native_profile(self):
        with patch.object(wrapper, "steam_ancestor", return_value=True):
            for arguments in [[], ["--cycletrim-managed-profile"]]:
                environment, profile = wrapper.prepare(["/game/OxygenNotIncluded"] + arguments, {})
                self.assertNotIn("CYCLETRIM_GPU_PROFILE", environment)
                self.assertIsNone(profile)

    def test_explicit_native_profile_is_preserved_without_mutating_input(self):
        original = {"CYCLETRIM_GPU_PROFILE": "1"}
        with patch.object(wrapper, "steam_ancestor", return_value=True):
            environment, _ = wrapper.prepare(["/game/OxygenNotIncluded"], original)
        self.assertEqual(environment["CYCLETRIM_GPU_PROFILE"], "1")
        self.assertEqual(original, {"CYCLETRIM_GPU_PROFILE": "1"})


if __name__ == "__main__":
    unittest.main()
