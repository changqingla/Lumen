#!/usr/bin/env bash

detect_sandbox_mode() {
  local config_path="${PROJECT_ROOT:-.}/config.yaml"
  if [ ! -f "$config_path" ]; then
    echo "local"
    return 0
  fi

  local provider
  provider="$(
    sed -E 's/[[:space:]]+#.*$//' "$config_path" \
      | awk '
          /^[[:space:]]*use:[[:space:]]*/ {
            sub(/^[[:space:]]*use:[[:space:]]*/, "", $0)
            gsub(/["'\'']/, "", $0)
            print $0
            exit
          }
        '
  )"

  case "$provider" in
    *AioSandboxProvider*)
      if sed -E 's/[[:space:]]+#.*$//' "$config_path" | grep -Eq '^[[:space:]]*provisioner_url:[[:space:]]*[^[:space:]]+'; then
        echo "provisioner"
      else
        echo "aio"
      fi
      ;;
    *LocalSandboxProvider*)
      echo "local"
      ;;
    *)
      echo "local"
      ;;
  esac
}
