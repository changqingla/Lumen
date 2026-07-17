from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker" / "docker-compose.yml"
REDIS_CONFIG_PATH = COMPOSE_PATH.parent / "redis.conf.template"


def _services() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]


def test_rag_receives_only_its_explicit_secret_allowlist():
    services = _services()
    rag = services["rag"]
    environment = rag["environment"]

    assert "env_file" not in rag
    assert environment["RAG_INTERNAL_API_TOKEN"] == (
        "${RAG_INTERNAL_API_TOKEN:?RAG_INTERNAL_API_TOKEN is required}"
    )
    assert services["lumen_api"]["environment"]["RAG_INTERNAL_API_TOKEN"] == (
        environment["RAG_INTERNAL_API_TOKEN"]
    )

    forbidden = {
        "DATABASE_URL",
        "SECRET_KEY",
        "MODEL_CONFIG_ENCRYPTION_KEY",
        "MODEL_RESOLVER_INTERNAL_TOKEN",
        "GATEWAY_INTERNAL_API_TOKEN",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "SMTP_PASSWORD",
    }
    assert forbidden.isdisjoint(environment)


def test_model_resolver_token_is_shared_only_by_backend_and_langgraph():
    services = _services()
    expression = (
        "${MODEL_RESOLVER_INTERNAL_TOKEN:"
        "?MODEL_RESOLVER_INTERNAL_TOKEN is required}"
    )

    assert services["lumen_api"]["environment"]["MODEL_RESOLVER_INTERNAL_TOKEN"] == expression
    assert (
        services["lumen_langgraph"]["environment"]["MODEL_RESOLVER_INTERNAL_TOKEN"]
        == expression
    )
    assert services["lumen_gateway"]["environment"]["MODEL_RESOLVER_INTERNAL_TOKEN"] == ""
    for service_name in ("rag", "lumen_sandbox_provisioner"):
        assert "MODEL_RESOLVER_INTERNAL_TOKEN" not in services[service_name]["environment"]


def test_rag_source_is_read_only_and_task_state_is_explicitly_persistent():
    rag = _services()["rag"]

    assert "../services/rag:/workspace/rag:ro" in rag["volumes"]
    assert (
        "${RAG_TASK_STATE_DIR:-/root/data/lumen-rag}:/var/lib/lumen-rag"
        in rag["volumes"]
    )
    assert rag["environment"]["TEMP_DIR"] == "/var/lib/lumen-rag/tmp"
    assert rag["environment"]["RAG_CACHE_DIR"] == "/var/lib/lumen-rag/cache"
    assert rag["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_rag_redis_identity_is_limited_to_its_queue_prefix():
    services = _services()
    rag_environment = services["rag"]["environment"]
    redis_config = REDIS_CONFIG_PATH.read_text(encoding="utf-8")
    rag_acl = next(
        line for line in redis_config.splitlines() if line.startswith("user rag ")
    )

    assert rag_environment["REDIS_USERNAME"] == "rag"
    assert rag_environment["REDIS_PASSWORD"] == (
        "${RAG_REDIS_PASSWORD:?RAG_REDIS_PASSWORD is required}"
    )
    assert "~document_parse_queue:*" in rag_acl
    assert "+eval" in rag_acl
    assert "+@all" not in rag_acl
    assert "~*" not in rag_acl


def test_redis_passwords_are_runtime_variables_with_fail_closed_validation():
    redis = _services()["redis"]
    command = redis["command"][-1]
    healthcheck = " ".join(redis["healthcheck"]["test"])

    assert redis["environment"] == {
        "REDIS_PASSWORD": "${REDIS_PASSWORD:?REDIS_PASSWORD is required}",
        "RAG_REDIS_PASSWORD": (
            "${RAG_REDIS_PASSWORD:?RAG_REDIS_PASSWORD is required}"
        ),
    }
    assert "${REDIS_PASSWORD:?" not in command
    assert "${RAG_REDIS_PASSWORD:?" not in command
    assert "$$REDIS_PASSWORD" in command
    assert "$$RAG_REDIS_PASSWORD" in command
    assert "replace-with-a-strong-random-lumen-redis-password" in command
    assert "replace-with-an-independent-random-rag-redis-password" in command
    assert "*[!A-Za-z0-9_-]*" in command
    assert 'REDISCLI_AUTH="$$REDIS_PASSWORD"' in healthcheck
    assert "${REDIS_PASSWORD:?" not in healthcheck
