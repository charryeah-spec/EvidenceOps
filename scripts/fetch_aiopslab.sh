#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_path="${AIOPSLAB_PATH:-${project_root}/upstream/AIOpsLab}"
commit_file="${project_root}/AIOPSLAB_COMMIT"
patch_file="${project_root}/patches/aiopslab-safe-helm-paths.patch"
app_patch_file="${project_root}/patches/aiopslab-applications-arm64-cn.patch"

if [[ ! -s "${commit_file}" ]]; then
  echo "Missing pinned commit file: ${commit_file}" >&2
  exit 1
fi

pinned_commit="$(tr -d '[:space:]' < "${commit_file}")"
if [[ ! -d "${repo_path}/.git" ]]; then
  git clone https://github.com/microsoft/AIOpsLab.git "${repo_path}"
fi
git -C "${repo_path}" fetch origin "${pinned_commit}"
git -C "${repo_path}" checkout --detach "${pinned_commit}"
git -C "${repo_path}" submodule update --init aiopslab-applications

if git -C "${repo_path}" apply --check "${patch_file}" >/dev/null 2>&1; then
  git -C "${repo_path}" apply "${patch_file}"
elif ! git -C "${repo_path}" apply --reverse --check "${patch_file}" >/dev/null 2>&1; then
  echo "AIOpsLab compatibility patch is neither applicable nor already applied." >&2
  exit 1
fi

if git -C "${repo_path}/aiopslab-applications" apply --check "${app_patch_file}" >/dev/null 2>&1; then
  git -C "${repo_path}/aiopslab-applications" apply "${app_patch_file}"
elif ! git -C "${repo_path}/aiopslab-applications" apply --reverse --check "${app_patch_file}" >/dev/null 2>&1; then
  echo "AIOpsLab applications compatibility patch is neither applicable nor already applied." >&2
  exit 1
fi

actual_commit="$(git -C "${repo_path}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${pinned_commit}" ]]; then
  echo "Expected ${pinned_commit}, got ${actual_commit}" >&2
  exit 1
fi
echo "AIOpsLab ${actual_commit} is ready at ${repo_path}"
