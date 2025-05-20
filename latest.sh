#!/bin/bash

# Log file
log_file=~/fw.log

# JSON file containing the models and CSCs
json_file="valid_combinations.json"

# Use getopts for command line arguments
while getopts ":m:" opt; do
  case ${opt} in
    m )
      max_cores=$OPTARG
      ;;
    \? )
      echo "Usage: cmd [-m number_of_cores]"
      exit 1
      ;;
  esac
done

# Temporary file for processing commands
cmd_file=$(mktemp)

# Function to process each model
process_model() {
    local csc=$1
    local model=$2
    local url="http://fota-cloud-dn.ospserver.net/firmware/$csc/$model/version.xml"

    xml_response=$(curl --retry 5 --retry-delay 5 -s "$url")
    latest_version=$(echo "$xml_response" | xmllint --xpath 'string(//version/latest)' - 2>/dev/null)
    android_version=$(echo "$xml_response" | xmllint --xpath 'string(//version/latest/@o)' - 2>/dev/null)

    # Validate format: should be like X/Y/Z
    if [[ ! "$latest_version" =~ [A-Z0-9]+\/[A-Z0-9]+\/[A-Z0-9]+ ]]; then
        echo "log:Firmware: $model CSC:$csc has invalid or missing latest version"
        return
    fi

    file="current.$csc.$model"
    tmp_file=$(mktemp)

    echo "$latest_version" > "$tmp_file"
    [ -n "$android_version" ] && echo "ANDROID_VERSION=$android_version" >> "$tmp_file"

    if [ -f "$file" ]; then
        if ! cmp -s "$file" "$tmp_file"; then
            mv "$tmp_file" "$file"
            echo "add:$file"
            commit_msg="$csc/$model: updated to $latest_version"
            [ -n "$android_version" ] && commit_msg+=" (Android $android_version)"
            echo "commit:$(echo "$commit_msg" | xargs)"
            echo "log:Firmware: $model CSC:$csc updated to $latest_version"
        else
            rm "$tmp_file"
            echo "log:Firmware: $model CSC:$csc is already up-to-date"
        fi
    else
        mv "$tmp_file" "$file"
        echo "add:$file"
        commit_msg="$csc/$model: created with $latest_version"
        [ -n "$android_version" ] && commit_msg+=" (Android $android_version)"
        echo "commit:$(echo "$commit_msg" | xargs)"
        echo "log:Firmware: $model CSC:$csc created with version $latest_version"
    fi
}

export -f process_model

# Start timer
start_time=$(date +%s)

# Generate CSC-model pairs from JSON
csp_model_list=$(mktemp)
for csc in $(jq -r '.CSC | keys[]' "$json_file"); do
    models=$(jq -r ".CSC[\"$csc\"] | keys[]" "$json_file")
    for model in $models; do
        echo "$csc $model"
    done
done > "$csp_model_list"


# Run processing in parallel
parallel_args=()
[ -n "$max_cores" ] && parallel_args+=("-j" "$max_cores")

cat "$csp_model_list" | parallel "${parallel_args[@]}" --colsep ' ' process_model {1} {2} > "$cmd_file"

# Handle output commands
while read -r line; do
    case $line in
        add:*)
            file="${line#add:}"
            git add "$file"
            ;;
        commit:*)
            message="${line#commit:}"
            git commit -m "$message"
            ;;
        log:*)
            log_msg="${line#log:}"
            echo "$log_msg" | tee -a "$log_file"
            ;;
    esac
done < "$cmd_file"

# Push all commits
git push

# Cleanup
rm "$cmd_file" "$csp_model_list"

# Report duration
end_time=$(date +%s)
duration=$((end_time - start_time))
echo "Finished in $duration seconds." | tee -a "$log_file"
