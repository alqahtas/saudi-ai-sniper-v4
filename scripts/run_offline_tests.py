"""Run real test suites with outbound connections forbidden."""
import logging
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
logging.disable(logging.CRITICAL)
with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden in tests")), \
     patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Network forbidden in tests")):
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName("test_smoke"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
