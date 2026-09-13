"""AES-256-GCM envelope encryption, and the ``enc://`` credential pointer.

These tests are pure CPU — no database, no Redis, no event loop — because the
module they cover is. That is deliberate: `app/core/crypto.py` is the one place
where a subtle mistake is silent rather than loud, so its tests must be the
ones that always run.

What is asserted here is mostly *refusal*: a wrong key, a wrong context, a
flipped bit and a truncated token must all fail, and fail the same way. A test
suite that only proves round-tripping proves the encryption works and says
nothing about whether it protects anything.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.core import crypto


def _key() -> str:
    return base64.b64encode(os.urandom(crypto.KEY_BYTES)).decode("ascii")


@pytest.fixture
def ring(monkeypatch: pytest.MonkeyPatch):
    """A single-key ring, installed for one test."""
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"2025a:{_key()}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "2025a")
    crypto.reset_cache()
    yield
    crypto.reset_cache()


@pytest.fixture
def no_ring(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crypto.settings, "encryption_keys", "")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "")
    crypto.reset_cache()
    yield
    crypto.reset_cache()


# ── Round trip ────────────────────────────────────────────────────────


def test_round_trip_returns_the_original(ring):
    assert crypto.decrypt(crypto.encrypt("hunter2", context="c"), context="c") == "hunter2"


@pytest.mark.parametrize(
    "value",
    ["", "a", "GJ03AB1234", "üñïçødé ✓", "x" * 8192, "colons:in:value"],
    ids=["empty", "single", "plate", "unicode", "long", "colons"],
)
def test_round_trip_preserves_awkward_values(ring, value):
    assert crypto.decrypt(crypto.encrypt(value, context="c"), context="c") == value


def test_ciphertext_does_not_contain_the_plaintext(ring):
    token = crypto.encrypt("hunter2", context="vms-credentials")
    assert "hunter2" not in token


# ── Nonce hygiene ─────────────────────────────────────────────────────


def test_same_plaintext_seals_differently_each_time(ring):
    """A fresh nonce per call, so equal values are not equal ciphertexts.

    Without this, an observer of the column learns which rows share a password
    without decrypting anything.
    """
    tokens = {crypto.encrypt("same", context="c") for _ in range(50)}
    assert len(tokens) == 50


def test_nonces_are_never_repeated(ring):
    nonces = {crypto.encrypt("v", context="c").split(".")[2] for _ in range(200)}
    assert len(nonces) == 200


# ── Token shape ───────────────────────────────────────────────────────


def test_token_is_versioned_and_names_its_key(ring):
    version, key_id, nonce, ciphertext = crypto.encrypt("v", context="c").split(".")
    assert version == crypto.TOKEN_VERSION
    assert key_id == "2025a"
    assert len(crypto._b64decode(nonce)) == crypto.NONCE_BYTES
    assert ciphertext


def test_token_is_url_safe(ring):
    """No characters that would need escaping in a URL, header or CSV cell."""
    token = crypto.encrypt("x" * 500, context="c")
    assert all(c.isalnum() or c in "-_." for c in token)


# ── Refusals: this is the part that matters ───────────────────────────


def test_wrong_context_will_not_open_it(ring):
    """The AAD binding — a ciphertext cannot be relocated to another field."""
    token = crypto.encrypt("secret", context="vms-credentials")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, context="something-else")


def test_wrong_key_will_not_open_it(ring, monkeypatch):
    token = crypto.encrypt("secret", context="c")
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"2025a:{_key()}")
    crypto.reset_cache()
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, context="c")


def test_a_flipped_bit_is_detected(ring):
    """GCM is authenticated: tampering raises rather than returning garbage."""
    version, key_id, nonce, ciphertext = crypto.encrypt("secret", context="c").split(".")
    raw = bytearray(crypto._b64decode(ciphertext))
    raw[0] ^= 0x01
    tampered = ".".join((version, key_id, nonce, crypto._b64encode(bytes(raw))))
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered, context="c")


def test_a_swapped_nonce_is_detected(ring):
    a = crypto.encrypt("secret one", context="c").split(".")
    b = crypto.encrypt("secret two", context="c").split(".")
    spliced = ".".join((a[0], a[1], b[2], a[3]))
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(spliced, context="c")


@pytest.mark.parametrize(
    "token",
    ["", "v1", "v1.2025a.abc", "v1.2025a.abc.def.ghi", "not-a-token"],
    ids=["empty", "one-part", "three-parts", "five-parts", "garbage"],
)
def test_malformed_tokens_are_rejected(ring, token):
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, context="c")


def test_unknown_version_is_rejected(ring):
    token = crypto.encrypt("v", context="c")
    with pytest.raises(crypto.DecryptionError, match="version"):
        crypto.decrypt("v99" + token[2:], context="c")


def test_token_from_an_absent_key_is_named_not_guessed(ring):
    token = crypto.encrypt("v", context="c")
    retired = "v1.1999x." + token.split(".", 2)[2]
    with pytest.raises(crypto.DecryptionError, match="not in this ring"):
        crypto.decrypt(retired, context="c")


def test_empty_context_is_refused_both_ways(ring):
    with pytest.raises(crypto.EncryptionError):
        crypto.encrypt("v", context="")
    with pytest.raises(crypto.EncryptionError):
        crypto.decrypt("v1.2025a.aa.bb", context="")


# ── Key ring validation ───────────────────────────────────────────────


def test_a_short_key_is_refused_rather_than_downgrading_to_aes128(monkeypatch):
    monkeypatch.setattr(
        crypto.settings, "encryption_keys", f"k:{base64.b64encode(os.urandom(16)).decode()}"
    )
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "k")
    crypto.reset_cache()
    with pytest.raises(crypto.EncryptionError, match="requires 32"):
        crypto.key_ring()
    crypto.reset_cache()


@pytest.mark.parametrize(
    "spec, match",
    [
        ("no-colon-here", "key_id:base64_key"),
        ("k:!!!not-base64!!!", "not valid base64"),
        ("has.dot:" + base64.b64encode(b"x" * 32).decode(), "may not contain"),
    ],
    ids=["no-separator", "bad-base64", "dot-in-id"],
)
def test_malformed_key_ring_entries_are_refused(monkeypatch, spec, match):
    monkeypatch.setattr(crypto.settings, "encryption_keys", spec)
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "")
    crypto.reset_cache()
    with pytest.raises(crypto.EncryptionError, match=match):
        crypto.key_ring()
    crypto.reset_cache()


def test_active_key_must_exist_in_the_ring(monkeypatch):
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"a:{_key()}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "b")
    crypto.reset_cache()
    with pytest.raises(crypto.EncryptionError, match="not present"):
        crypto.key_ring()
    crypto.reset_cache()


def test_active_key_is_implied_when_only_one_is_configured(monkeypatch):
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"solo:{_key()}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "")
    crypto.reset_cache()
    assert crypto.key_ring().active_key_id == "solo"
    crypto.reset_cache()


def test_active_key_must_be_named_when_several_are_configured(monkeypatch):
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"a:{_key()},b:{_key()}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "")
    crypto.reset_cache()
    with pytest.raises(crypto.EncryptionError, match="must name a key"):
        crypto.key_ring()
    crypto.reset_cache()


def test_key_ring_repr_does_not_leak_key_material(monkeypatch):
    key = _key()
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"2025a:{key}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "2025a")
    crypto.reset_cache()
    text = repr(crypto.key_ring())
    assert key not in text and "2025a" in text
    crypto.reset_cache()


# ── Rotation ──────────────────────────────────────────────────────────


def test_a_retired_key_still_opens_what_it_sealed(monkeypatch):
    """The point of the ring: rotate the active key without a flag day."""
    old, new = _key(), _key()
    monkeypatch.setattr(crypto.settings, "encryption_keys", f"old:{old}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "old")
    crypto.reset_cache()
    sealed_before = crypto.encrypt("archived", context="c")

    monkeypatch.setattr(crypto.settings, "encryption_keys", f"old:{old},new:{new}")
    monkeypatch.setattr(crypto.settings, "encryption_active_key_id", "new")
    crypto.reset_cache()

    assert crypto.decrypt(sealed_before, context="c") == "archived"
    assert crypto.encrypt("fresh", context="c").split(".")[1] == "new"
    crypto.reset_cache()


# ── Absent configuration ──────────────────────────────────────────────


def test_without_keys_it_refuses_rather_than_storing_plaintext(no_ring):
    assert crypto.is_enabled() is False
    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.encrypt("secret", context="c")
    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.decrypt("v1.k.aa.bb", context="c")


def test_is_enabled_is_true_with_a_ring(ring):
    assert crypto.is_enabled() is True


def test_generate_key_produces_a_usable_aes256_key():
    assert len(base64.b64decode(crypto.generate_key())) == crypto.KEY_BYTES


# ── The enc:// credential pointer ─────────────────────────────────────


def test_enc_pointer_resolves_a_sealed_credential(ring):
    from app.services import secrets

    token = crypto.encrypt("vmsuser:vmspass", context=secrets.CREDENTIALS_CONTEXT)
    resolved = secrets.resolve_credentials(f"enc://{token}")
    assert resolved is not None
    assert (resolved.username, resolved.password) == ("vmsuser", "vmspass")


def test_enc_pointer_never_prints_its_password(ring):
    from app.services import secrets

    token = crypto.encrypt("vmsuser:hunter2", context=secrets.CREDENTIALS_CONTEXT)
    resolved = secrets.resolve_credentials(f"enc://{token}")
    assert "hunter2" not in repr(resolved)


def test_enc_pointer_sealed_for_another_context_does_not_resolve(ring):
    """A ciphertext from a different field must not open as a credential."""
    from app.services import secrets

    token = crypto.encrypt("vmsuser:vmspass", context="some-other-field")
    assert secrets.resolve_credentials(f"enc://{token}") is None


@pytest.mark.parametrize(
    "reference",
    ["enc://", "enc://garbage", "enc://v1.2025a.aa.bb"],
    ids=["empty", "garbage", "undecryptable"],
)
def test_bad_enc_pointer_returns_none_rather_than_raising(ring, reference):
    """One unreadable row must not take down supervision for the whole fleet."""
    from app.services import secrets

    assert secrets.resolve_credentials(reference) is None


def test_enc_pointer_without_a_key_ring_returns_none(no_ring):
    from app.services import secrets

    assert secrets.resolve_credentials("enc://v1.2025a.aa.bb") is None


def test_existing_schemes_are_unaffected(ring, monkeypatch, tmp_path):
    """The demo path uses env:// and file://; enc:// must not disturb them."""
    from app.services import secrets

    monkeypatch.setenv("T_USER", "u")
    monkeypatch.setenv("T_PASS", "p")
    assert secrets.resolve_credentials("env://T_USER:T_PASS").username == "u"

    path = tmp_path / "cred"
    path.write_text("fileuser:filepass", encoding="utf-8")
    assert secrets.resolve_credentials(f"file://{path}").password == "filepass"

    assert secrets.resolve_credentials(None) is None
    assert secrets.resolve_credentials("vault://x") is None
    assert secrets.resolve_credentials("nonsense://x") is None
