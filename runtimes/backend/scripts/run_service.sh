#!/bin/sh

set -eu

service_name="${1:?service name is required}"
shift

if [ "$#" -eq 0 ]; then
  echo "usage: run_service.sh <service-name> <command> [args...]" >&2
  exit 1
fi

venv_dir="${LUMEN_RUNTIME_VENV_DIR:-/app/state/runtime-venv}"
lock_file="${venv_dir}.lock"
bootstrap_marker="${venv_dir}/.bootstrap-complete"
seed_dir="${LUMEN_RUNTIME_VENV_SEED_PATH:-/app/backend/.venv}"
mkdir -p "${venv_dir}"

unset VIRTUAL_ENV
export UV_PROJECT_ENVIRONMENT="${venv_dir}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/app/state/.uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"
export PATH="${venv_dir}/bin:${PATH}"

seed_runtime_env() {
  if [ ! -d "${seed_dir}/bin" ] || [ ! -d "${seed_dir}/lib/python3.12/site-packages" ]; then
    return 1
  fi

  echo "[runtime-bootstrap:${service_name}] seeding environment from ${seed_dir}"
  rm -rf "${venv_dir}"
  mkdir -p "${venv_dir}"
  cp -a "${seed_dir}/." "${venv_dir}/"

  ln -sf /usr/local/bin/python3.12 "${venv_dir}/bin/python"
  ln -sf python "${venv_dir}/bin/python3"
  ln -sf python "${venv_dir}/bin/python3.12"

  if [ -f "${venv_dir}/pyvenv.cfg" ]; then
    sed -i 's#^home = .*#home = /usr/local/bin#' "${venv_dir}/pyvenv.cfg"
  fi

  for script in "${venv_dir}"/bin/*; do
    if [ ! -f "${script}" ]; then
      continue
    fi

    first_line="$(head -n 1 "${script}" 2>/dev/null || true)"
    case "${first_line}" in
      '#!'*)
        interpreter="${first_line#\#!}"
        python_path="$(printf '%s\n' "${interpreter}" | awk '{print $1}')"
        python_args="$(printf '%s\n' "${interpreter}" | cut -s -d ' ' -f2-)"

        case "${python_path}" in
          "${venv_dir}/bin/python"|\
          "${venv_dir}/bin/python3"|\
          "${venv_dir}/bin/python3.12")
            ;;
          */bin/python|\
          */bin/python3|\
          */bin/python3.12)
            tmp_file="$(mktemp)"
            {
              if [ -n "${python_args}" ]; then
                printf '%s\n' "#!${venv_dir}/bin/python ${python_args}"
              else
                printf '%s\n' "#!${venv_dir}/bin/python"
              fi
              tail -n +2 "${script}"
            } > "${tmp_file}"
            cat "${tmp_file}" > "${script}"
            rm -f "${tmp_file}"
            chmod +x "${script}"
            ;;
        esac
        ;;
    esac
  done

  return 0
}

runtime_env_ready() {
  if [ ! -x "${venv_dir}/bin/python" ]; then
    return 1
  fi

  if [ ! -x "${venv_dir}/bin/langgraph" ]; then
    return 1
  fi

  "${venv_dir}/bin/python" - <<'PY' >/dev/null 2>&1
import fastapi
import langgraph
PY

  "${venv_dir}/bin/langgraph" --help >/dev/null 2>&1
}

exec 9>"${lock_file}"
flock 9

if [ ! -f "${bootstrap_marker}" ] || [ /app/backend/uv.lock -nt "${bootstrap_marker}" ] || [ /app/backend/pyproject.toml -nt "${bootstrap_marker}" ] || [ "$0" -nt "${bootstrap_marker}" ]; then
  if ! runtime_env_ready; then
    seed_runtime_env || true
  fi

  if runtime_env_ready; then
    echo "[runtime-bootstrap:${service_name}] seeded environment is ready at ${venv_dir}"
  else
    echo "[runtime-bootstrap:${service_name}] syncing dependencies into ${venv_dir}"
    uv sync --frozen --no-dev --project /app/backend
  fi

  if ! runtime_env_ready; then
    echo "[runtime-bootstrap:${service_name}] environment validation failed after bootstrap" >&2
    exit 1
  fi

  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${bootstrap_marker}"
else
  echo "[runtime-bootstrap:${service_name}] reusing existing environment at ${venv_dir}"
fi

flock -u 9
exec 9>&-

echo "[runtime-bootstrap:${service_name}] starting $*"
cd /app/backend
exec "$@"
