# tests/test_web_fetch.py
"""Tests de seguridad y funcionalidad para web_fetch."""

import pytest

from tools.web_fetch import _is_private_url


class TestIsPrivateUrl:
    def test_blocks_loopback_ipv4(self):
        assert _is_private_url("http://127.0.0.1:8080/") is True

    def test_blocks_private_ipv4(self):
        assert _is_private_url("http://192.168.1.1/") is True
        assert _is_private_url("http://10.0.0.1/") is True
        assert _is_private_url("http://172.16.0.1/") is True

    def test_blocks_loopback_ipv6(self):
        assert _is_private_url("http://[::1]:8080/") is True

    def test_blocks_ipv4_mapped_ipv6(self):
        assert _is_private_url("http://[::ffff:127.0.0.1]/") is True

    def test_allows_public_ip(self):
        assert _is_private_url("http://8.8.8.8/") is False

    def test_allows_hostname(self):
        assert _is_private_url("https://www.google.com/") is False

    def test_blocks_empty_hostname(self):
        assert _is_private_url("not-a-url") is True

    def test_blocks_aws_metadata(self):
        assert _is_private_url("http://169.254.169.254/latest/meta-data/") is True

    def test_blocks_unspecified_ipv6(self):
        assert _is_private_url("http://[::]/") is True


@pytest.mark.asyncio
async def test_web_fetch_invalid_url():
    """Verifica que URLs sin http/https se rechazan."""
    from tools.web_fetch import _web_fetch_tool

    result = await _web_fetch_tool("ftp://example.com")
    assert "inválida" in result.lower()


@pytest.mark.asyncio
async def test_web_fetch_private_url_blocked():
    """Verifica que URLs a IPs privadas se bloquean."""
    from tools.web_fetch import _web_fetch_tool

    result = await _web_fetch_tool("http://127.0.0.1/secret")
    assert "denegado" in result.lower()
