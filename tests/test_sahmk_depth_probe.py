import json
import unittest
from unittest.mock import patch
import sahmk_depth_probe as depth

class FakeSocket:
    def __init__(self, packets):
        self.packets = iter(json.dumps(packet) for packet in packets)
        self.sent=[]
    def settimeout(self, seconds):
        self.timeout=seconds
    def recv(self):
        return next(self.packets)
    def send(self, message):
        self.sent.append(json.loads(message))
    def close(self):
        self.closed=True

class DepthAccessTests(unittest.TestCase):
    def test_pro_level_five_levels(self):
        levels,entitled=depth.parse_entitlement({"type":"connected","channel":"depth",
                                                "plan":"pro","entitled_levels":5,
                                                "limits":{"max_symbols_per_call":20}})
        self.assertEqual((levels,entitled),(5,5))
    def test_business_twenty_entitlement_still_starts_five(self):
        self.assertEqual(depth.parse_entitlement({"type":"connected","channel":"depth",
                            "plan":"business","entitled_levels":20,"limits":{"max_symbols_per_call":20}}),(5,20))
    def test_absent_entitlement_refused(self):
        with self.assertRaises(PermissionError):
            depth.parse_entitlement({"type":"connected","channel":"depth","entitled_levels":0})
    def test_full_handshake_and_snapshot(self):
        packets=[{"type":"connected","channel":"depth","plan":"pro","entitled_levels":5,
                  "limits":{"max_symbols_per_connection":200,"max_symbols_per_call":20}},
                 {"type":"depth_snapshot","symbol":"2222","bids":[],"asks":[]},
                 {"type":"subscribed","symbols":["2222"],"total":1,"limit":200}]
        sock=FakeSocket(packets)
        with patch.dict("os.environ",{"SAHMK_API_KEY":"redacted_test_key"}):
            result=depth.probe(socket_factory=lambda *args,**kwargs:sock)
        self.assertTrue(result["snapshot"])
        self.assertEqual(sock.sent,[{"action":"subscribe","symbols":["2222"],"levels":5}])
        self.assertTrue(sock.closed)
    def test_error_is_not_treated_as_permission(self):
        sock=FakeSocket([{"type":"error","code":"ACCESS_DENIED","message":"secret"}])
        with patch.dict("os.environ",{"SAHMK_API_KEY":"redacted_test_key"}):
            with self.assertRaisesRegex(PermissionError,"ACCESS_DENIED"):
                depth.probe(socket_factory=lambda *args,**kwargs:sock)

if __name__=="__main__":
    unittest.main()
