import os

os.environ["DEBUG"] = "false"

import pytest
from pydantic import ValidationError

from modules.auth.schemas import LoginRequest, RegisterRequest, ResetPasswordRequest


@pytest.mark.parametrize(
    "schema,payload",
    [
        (LoginRequest, {"email": "user@example.com", "password": "p" * 73}),
        (
            RegisterRequest,
            {"email": "user@example.com", "password": "密" * 25, "name": "user", "code": "123456"},
        ),
        (
            ResetPasswordRequest,
            {"email": "user@example.com", "password": "p" * 73, "code": "123456"},
        ),
    ],
)
def test_auth_requests_reject_passwords_beyond_bcrypt_byte_limit(schema, payload):
    with pytest.raises(ValidationError, match="72 UTF-8 bytes"):
        schema.model_validate(payload)
