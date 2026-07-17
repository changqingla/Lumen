from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INITIALIZER = ROOT / "docker" / "init-env.sh"
REQUIRED_SECRETS = {
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "RAG_REDIS_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "GATEWAY_INTERNAL_API_TOKEN",
    "RAG_INTERNAL_API_TOKEN",
    "MODEL_RESOLVER_INTERNAL_TOKEN",
    "SANDBOX_PROVISIONER_INTERNAL_TOKEN",
}
INDEPENDENT_SECRETS = REQUIRED_SECRETS - {
    "POSTGRES_PASSWORD",
    "MINIO_ROOT_PASSWORD",
}


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _run_initializer(env_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(INITIALIZER), str(env_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _valid_legacy_secrets() -> dict[str, str]:
    return {
        "POSTGRES_PASSWORD": "existing-postgres-password",
        "REDIS_PASSWORD": "Existing_Redis_Password_0123456789",
        "MINIO_ROOT_PASSWORD": "existing-minio-password",
    }


def test_initializer_upgrades_existing_env_without_rotating_valid_secrets(tmp_path):
    env_path = tmp_path / ".env"
    preserved = _valid_legacy_secrets()
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in preserved.items()) + "\n",
        encoding="utf-8",
    )

    first = _run_initializer(env_path)
    first_values = _read_env(env_path)

    assert REQUIRED_SECRETS <= first_values.keys()
    assert all(first_values[key] == value for key, value in preserved.items())
    assert len({first_values[key] for key in INDEPENDENT_SECRETS}) == len(
        INDEPENDENT_SECRETS
    )
    for key in REQUIRED_SECRETS - preserved.keys():
        assert re.fullmatch(r"[0-9a-f]{64}", first_values[key])
        assert first_values[key] not in first.stdout
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    second = _run_initializer(env_path)

    assert _read_env(env_path) == first_values
    assert "no values were changed" in second.stdout


def test_initializer_creates_env_from_template_and_replaces_placeholders(tmp_path):
    env_path = tmp_path / "new.env"

    result = _run_initializer(env_path)
    values = _read_env(env_path)

    assert REQUIRED_SECRETS <= values.keys()
    assert all(re.fullmatch(r"[0-9a-f]{64}", values[key]) for key in REQUIRED_SECRETS)
    assert len({values[key] for key in INDEPENDENT_SECRETS}) == len(
        INDEPENDENT_SECRETS
    )
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "values were not printed" in result.stdout
    assert not any(values[key] in result.stdout for key in REQUIRED_SECRETS)


def test_initializer_is_executable_and_does_not_depend_on_current_directory():
    assert INITIALIZER.stat().st_mode & stat.S_IXUSR
    assert os.access(INITIALIZER, os.X_OK)


def test_initializer_uses_compose_dotenv_semantics_for_quoted_values(tmp_path):
    env_path = tmp_path / ".env"
    values = _valid_legacy_secrets()
    values.update(
        {
            "RAG_REDIS_PASSWORD": '""',
            "GATEWAY_INTERNAL_API_TOKEN": '"replace-with-a-strong-random-gateway-token"',
            "RAG_INTERNAL_API_TOKEN": "'replace-with-a-strong-random-rag-token'",
            "MODEL_RESOLVER_INTERNAL_TOKEN": '""',
            "SANDBOX_PROVISIONER_INTERNAL_TOKEN": '"replace-with-a-strong-random-sandbox-provisioner-token"',
        }
    )
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )

    result = _run_initializer(env_path)
    initialized = _read_env(env_path)

    for key in INDEPENDENT_SECRETS - {"REDIS_PASSWORD"}:
        assert re.fullmatch(r"[0-9a-f]{64}", initialized[key])
    assert "Initialized 5 missing deployment secret(s)" in result.stdout


def test_initializer_detects_equal_values_after_compose_expansion(tmp_path):
    env_path = tmp_path / ".env"
    values = _valid_legacy_secrets()
    shared_token = "a" * 64
    values.update(
        {
            "RAG_REDIS_PASSWORD": "b" * 64,
            "GATEWAY_INTERNAL_API_TOKEN": shared_token,
            "RAG_INTERNAL_API_TOKEN": '"${GATEWAY_INTERNAL_API_TOKEN}"',
            "MODEL_RESOLVER_INTERNAL_TOKEN": "c" * 64,
            "SANDBOX_PROVISIONER_INTERNAL_TOKEN": "d" * 64,
        }
    )
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(INITIALIZER), str(env_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must use independent values" in result.stderr


def test_initializer_does_not_rotate_existing_persistent_credentials(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "POSTGRES_PASSWORD=change-me\n"
        "REDIS_PASSWORD=replace-with-a-strong-random-lumen-redis-password\n"
        "MINIO_ROOT_PASSWORD=change-me\n",
        encoding="utf-8",
    )

    result = _run_initializer(env_path)
    initialized = _read_env(env_path)

    assert initialized["POSTGRES_PASSWORD"] == "change-me"
    assert initialized["MINIO_ROOT_PASSWORD"] == "change-me"
    assert re.fullmatch(r"[0-9a-f]{64}", initialized["REDIS_PASSWORD"])
    assert "persistent credentials cannot be rotated automatically" in result.stderr
