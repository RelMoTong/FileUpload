# -*- coding: utf-8 -*-
"""
敏感信息加解密工具测试
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import protect_secret, unprotect_secret


class TestSecretUtils(unittest.TestCase):
    def test_unprotect_plaintext_returns_plaintext(self) -> None:
        self.assertEqual(unprotect_secret("plain-text"), "plain-text")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI 仅在 Windows 上可用")
    def test_protect_secret_roundtrip(self) -> None:
        encrypted = protect_secret("s3cret!Value")
        self.assertTrue(encrypted.startswith("dpapi:"))
        self.assertEqual(unprotect_secret(encrypted), "s3cret!Value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
