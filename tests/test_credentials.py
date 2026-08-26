from __future__ import annotations

import unittest
from unittest import mock

from installer import credentials


class CredentialErrorHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        credentials._ADVAPI32 = None

    @mock.patch("installer.credentials.ctypes.set_last_error")
    @mock.patch("installer.credentials.ctypes.get_last_error", return_value=1168)
    @mock.patch("installer.credentials._advapi32")
    def test_delete_missing_credential_is_idempotent(self, advapi32, get_error, set_error) -> None:
        api = advapi32.return_value
        api.CredDeleteW.return_value = False

        credentials._delete_secret("missing")

        set_error.assert_called_once_with(0)
        get_error.assert_called_once_with()

    @mock.patch("installer.credentials.ctypes.set_last_error")
    @mock.patch("installer.credentials.ctypes.get_last_error", return_value=5)
    @mock.patch("installer.credentials._advapi32")
    def test_delete_access_denied_is_not_hidden(self, advapi32, get_error, set_error) -> None:
        api = advapi32.return_value
        api.CredDeleteW.return_value = False

        with self.assertRaisesRegex(credentials.CredentialError, "Windows 错误码 5"):
            credentials._delete_secret("protected")

    @mock.patch("installer.credentials.ctypes.set_last_error")
    @mock.patch("installer.credentials.ctypes.get_last_error", return_value=0)
    @mock.patch("installer.credentials._advapi32")
    def test_delete_false_with_zero_error_is_reported_as_binding_failure(self, advapi32, get_error, set_error) -> None:
        api = advapi32.return_value
        api.CredDeleteW.return_value = False

        with self.assertRaisesRegex(credentials.CredentialError, "未返回有效错误码"):
            credentials._delete_secret("unknown")

    @mock.patch("installer.credentials.ctypes.WinDLL")
    def test_advapi32_enables_last_error_capture(self, win_dll) -> None:
        credentials._ADVAPI32 = None

        self.assertIs(credentials._advapi32(), win_dll.return_value)
        win_dll.assert_called_once_with("Advapi32.dll", use_last_error=True)


if __name__ == "__main__":
    unittest.main()
