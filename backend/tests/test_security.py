from utils.security import get_password_hash, verify_password


def test_password_hash_roundtrip_with_long_password():
    password = "p" * 128

    hashed = get_password_hash(password)

    assert hashed.startswith("$2")
    assert verify_password(password, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_handles_invalid_hash():
    assert verify_password("password", "not-a-bcrypt-hash") is False
