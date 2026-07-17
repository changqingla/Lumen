from __future__ import annotations

from task_metadata import contains_sensitive_task_metadata, sanitize_task_metadata


def test_sanitize_task_metadata_removes_nested_secrets_and_url_userinfo():
    metadata = {
        "embedding_config": {
            "model_name": "embedding-v1",
            "api_key": "embedding-secret",
            "base_url": "https://service-user:service-pass@example.test/v1",
        },
        "chunk_config": {
            "cv_model_config": {
                "model_factory": "OpenAI",
                "client-secret": "vision-secret",
            }
        },
        "store_config": {
            "es_host": "https://elastic:elastic-pass@es.test:9200",
            "password": "elastic-pass",
        },
        "message": "provider rejected embedding-secret",
        "result_data": {"total_tokens": 42},
    }

    sanitized = sanitize_task_metadata(metadata)

    assert contains_sensitive_task_metadata(metadata) is True
    assert sanitized["embedding_config"] == {
        "model_name": "embedding-v1",
        "base_url": "https://example.test/v1",
    }
    assert sanitized["chunk_config"]["cv_model_config"] == {
        "model_factory": "OpenAI"
    }
    assert sanitized["store_config"] == {"es_host": "https://es.test:9200"}
    assert sanitized["message"] == "provider rejected [REDACTED]"
    assert sanitized["result_data"] == {"total_tokens": 42}
    assert contains_sensitive_task_metadata(sanitized) is False


def test_sanitize_task_metadata_does_not_mutate_input():
    metadata = {"config": {"api_key": "secret", "batch_size": 16}}

    sanitized = sanitize_task_metadata(metadata)

    assert metadata["config"]["api_key"] == "secret"
    assert sanitized == {"config": {"batch_size": 16}}
