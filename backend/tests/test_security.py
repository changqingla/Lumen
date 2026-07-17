import pytest

from utils.security import get_password_hash, verify_password


def test_password_hash_roundtrip_at_bcrypt_limit():
    password = "p" * 72

    hashed = get_password_hash(password)

    assert hashed.startswith("$2")
    assert verify_password(password, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_password_hash_rejects_values_beyond_bcrypt_limit():
    with pytest.raises(ValueError, match="72 UTF-8 bytes"):
        get_password_hash("p" * 73)

    assert verify_password("p" * 73, get_password_hash("p" * 72)) is False


def test_password_hash_limit_counts_utf8_bytes():
    with pytest.raises(ValueError, match="72 UTF-8 bytes"):
        get_password_hash("密" * 25)


def test_verify_password_handles_invalid_hash():
    assert verify_password("password", "not-a-bcrypt-hash") is False
