import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_service.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_runtime_bootstrap_seeds_then_syncs_and_reuses_marker(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    seed_dir = tmp_path / "seed"
    venv_dir = tmp_path / "runtime" / "venv"
    fake_bin = tmp_path / "fake-bin"
    uv_log = tmp_path / "uv.log"
    command_log = tmp_path / "command.log"

    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
    (project_dir / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (seed_dir / "bin").mkdir(parents=True)
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    (seed_dir / "lib" / python_version / "site-packages").mkdir(parents=True)
    _write_executable(seed_dir / "bin" / "langgraph", "#!/bin/sh\nexit 0\n")

    fake_bin.mkdir()
    _write_executable(
        fake_bin / "uv",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(uv_log))}\n",
    )

    env = os.environ.copy()
    env.update(
        {
            "LUMEN_RUNTIME_PROJECT_DIR": str(project_dir),
            "LUMEN_RUNTIME_PYTHON_BIN": sys.executable,
            "LUMEN_RUNTIME_VENV_DIR": str(venv_dir),
            "LUMEN_RUNTIME_VENV_SEED_PATH": str(seed_dir),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    command = [
        str(SCRIPT_PATH),
        "test-service",
        "/bin/sh",
        "-c",
        f"printf 'started\\n' >> '{command_log}'",
    ]

    first = subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    second = subprocess.run(command, env=env, check=True, capture_output=True, text=True)

    (venv_dir / "bin" / "langgraph").unlink()
    repaired = subprocess.run(command, env=env, check=True, capture_output=True, text=True)

    (project_dir / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    os.utime(project_dir / "uv.lock", (1, 1))
    resynced = subprocess.run(command, env=env, check=True, capture_output=True, text=True)

    assert f"seeding environment from {seed_dir}" in first.stdout
    assert "dependency sync attempt 1/3" in first.stdout
    assert f"reusing existing environment at {venv_dir}" in second.stdout
    assert f"seeding environment from {seed_dir}" in repaired.stdout
    assert "dependency sync attempt 1/3" in repaired.stdout
    assert "dependency sync attempt 1/3" in resynced.stdout
    assert uv_log.read_text(encoding="utf-8").splitlines() == [
        f"sync --frozen --no-dev --project {project_dir}",
        f"sync --frozen --no-dev --project {project_dir}",
        f"sync --frozen --no-dev --project {project_dir}",
    ]
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "started",
        "started",
        "started",
        "started",
    ]
    assert (venv_dir / ".bootstrap-complete").is_file()


def test_runtime_bootstrap_defaults_to_prebuilt_image_environment() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "LUMEN_RUNTIME_VENV_SEED_PATH:-/opt/insight-flow-venv" in script
