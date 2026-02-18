#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/image.png" >&2
  exit 2
fi

IMAGE="$1"
if [[ ! -f "$IMAGE" ]]; then
  echo "Image not found: $IMAGE" >&2
  exit 1
fi

RATIOS=("1:1" "3:2" "4:5" "9:16" "16:9")

NEGATIVE_PROMPT="text, words, letters, numbers, phrases, captions, watermarks, logos, signatures, writing, added objects, extraneous elements, embellishments, augmented content, altered subject matter, new foreground objects, overlays, borders, frames"

for ratio in "${RATIOS[@]}"; do
  args=$(printf '{"image_path":"%s","aspect_ratio":"%s","negative_prompt":"%s","workflow_id":"aspect_ratio_adjustment"}' "$IMAGE" "$ratio" "$NEGATIVE_PROMPT")
  run=$(./run_mcp_tools.sh call --tool workflows_run_aspect_ratio_adjustment --args "$args")
  pid=$(printf '%s\n' "$run" | sed -n 's/.*"prompt_id": "\([^"]*\)".*/\1/p' | head -n1)
  if [[ -z "$pid" ]]; then
    echo "ratio=$ratio error=missing_prompt_id" >&2
    continue
  fi
  wait=$(./run_mcp_tools.sh call --tool workflows_wait --args "{\"prompt_id\":\"$pid\",\"timeout_s\":900,\"poll_ms\":500}")
  file=$(printf '%s\n' "$wait" | sed -n 's/.*"filename": "\([^"]*\)".*/\1/p' | head -n1)
  echo "ratio=$ratio prompt_id=$pid output=$file"
done
