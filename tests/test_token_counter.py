"""Tests for token counter — lazy loading, error handling."""

from unittest.mock import MagicMock, patch

from core import token_counter


class TestTokenCounter:
    def teardown_method(self):
        token_counter._enc = None

    def test_get_encoding_loads_and_caches(self):
        token_counter._enc = None
        mock = MagicMock()
        mock.get_encoding.return_value = "fake_enc"
        with patch.dict("sys.modules", tiktoken=mock):
            result = token_counter.get_encoding()
            assert result == "fake_enc"
            assert token_counter._enc == "fake_enc"
            # Second call returns cached
            result2 = token_counter.get_encoding()
            assert result2 == "fake_enc"
            mock.get_encoding.assert_called_once()

    def test_get_encoding_import_error_returns_none(self):
        token_counter._enc = None
        # Patch import to raise ImportError
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("no tiktoken")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = token_counter.get_encoding()
            assert result is None

    def test_get_encoding_generic_exception_returns_none(self):
        token_counter._enc = None
        mock = MagicMock()
        mock.get_encoding.side_effect = RuntimeError("fake error")
        with patch.dict("sys.modules", tiktoken=mock):
            result = token_counter.get_encoding()
            assert result is None
