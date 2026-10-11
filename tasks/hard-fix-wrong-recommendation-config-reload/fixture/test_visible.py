import json
import tempfile
import unittest
from pathlib import Path

import daemon


class Visible(unittest.TestCase):
    def test_valid_overrides_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.json"
            path.write_text(json.dumps({"merge_enabled": False}))
            state = daemon.reload({"config": {}, "degraded": None}, path)
        self.assertFalse(state["config"]["merge_enabled"])
        self.assertEqual(state["config"]["max_workers"], 4)
        self.assertIsNone(state["degraded"])


if __name__ == "__main__":
    unittest.main()
