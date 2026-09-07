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

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8080/",
            "http://localhost.localdomain/",
            "http://metadata.google.internal/latest/meta-data/",
            "http://127.0.0.1.nip.io/x",
            "http://1.2.3.4.sslip.io/x",
        ],
    )
    def test_blocks_private_hostnames(self, url):
        assert _is_private_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1.xip.io/x",
            "http://0x7f000001.nip.io/x",
        ],
    )
    def test_blocks_private_ip_encoded_in_hostname(self, url):
        assert _is_private_url(url) is True


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


# ── IP pinning + body cap ──

import socket as _socket
from unittest.mock import patch

from tools.web_fetch import _MAX_BODY_BYTES, _pinned_url, _resolve_first_ip


def _fake_addr(ip):
    return [(2, 1, 6, "", (ip, 0))]


def test_resolve_first_ip_public_selected_and_fail_closed():
    with patch(
        "tools.web_fetch.socket.getaddrinfo",
        return_value=_fake_addr("93.184.216.34"),
    ):
        assert _resolve_first_ip("example.com") == "93.184.216.34"

    with patch("tools.web_fetch.socket.getaddrinfo", side_effect=_socket.gaierror(1, "x")):
        assert _resolve_first_ip("noresuelve") is None

    # solo IPs privadas ⇒ None (fail-closed)
    with patch(
        "tools.web_fetch.socket.getaddrinfo",
        return_value=_fake_addr("192.168.1.5"),
    ):
        assert _resolve_first_ip("interno.local") is None


def test_pinned_url_ipv4_ipv6_port():
    assert (
        _pinned_url("https://example.com/a?b=1", "93.184.216.34") == "https://93.184.216.34/a?b=1"
    )
    assert _pinned_url("https://example.com/", "2606:2800::1") == "https://[2606:2800::1]/"
    out = _pinned_url("http://example.com:8080/x", "1.2.3.4")
    assert out == "http://1.2.3.4:8080/x"


@pytest.mark.asyncio
async def test_body_capped_before_decode(monkeypatch):
    """Un body gigante se corta en _MAX_BODY_BYTES sin decodificarlo entero."""
    import httpx as _httpx

    from tools.web_fetch import _web_fetch_tool

    big = b"<html><body>" + b"A" * (_MAX_BODY_BYTES + 500_000) + b"</body></html>"

    def handler(request):
        return _httpx.Response(200, headers={"content-type": "text/html"}, content=big)

    real_client = _httpx.AsyncClient

    class PinnedClient(real_client):
        pass

    # resolver DNS público fijo
    monkeypatch.setattr(
        "tools.web_fetch.socket.getaddrinfo",
        lambda *a, **k: _fake_addr("93.184.216.34"),
    )
    monkeypatch.setattr(_httpx, "AsyncClient", real_client)

    orig_init = real_client.__init__

    def patched_init(self, *a, **k):
        k.pop("transport", None)
        orig_init(self, *a, transport=_httpx.MockTransport(handler), **k)

    monkeypatch.setattr(_httpx.AsyncClient, "__init__", patched_init)

    result = await _web_fetch_tool(url="https://example.com/big")
    assert "truncado" in result
    assert len(result) < _MAX_BODY_BYTES
