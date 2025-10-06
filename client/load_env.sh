#!/bin/bash

# NOTE! Run this with `source client/load_env.sh` to load env vars into current shell
# afterwards, start the client with `python client/client.py`

load_env() {
    local env_file="${1:-.env}"  # Default to .env if no arg given
    if [[ -f "$env_file" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            # Skip empty lines and comments
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            # Match KEY=VALUE (trim whitespace around =)
            if [[ "$line" =~ ^[[:space:]]*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.*)$ ]]; then
                local key="${BASH_REMATCH[1]}"
                local value="${BASH_REMATCH[2]}"
                # Trim trailing whitespace from value
                local trailing="${value##*[![:space:]]}"
                value="${value%"$trailing"}"
                export "$key"="$value"
                echo "Loaded: $key=$value"  # Optional: verbose output
            fi
        done < "$env_file"
    else
        echo "Error: $env_file not found."
        return 1
    fi
}

load_env
