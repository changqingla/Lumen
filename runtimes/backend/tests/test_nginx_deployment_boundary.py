from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "docker" / "docker-compose.yml"
NGINX_TEMPLATE_PATH = ROOT / "docker" / "nginx" / "lumen.conf.template"


def _nginx_service() -> dict:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"]["lumen_nginx"]


def test_nginx_defaults_to_https_without_runtime_startup_coupling():
    nginx = _nginx_service()

    assert nginx["environment"]["LUMEN_HTTP_MODE"] == (
        "${LUMEN_HTTP_MODE:-redirect}"
    )
    command = " ".join(nginx["command"])
    assert "redirect|serve" in command
    assert set(nginx["depends_on"]) == {"lumen_api", "lumen_minio"}


def test_nginx_template_enforces_redirect_and_reachable_streaming_proxy():
    template = NGINX_TEMPLATE_PATH.read_text(encoding="utf-8")
    server_blocks = template.split("\nserver {")[1:]

    assert len(server_blocks) == 2
    assert 'map "${LUMEN_HTTP_MODE}" $lumen_force_https' in template
    assert "return 308 https://$host$request_uri;" in server_blocks[0]
    assert "acme-challenge" in template
    assert "location ~ ^/api/kb/" not in template

    for server in server_blocks:
        assert server.count("location ^~ /api/ {") == 1
        assert "proxy_request_buffering off;" in server
        assert "add_header X-Content-Type-Options nosniff always;" in server
        assert "add_header X-Frame-Options SAMEORIGIN always;" in server
        assert (
            "add_header Referrer-Policy strict-origin-when-cross-origin always;"
            in server
        )
