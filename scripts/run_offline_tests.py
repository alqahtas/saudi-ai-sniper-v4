"""Run unittest discovery with all outbound socket connects forbidden."""
import logging
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"src")]
logging.disable(logging.CRITICAL)
with patch.object(socket.socket,"connect",side_effect=AssertionError("Network forbidden in tests")), \
     patch.object(socket.socket,"connect_ex",side_effect=AssertionError("Network forbidden in tests")):
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(ROOT/"tests")))
raise SystemExit(not result.wasSuccessful())
