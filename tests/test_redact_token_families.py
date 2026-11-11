# tests/test_redact_token_families.py — familias GitHub/Slack y frontera de longitud
"""`ghs_`, `ghr_`, `xoxr`, `github_pat_` deben redactarse como el resto del
patrón RAW_TOKEN; la frontera de longitud del cuerpo también queda testada."""

from agents.audit import redact_credentials


def _secret(prefix: str, body_len: int = 20) -> str:
    return prefix + ("A1" * (body_len // 2 + 1))[:body_len]


def test_github_app_and_refresh_tokens_redacted():
    for prefix in ("ghs_", "ghr_", "ghu_", "gho_", "ghp_"):
        tok = _secret(prefix)
        assert redact_credentials(f"token={tok}") == "token=***", prefix


def test_slack_token_families_including_refresh():
    for prefix in ("xoxb-", "xoxp-", "xoxa-", "xoxs-", "xoxr-", "xoxe-"):
        tok = _secret(prefix)
        out = redact_credentials(f"slack {tok} fin")
        assert tok not in out, prefix
        assert "***" in out


def test_github_fine_grained_pat_redacted():
    tok = _secret("github_pat_")
    out = redact_credentials(f"GITHUB_TOKEN={tok}")
    assert tok not in out and "***" in out


def test_openai_style_sk_still_works():
    tok = _secret("sk-")
    out = redact_credentials(f"Bearer {tok}")
    assert tok not in out and "***" in out


def test_boundary_body_length_14_matched_13_not():
    ok14 = redact_credentials(_secret("ghp_", body_len=14))
    assert "***" in ok14, "cuerpo de 14 debe redactarse"

    short = "ghp_" + "A" * 13
    assert (
        redact_credentials(f"x {short} y") == f"x {short} y"
    ), "cuerpo de 13 NO debe disparar falsos positivos"


def test_safe_words_still_safe():
    text = "skill skyward gh-pages xoxo-hugs task"
    assert redact_credentials(text) == text


# ── Familias sin prefijo de proveedor ──────────


def test_jwt_redacted():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9P"
    )
    out = redact_credentials(f"Bearer {jwt}")
    assert jwt not in out and "***" in out
    # sin prefijo Authorization/Bearer delante también
    assert redact_credentials(f"x {jwt} y") == "x *** y"


def test_aws_access_key_redacted():
    key = "AKIAIOSFODNN7EXAMPLE"
    out = redact_credentials(f"aws_access_key_id={key}")
    assert key not in out and "***" in out


def test_google_api_key_redacted():
    key = "AIzaSyA1234567890abcdefghijklmnopqrstuv"
    out = redact_credentials(f"key={key}")
    assert key not in out and "***" in out


def test_bare_bearer_token_redacted():
    out = redact_credentials("Bearer abcdef1234567890abcdef")
    assert "abcdef1234567890abcdef" not in out
    assert "***" in out


def test_short_words_still_safe():
    """'bearer' seguido de palabra corta (prosa) NO se redacta."""
    text = "El bearer de la solución es único"
    assert redact_credentials(text) == text
