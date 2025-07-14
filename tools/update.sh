#!/bin/bash

set -euo pipefail

VALID_SUFFIXES=("B" "N" "F" "0" "W" "U" "C" "Q")
VALID_JSON="valid_combinations.json"
TMP_JSON="valid_combinations.tmp.json"
MAX_JOBS=32  # Tune depending on your system

# Check args
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 SM-XXXX [SM-YYYY ...]"
    exit 1
fi

# Get all CSCs once
readarray -t ALL_CSCS < <(jq -r '.CSC | keys[]' "$VALID_JSON")

# Collect successful pairs
FOUND_FILE=$(mktemp)

check_model_csc() {
    local model="$1"
    local csc="$2"
    local url="http://fota-cloud-dn.ospserver.net/firmware/$csc/$model/version.xml"

    if curl --head --silent --fail "$url" > /dev/null; then
        echo "$csc $model" >> "$FOUND_FILE"
        echo "✅ $csc / $model"
    else
        echo "❌ $csc / $model"
    fi
}

export -f check_model_csc
export FOUND_FILE

# Build and run parallel curl jobs
PIDS=()

for base_model in "$@"; do
    for suffix in "${VALID_SUFFIXES[@]}"; do
        full_model="${base_model}${suffix}"

        for csc in "${ALL_CSCS[@]}"; do
            check_model_csc "$full_model" "$csc" &
            PIDS+=($!)

            # Limit number of parallel jobs
            if (( ${#PIDS[@]} >= MAX_JOBS )); then
                wait -n
                PIDS=($(jobs -pr))
            fi
        done
    done
done

# Wait for all remaining background jobs
wait

# Prepare working copy of JSON
cp "$VALID_JSON" "$TMP_JSON"

# Parse results and add to JSON
while read -r csc model; do
    # Find a sample model to clone from
    sample=$(jq -r --arg csc "$csc" '.CSC[$csc] | to_entries | .[0].key // empty' "$TMP_JSON")
    if [[ -z "$sample" ]]; then
        echo "⚠️ Skipping $csc / $model — no existing entries to clone"
        continue
    fi

    jq --arg csc "$csc" --arg model "$model" --arg from "$sample" \
       '.CSC[$csc][$model] = .CSC[$csc][$from]' "$TMP_JSON" > "$TMP_JSON.tmp" && mv "$TMP_JSON.tmp" "$TMP_JSON"

done < "$FOUND_FILE"

# Save result
mv "$TMP_JSON" "$VALID_JSON"
rm -f "$FOUND_FILE"

echo "✅ JSON updated: $VALID_JSON"
