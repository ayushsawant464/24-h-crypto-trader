import unittest
from bot.data.roostoo_client import RoostooClient

class TestRoostooClient(unittest.TestCase):
    def test_hmac_signature_generation(self):
        """
        Validates signature generation against Roostoo official test vector from API documentation.
        """
        secret = "S1XP1e3UZj6A7H5fATj0jNhqPxxdSJYdInClVN65XAbvqqMKjVHjA7PZj4W12oep"
        client = RoostooClient(api_key="TEST_KEY", secret_key=secret)

        params = {
            "timestamp": 1580774512000,
            "pair": "BNB/USD",
            "quantity": 2000,
            "side": "BUY",
            "type": "MARKET"
        }

        sig = client.generate_signature(params)
        expected_sig = "20b7fd5550b67b3bf0c1684ed0f04885261db8fdabd38611e9e6af23c19b7fff"
        self.assertEqual(sig, expected_sig)

    def test_public_server_time(self):
        """
        Integration test checking connectivity to Roostoo public serverTime.
        """
        client = RoostooClient()
        resp = client.get_server_time()
        self.assertIn("ServerTime", resp)
        self.assertIsInstance(resp["ServerTime"], int)

if __name__ == "__main__":
    unittest.main()
