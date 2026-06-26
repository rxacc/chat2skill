#!/usr/bin/env bash
# Generate hooks.json with absolute paths and refresh known local installs.
set -euo pipefail

OVERWRITE_INSTALLED=1

usage() {
    cat <<'EOF'
Usage: ./install.sh [--overwrite-installed|--no-overwrite-installed]

By default this script refreshes known local installs:
  - ~/plugins/chat2skill
  - ~/.codex/plugins/cache/personal/chat2skill/*
  - ~/.codex/.tmp/marketplaces/.staging/*
  - ~/.claude/plugins/cache/chat2skill/chat2skill/*
  - ~/.claude/plugins/marketplaces/chat2skill

Set CHAT2SKILL_INSTALL_TARGETS to a colon-separated list of extra install
targets to refresh.

The user config file ~/.chat2skill/config.json is created only when missing.
Any config.json inside an existing install target is preserved during refresh.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --overwrite-installed)
            OVERWRITE_INSTALLED=1
            ;;
        --no-overwrite-installed)
            OVERWRITE_INSTALLED=0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

real_path() {
    python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

is_chat2skill_target() {
    local target="$1"
    local manifest

    [ -d "${target}" ] || return 1

    if [ -f "${target}/skills/chat2skill/SKILL.md" ]; then
        return 0
    fi

    for manifest in \
        "${target}/.codex-plugin/plugin.json" \
        "${target}/.claude-plugin/plugin.json" \
        "${target}/.cursor-plugin/plugin.json"
    do
        [ -f "${manifest}" ] || continue
        if python3 - "$manifest" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)

sys.exit(0 if data.get("name") == "chat2skill" else 1)
PY
        then
            return 0
        fi
    done

    return 1
}

write_hooks_json() {
    local target_root="$1"
    cat > "${target_root}/hooks.json" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${target_root}/scripts/hook_user_prompt_submit.py\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${target_root}/scripts/hook_stop.py\""
          },
          {
            "type": "command",
            "command": "python3 \"${target_root}/scripts/hook_stop_response_guard.py\""
          }
        ]
      }
    ]
  }
}
EOF
}

sync_install_target() {
    local target="$1"
    local source_real
    local target_real

    if [ ! -d "${target}" ]; then
        echo "Install target not found, skipped: ${target}"
        return 0
    fi

    source_real="$(real_path "${PLUGIN_ROOT}")"
    target_real="$(real_path "${target}")"
    if [ "${source_real}" = "${target_real}" ]; then
        echo "Install target is current checkout, skipped: ${target}"
        return 0
    fi

    rsync -a --delete \
        --exclude '.git/' \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude '.DS_Store' \
        --exclude 'config.json' \
        "${PLUGIN_ROOT}/" "${target}/"
    write_hooks_json "${target}"
    echo "Overwrote install target: ${target}"
}

sync_child_install_targets() {
    local root="$1"
    local target

    if [ ! -d "${root}" ]; then
        echo "Install target root not found, skipped: ${root}"
        return 0
    fi

    for target in "${root}"/*; do
        [ -d "${target}" ] || continue
        is_chat2skill_target "${target}" || continue
        sync_install_target "${target}"
    done
}

sync_extra_install_targets() {
    local target
    local old_ifs
    local -a extra_targets

    [ -n "${CHAT2SKILL_INSTALL_TARGETS:-}" ] || return 0

    old_ifs="${IFS}"
    IFS=":"
    read -r -a extra_targets <<< "${CHAT2SKILL_INSTALL_TARGETS}"
    IFS="${old_ifs}"

    for target in "${extra_targets[@]}"; do
        [ -n "${target}" ] || continue
        sync_install_target "${target}"
    done
}

write_hooks_json "${PLUGIN_ROOT}"
echo "Wrote ${PLUGIN_ROOT}/hooks.json"

if [ "${OVERWRITE_INSTALLED}" = "1" ]; then
    sync_install_target "${HOME}/plugins/chat2skill"

    sync_child_install_targets "${HOME}/.codex/plugins/cache/personal/chat2skill"
    sync_child_install_targets "${HOME}/.codex/.tmp/marketplaces/.staging"
    sync_child_install_targets "${HOME}/.claude/plugins/cache/chat2skill/chat2skill"
    sync_install_target "${HOME}/.claude/plugins/marketplaces/chat2skill"
    sync_extra_install_targets
else
    echo "Skipped overwrite of known local installs."
fi

CONFIG_DIR="${CHAT2SKILL_HOME:-$HOME/.chat2skill}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
if [ ! -f "${CONFIG_FILE}" ]; then
    mkdir -p "${CONFIG_DIR}"
    cp "${PLUGIN_ROOT}/config.example.json" "${CONFIG_FILE}"
    echo "Created ${CONFIG_FILE} — edit it to set your LLM api key."
else
    echo "Config already exists: ${CONFIG_FILE}"
fi
