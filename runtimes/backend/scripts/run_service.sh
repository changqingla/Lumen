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
project_dir="${LUMEN_RUNTIME_PROJECT_DIR:-/app/backend}"
seed_dir="${LUMEN_RUNTIME_VENV_SEED_PATH:-/opt/insight-flow-venv}"
python_bin="${LUMEN_RUNTIME_PYTHON_BIN:-}"

if [ -z "${python_bin}" ]; then
  python_command="$(command -v python3 || true)"
  if [ -n "${python_command}" ]; then
    python_bin="$(readlink -f "${python_command}")"
  fi
fi

if [ ! -x "${python_bin}" ]; then
  echo "A system Python interpreter is required to bootstrap the Runtime environment" >&2
  exit 1
fi

python_version="$("${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
mkdir -p "${venv_dir}"

unset VIRTUAL_ENV
export UV_PROJECT_ENVIRONMENT="${venv_dir}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/app/state/.uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}"
export PATH="${venv_dir}/bin:${PATH}"

sync_attempts="${LUMEN_RUNTIME_SYNC_ATTEMPTS:-3}"
sync_retry_delay="${LUMEN_RUNTIME_SYNC_RETRY_DELAY_SECONDS:-5}"

case "${sync_attempts}" in
  ""|*[!0-9]*|0)
    echo "LUMEN_RUNTIME_SYNC_ATTEMPTS must be a positive integer" >&2
    exit 1
    ;;
esac

case "${sync_retry_delay}" in
  ""|*[!0-9]*)
    echo "LUMEN_RUNTIME_SYNC_RETRY_DELAY_SECONDS must be a non-negative integer" >&2
    exit 1
    ;;
esac

seed_runtime_env() {
  seed_has_site_packages=false
  for site_packages in "${seed_dir}"/lib*/"python${python_version}"/site-packages; do
    if [ -d "${site_packages}" ]; then
      seed_has_site_packages=true
      break
    fi
  done

  if [ ! -d "${seed_dir}/bin" ] || [ "${seed_has_site_packages}" != true ]; then
    return 1
  fi

  echo "[runtime-bootstrap:${service_name}] seeding environment from ${seed_dir}"
  rm -rf "${venv_dir}"
  mkdir -p "${venv_dir}"
  cp -a "${seed_dir}/." "${venv_dir}/"

  ln -sf "${python_bin}" "${venv_dir}/bin/python"
  ln -sf python "${venv_dir}/bin/python3"
  ln -sf python "${venv_dir}/bin/python${python_version}"

  if [ -f "${venv_dir}/pyvenv.cfg" ]; then
    python_home="$(dirname "${python_bin}")"
    sed -i "s#^home = .*#home = ${python_home}#" "${venv_dir}/pyvenv.cfg"
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
          "${venv_dir}"/bin/python*)
            ;;
          */bin/python*)
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
import httpx
import langgraph
import psycopg
import uvicorn
PY

  "${venv_dir}/bin/langgraph" --help >/dev/null 2>&1
}

sync_runtime_env() {
  attempt=1
  while [ "${attempt}" -le "${sync_attempts}" ]; do
    echo "[runtime-bootstrap:${service_name}] dependency sync attempt ${attempt}/${sync_attempts}"
    if env -i \
      HOME="${HOME:-/root}" \
      PATH="${PATH}" \
      LANG="${LANG:-C.UTF-8}" \
      LC_ALL="${LC_ALL:-}" \
      SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" \
      SSL_CERT_DIR="${SSL_CERT_DIR:-/etc/ssl/certs}" \
      REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}" \
      HTTP_PROXY="${HTTP_PROXY:-}" \
      HTTPS_PROXY="${HTTPS_PROXY:-}" \
      NO_PROXY="${NO_PROXY:-}" \
      http_proxy="${http_proxy:-}" \
      https_proxy="${https_proxy:-}" \
      no_proxy="${no_proxy:-}" \
      UV_CACHE_DIR="${UV_CACHE_DIR}" \
      UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT}" \
      UV_LINK_MODE="${UV_LINK_MODE}" \
      UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT}" \
      UV_INDEX_URL="${UV_INDEX_URL:-}" \
      UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-}" \
      UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-}" \
      UV_INSECURE_HOST="${UV_INSECURE_HOST:-}" \
      UV_NATIVE_TLS="${UV_NATIVE_TLS:-false}" \
      uv sync --frozen --no-dev --project "${project_dir}"; then
      return 0
    fi

    if [ "${attempt}" -eq "${sync_attempts}" ]; then
      echo "[runtime-bootstrap:${service_name}] dependency sync exhausted ${sync_attempts} attempt(s)" >&2
      return 1
    fi

    echo "[runtime-bootstrap:${service_name}] dependency sync failed; retrying in ${sync_retry_delay}s" >&2
    sleep "${sync_retry_delay}"
    attempt=$((attempt + 1))
  done
}

bootstrap_fingerprint() {
  {
    cksum "${project_dir}/uv.lock" "${project_dir}/pyproject.toml" "$0"
    "${python_bin}" -c 'import sys; print(sys.implementation.name, sys.version, sys.abiflags)'
    printf 'seed=%s\n' "${seed_dir}"
  } | cksum | awk '{ print $1 ":" $2 }'
}

for project_file in "${project_dir}/uv.lock" "${project_dir}/pyproject.toml"; do
  if [ ! -f "${project_file}" ]; then
    echo "Runtime project file not found: ${project_file}" >&2
    exit 1
  fi
done

expected_fingerprint="$(bootstrap_fingerprint)"

exec 9>"${lock_file}"
flock 9

recorded_fingerprint=""
if [ -f "${bootstrap_marker}" ]; then
  recorded_fingerprint="$(cat "${bootstrap_marker}")"
fi

if [ "${recorded_fingerprint}" != "${expected_fingerprint}" ] || ! runtime_env_ready; then
  if ! runtime_env_ready; then
    seed_runtime_env || true
  fi

  echo "[runtime-bootstrap:${service_name}] syncing dependencies into ${venv_dir}"
  sync_runtime_env

  if ! runtime_env_ready; then
    echo "[runtime-bootstrap:${service_name}] environment validation failed after bootstrap" >&2
    exit 1
  fi

  marker_temp="${bootstrap_marker}.tmp.$$"
  printf '%s\n' "${expected_fingerprint}" > "${marker_temp}"
  mv "${marker_temp}" "${bootstrap_marker}"
else
  echo "[runtime-bootstrap:${service_name}] reusing existing environment at ${venv_dir}"
fi

flock -u 9
exec 9>&-

echo "[runtime-bootstrap:${service_name}] starting $*"
cd "${project_dir}"
exec "$@"
