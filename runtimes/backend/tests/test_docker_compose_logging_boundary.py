from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker" / "docker-compose.yml"


def test_runtime_stdout_uses_compose_bounded_log_driver():
    services = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]

    for service_name in ("lumen_gateway", "lumen_langgraph"):
        command = " ".join(services[service_name]["command"])
        assert ">" not in command
        assert "2>&1" not in command
        assert services[service_name]["logging"]["driver"] == "json-file"
        assert services[service_name]["logging"]["options"] == {
            "max-size": "${DOCKER_LOG_MAX_SIZE:-10m}",
            "max-file": "${DOCKER_LOG_MAX_FILE:-3}",
        }
