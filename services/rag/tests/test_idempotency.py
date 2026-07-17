import hashlib
from uuid import UUID

import pytest

from idempotency import build_document_parse_idempotency_key


def test_document_parse_idempotency_key_known_vector():
    content_digest = hashlib.sha256(b"markdown").hexdigest()

    key = build_document_parse_idempotency_key(
        "11111111-1111-1111-1111-111111111111",
        content_digest,
    )

    assert key == "863f4aa3b6996f51323de50d26f698159faa637417be1e0a16e08606e4d2dc28"


@pytest.mark.parametrize(
    ("document_id", "digest"),
    [
        ("not-a-uuid", "a" * 64),
        (str(UUID(int=1)), "not-a-digest"),
    ],
)
def test_document_parse_idempotency_key_rejects_invalid_identity(
    document_id,
    digest,
):
    with pytest.raises(ValueError):
        build_document_parse_idempotency_key(document_id, digest)
