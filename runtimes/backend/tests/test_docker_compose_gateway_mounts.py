from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker" / "docker-compose.yml"


def _service(name: str) -> dict:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"][name]


def test_gateway_only_mounts_mutable_extension_state_as_writable():
    gateway = _service("lumen_gateway")

    assert gateway["environment"]["LUMEN_EXTENSIONS_CONFIG_PATH"] == "/app/extensions/extensions_config.json"
    assert "../runtimes/config:/app/config:ro" in gateway["volumes"]
    assert "../runtimes/config/extensions:/app/extensions:rw" in gateway["volumes"]
    assert "../runtimes/skills/public:/app/skills/public:ro" in gateway["volumes"]
    assert "../runtimes/skills/custom:/app/skills/custom:rw" in gateway["volumes"]


def test_langgraph_reads_the_same_extension_state_without_write_access():
    langgraph = _service("lumen_langgraph")

    assert langgraph["environment"]["LUMEN_EXTENSIONS_CONFIG_PATH"] == "/app/extensions/extensions_config.json"
    assert "../runtimes/config/extensions:/app/extensions:ro" in langgraph["volumes"]
    assert "../runtimes/backend:/app/backend:ro" in langgraph["volumes"]
    assert "../runtimes/skills/public:/app/skills/public:ro" in langgraph["volumes"]
    assert "../runtimes/skills/custom:/app/skills/custom:ro" in langgraph["volumes"]


def test_only_constrained_provisioner_holds_docker_socket():
    gateway = _service("lumen_gateway")
    langgraph = _service("lumen_langgraph")
    provisioner = _service("lumen_sandbox_provisioner")

    assert "/var/run/docker.sock:/var/run/docker.sock" not in langgraph["volumes"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in provisioner["volumes"]
    assert "ports" not in provisioner
    assert langgraph["environment"]["SANDBOX_PROVISIONER_URL"] == (
        "http://lumen_sandbox_provisioner:8002"
    )
    token_expression = (
        "${SANDBOX_PROVISIONER_INTERNAL_TOKEN:"
        "?SANDBOX_PROVISIONER_INTERNAL_TOKEN is required}"
    )
    assert langgraph["environment"]["SANDBOX_PROVISIONER_INTERNAL_TOKEN"] == token_expression
    assert provisioner["environment"]["SANDBOX_PROVISIONER_INTERNAL_TOKEN"] == token_expression
    assert gateway["environment"]["SANDBOX_PROVISIONER_INTERNAL_TOKEN"] == ""
    assert provisioner["read_only"] is True
    assert provisioner["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in provisioner["security_opt"]
    provisioned_image = provisioner["environment"]["SANDBOX_PROVISIONER_IMAGE"]
    assert "@sha256:" in provisioned_image
    assert not provisioned_image.endswith(":latest")


def test_runtime_services_do_not_share_executable_venv_or_package_cache():
    gateway = _service("lumen_gateway")
    langgraph = _service("lumen_langgraph")
    provisioner = _service("lumen_sandbox_provisioner")

    expected_volumes = {
        "lumen_gateway_runtime_env:/app/runtime-env",
        "lumen_langgraph_runtime_env:/app/runtime-env",
        "lumen_sandbox_provisioner_runtime_env:/app/runtime-env",
    }
    actual_volumes = {
        next(volume for volume in service["volumes"] if volume.endswith(":/app/runtime-env"))
        for service in (gateway, langgraph, provisioner)
    }
    assert actual_volumes == expected_volumes
    for service in (gateway, langgraph, provisioner):
        assert service["environment"]["LUMEN_RUNTIME_VENV_DIR"] == "/app/runtime-env/venv"
        assert service["environment"]["LUMEN_RUNTIME_VENV_SEED_PATH"] == (
            "${RUNTIME_VENV_SEED_PATH:-/opt/insight-flow-venv}"
        )
        assert service["environment"]["UV_CACHE_DIR"] == "/app/runtime-env/uv-cache"
        assert not any("runtime-venv" in volume for volume in service["volumes"])


def test_gateway_internal_token_is_required_and_shared_by_all_callers():
    token_expression = "${GATEWAY_INTERNAL_API_TOKEN:?GATEWAY_INTERNAL_API_TOKEN is required}"

    assert _service("lumen_gateway")["environment"]["GATEWAY_INTERNAL_API_TOKEN"] == token_expression
    assert _service("lumen_langgraph")["environment"]["GATEWAY_INTERNAL_API_TOKEN"] == token_expression
    assert _service("lumen_api")["environment"]["GATEWAY_INTERNAL_API_TOKEN"] == token_expression

    gateway_healthcheck = " ".join(_service("lumen_gateway")["healthcheck"]["test"])
    assert "/health" in gateway_healthcheck
    assert "X-Gateway-Internal-Token" not in gateway_healthcheck


def test_runtime_services_do_not_inherit_backend_business_secrets():
    expected_runtime_env = ["../runtimes/config/.env"]

    for service_name in ("lumen_gateway", "lumen_langgraph"):
        service = _service(service_name)
        assert service["env_file"] == expected_runtime_env
        assert "../backend/.env" not in service["env_file"]


def test_backend_and_runtime_share_the_same_model_endpoint_policy():
    expected_private_policy = "${MODEL_PROVIDER_ALLOW_PRIVATE_ENDPOINTS:-false}"
    expected_dns_timeout = "${MODEL_PROVIDER_DNS_TIMEOUT_SECONDS:-5.0}"

    for service_name in ("lumen_api", "lumen_gateway", "lumen_langgraph"):
        environment = _service(service_name)["environment"]
        assert environment["MODEL_PROVIDER_ALLOW_PRIVATE_ENDPOINTS"] == (
            expected_private_policy
        )
        assert environment["MODEL_PROVIDER_DNS_TIMEOUT_SECONDS"] == (
            expected_dns_timeout
        )
