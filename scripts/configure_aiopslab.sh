#!/usr/bin/env bash
set -euo pipefail

repo_path="${AIOPSLAB_PATH:-upstream/AIOpsLab}"
user_name="$(id -un)"
config_path="${repo_path}/aiopslab/config.yml"

printf '%s\n' \
  'k8s_host: kind' \
  "k8s_user: ${user_name}" \
  'ssh_key_path: ~/.ssh/id_rsa' \
  'data_dir: data' \
  'qualitative_eval: false' \
  'print_session: true' > "${config_path}"

echo "Wrote ${config_path}"

