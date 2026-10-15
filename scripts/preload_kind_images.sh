#!/usr/bin/env bash
set -euo pipefail

cluster_name="${AIOPSLAB_CLUSTER_NAME:-kind}"
node_name="${cluster_name}-control-plane"
mirror_prefix="${EVIDENCEOPS_DOCKER_MIRROR:-docker.1ms.run}"
tmp_dir="$(mktemp -d)"
archive_path="${tmp_dir}/evidenceops-arm64-images.tar"
node_archive="/evidenceops-arm64-images.tar"

cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

pull_with_fallback() {
  local target="$1"
  local fallback="$2"

  if docker pull "${target}"; then
    return
  fi
  echo "Primary pull failed for ${target}; trying ${fallback}."
  docker pull "${fallback}"
  docker tag "${fallback}" "${target}"
}

docker exec "${node_name}" mkdir -p /run/udev

pull_with_fallback \
  "openebs/node-disk-exporter:2.1.0" \
  "${mirror_prefix}/openebs/node-disk-exporter:2.1.0"
pull_with_fallback \
  "openebs/linux-utils:3.5.0" \
  "${mirror_prefix}/openebs/linux-utils:3.5.0"
pull_with_fallback \
  "quay.io/prometheus/node-exporter:v1.7.0" \
  "${mirror_prefix}/quay.io/prometheus/node-exporter:v1.7.0"
pull_with_fallback \
  "yinfangchen/hotelreservation:latest" \
  "${mirror_prefix}/yinfangchen/hotelreservation:latest"

docker save -o "${archive_path}" \
  openebs/node-disk-exporter:2.1.0 \
  openebs/linux-utils:3.5.0 \
  quay.io/prometheus/node-exporter:v1.7.0 \
  yinfangchen/hotelreservation:latest
docker cp "${archive_path}" "${node_name}:${node_archive}"
docker exec "${node_name}" ctr --namespace=k8s.io images import \
  --platform linux/arm64 --no-unpack "${node_archive}"
docker exec "${node_name}" rm -f "${node_archive}"

echo "Kind arm64 prerequisites and images are ready."
