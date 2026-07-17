#!/bin/sh
set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${1:-"${script_dir}/.env"}
example_file="${script_dir}/.env.example"
env_preexisted=false

case "$env_file" in
  /*) ;;
  *) env_file="$(pwd)/${env_file}" ;;
esac

if [ -e "$env_file" ]; then
  env_preexisted=true
else
  if [ ! -f "$example_file" ]; then
    echo "Compose environment template not found: $example_file" >&2
    exit 1
  fi
  cp "$example_file" "$env_file"
fi

if [ ! -f "$env_file" ]; then
  echo "Compose environment path is not a regular file: $env_file" >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to initialize deployment secrets" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is required to parse deployment environment values" >&2
  exit 1
fi

secret_keys="
POSTGRES_PASSWORD
REDIS_PASSWORD
RAG_REDIS_PASSWORD
MINIO_ROOT_PASSWORD
GATEWAY_INTERNAL_API_TOKEN
RAG_INTERNAL_API_TOKEN
MODEL_RESOLVER_INTERNAL_TOKEN
SANDBOX_PROVISIONER_INTERNAL_TOKEN
"

independent_keys="
REDIS_PASSWORD
RAG_REDIS_PASSWORD
GATEWAY_INTERNAL_API_TOKEN
RAG_INTERNAL_API_TOKEN
MODEL_RESOLVER_INTERNAL_TOKEN
SANDBOX_PROVISIONER_INTERNAL_TOKEN
"

internal_token_keys="
GATEWAY_INTERNAL_API_TOKEN
RAG_INTERNAL_API_TOKEN
MODEL_RESOLVER_INTERNAL_TOKEN
SANDBOX_PROVISIONER_INTERNAL_TOKEN
"

temp_file=""
value_file=""
compose_probe_file=$(mktemp "${env_file}.compose.XXXXXX")
cleanup() {
  rm -f "${temp_file:-}" "${value_file:-}" "${compose_probe_file:-}"
}
trap cleanup 0 1 2 3 15
printf 'services: {}\n' > "$compose_probe_file"

load_resolved_environment() {
  if ! resolved_environment=$(
    env -i \
      PATH="$PATH" \
      HOME="${HOME:-/tmp}" \
      DOCKER_CONFIG="${DOCKER_CONFIG:-${HOME:-/tmp}/.docker}" \
      docker compose \
        --env-file "$env_file" \
        -f "$compose_probe_file" \
        config --environment
  ); then
    echo "Failed to parse Compose environment file: $env_file" >&2
    exit 1
  fi
}

read_value() {
  printf '%s\n' "$resolved_environment" | awk -v key="$1" '
    index($0, key "=") == 1 {
      value = substr($0, length(key) + 2)
      found = 1
    }
    END {
      if (found) {
        printf "%s", value
      }
    }
  '
}

valid_internal_token() {
  value=$1
  if [ -z "$value" ] || [ "$value" != "$(printf '%s' "$value" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')" ]; then
    return 1
  fi
  if [ "${#value}" -lt 32 ]; then
    return 1
  fi
  case "$value" in
    *[!\ -~]*) return 1 ;;
  esac

  lowered=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
  case "$lowered" in
    change-me*|replace-with-*|example*|template*|your-*) return 1 ;;
  esac
  return 0
}

valid_redis_password() {
  value=$1
  if [ "${#value}" -lt 32 ]; then
    return 1
  fi
  case "$value" in
    *[!A-Za-z0-9_-]*) return 1 ;;
  esac
  return 0
}

needs_generation() {
  key=$1
  value=$(read_value "$key")

  if [ "$env_preexisted" = false ]; then
    case "$value" in
      ""|change-me|replace-with-*) return 0 ;;
    esac
  fi

  case "$key" in
    REDIS_PASSWORD|RAG_REDIS_PASSWORD)
      case "$key:$value" in
        REDIS_PASSWORD:replace-with-a-strong-random-lumen-redis-password|\
        RAG_REDIS_PASSWORD:replace-with-an-independent-random-rag-redis-password)
          return 0
          ;;
      esac
      ! valid_redis_password "$value"
      ;;
    GATEWAY_INTERNAL_API_TOKEN|RAG_INTERNAL_API_TOKEN|MODEL_RESOLVER_INTERNAL_TOKEN|SANDBOX_PROVISIONER_INTERNAL_TOKEN)
      ! valid_internal_token "$value"
      ;;
    POSTGRES_PASSWORD|MINIO_ROOT_PASSWORD)
      [ -z "$value" ] && [ "$env_preexisted" = false ]
      ;;
    *)
      return 1
      ;;
  esac
}

write_value_from_file() {
  key=$1
  source_file=$2
  temp_file=$(mktemp "${env_file}.tmp.XXXXXX")
  awk -v key="$key" -v source_file="$source_file" '
    BEGIN {
      if ((getline value < source_file) < 0) {
        exit 1
      }
      close(source_file)
    }
    {
      candidate = $0
      sub(/^[[:space:]]*/, "", candidate)
      sub(/^export[[:space:]]+/, "", candidate)
    }
    candidate ~ ("^" key "[[:space:]]*=") {
      if (!written) {
        print key "=" value
        written = 1
      }
      next
    }
    { print }
    END {
      if (!written) {
        print key "=" value
      }
    }
  ' "$env_file" > "$temp_file"
  chmod 600 "$temp_file"
  mv "$temp_file" "$env_file"
  temp_file=""
}

generate_value() {
  key=$1
  value_file=$(mktemp "${env_file}.value.XXXXXX")
  openssl rand -hex 32 > "$value_file"
  write_value_from_file "$key" "$value_file"
  rm -f "$value_file"
  value_file=""
}

load_resolved_environment
generated_count=0
for key in $secret_keys; do
  if needs_generation "$key"; then
    generate_value "$key"
    generated_count=$((generated_count + 1))
  fi
done

chmod 600 "$env_file"
load_resolved_environment

for key in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD; do
  value=$(read_value "$key")
  if [ -z "$value" ]; then
    echo "$key is missing; existing persistent-service credentials cannot be generated safely" >&2
    exit 1
  fi
done

minio_password=$(read_value MINIO_ROOT_PASSWORD)
if [ "${#minio_password}" -lt 8 ]; then
  echo "MINIO_ROOT_PASSWORD must be at least 8 characters" >&2
  exit 1
fi

for key in REDIS_PASSWORD RAG_REDIS_PASSWORD; do
  value=$(read_value "$key")
  if ! valid_redis_password "$value"; then
    echo "$key must contain at least 32 ASCII letters, digits, underscores, or hyphens" >&2
    exit 1
  fi
done

for key in $internal_token_keys; do
  value=$(read_value "$key")
  if ! valid_internal_token "$value"; then
    echo "$key must be a random printable ASCII token of at least 32 characters" >&2
    exit 1
  fi
done

if ! (
  for key in $independent_keys; do
    read_value "$key"
    printf '\n'
  done
) | awk 'seen[$0]++ { exit 1 }'; then
  echo "Redis passwords and internal service tokens must use independent values" >&2
  exit 1
fi

if [ "$env_preexisted" = true ]; then
  for key in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD; do
    value=$(read_value "$key")
    if [ "$value" = "change-me" ]; then
      echo "Warning: $key still uses the template value; it was preserved because persistent credentials cannot be rotated automatically." >&2
    fi
  done
fi

if [ "$generated_count" -gt 0 ]; then
  echo "Initialized $generated_count missing deployment secret(s) in $env_file; values were not printed."
else
  echo "Deployment secrets are already initialized in $env_file; no values were changed."
fi
