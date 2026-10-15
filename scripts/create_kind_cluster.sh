#!/usr/bin/env bash
set -euo pipefail

config_path="${AIOPSLAB_KIND_CONFIG:-configs/kind-arm-single.yaml}"
cluster_name="${AIOPSLAB_CLUSTER_NAME:-kind}"

docker info >/dev/null
test -f "${config_path}"
if kind get clusters | grep -qx "${cluster_name}"; then
  echo "Kind cluster ${cluster_name} already exists."
else
  kind create cluster --name "${cluster_name}" --config "${config_path}"
fi
docker exec "${cluster_name}-control-plane" mkdir -p /run/udev
kubectl cluster-info --context "kind-${cluster_name}"
kubectl get nodes -o wide

if [[ "${EVIDENCEOPS_PRELOAD_IMAGES:-1}" == "1" ]]; then
  AIOPSLAB_CLUSTER_NAME="${cluster_name}" "$(dirname "$0")/preload_kind_images.sh"
fi
