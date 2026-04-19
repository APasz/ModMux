#!/usr/bin/env bash
set -euo pipefail

output_path="${1:-out.json}"
urls_path="${2:-urls.txt}"

uv run modmux --from-urls "$urls_path" --pretty > "$output_path"
