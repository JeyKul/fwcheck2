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

    # Fetch XML content
    xml_response=$(curl --retry 5 --retry-delay 5 -s "http://fota-cloud-dn.ospserver.net/firmware/$csc/$model/version.xml")
    xml_line=$(echo "$xml_response" | grep -m 1 '<latest')

    # Extract latest version and Android version, sanitize spaces
    latest_version=$(echo "$xml_line" | sed -e 's/<latest[^>]*>//' -e 's/<\/latest>//' | tr -s '[:space:]' ' ' | sed 's/^ *//;s/ *$//')
    android_version=$(echo "$xml_line" | sed -n 's/.* o="\([^"]*\)".*/\1/p')

    # Check if we got a valid response
    if [ -z "$latest_version" ]; then
        echo "log:Firmware: $model CSC:$csc not found"
        return
    fi

    # Determine action based on current version
    if [ -f "current.$csc.$model" ]; then
        current_version=$(head -n1 "current.$csc.$model")
        if [ "$current_version" != "$latest_version" ]; then
            # Write latest version and Android version if present
            echo "$latest_version" > "current.$csc.$model"
            if [ -n "$android_version" ]; then
                echo "ANDROID_VERSION=$android_version" >> "current.$csc.$model"
            fi
            echo "add:current.$csc.$model"
            commit_msg="$csc/$model: updated to $latest_version"
            [ -n "$android_version" ] && commit_msg+=" (Android $android_version)"
            commit_msg=$(echo "$commit_msg" | tr -s '[:space:]' ' ' | sed 's/^ *//;s/ *$//')
            echo "commit:$commit_msg"
            echo "log:Firmware: $model CSC:$csc updated to $latest_version"
        fi
    else
        echo "$latest_version" > "current.$csc.$model"
        if [ -n "$android_version" ]; then
            echo "ANDROID_VERSION=$android_version" >> "current.$csc.$model"
        fi
        echo "add:current.$csc.$model"
        commit_msg="$csc/$model: created with $latest_version"
        [ -n "$android_version" ] && commit_msg+=" (Android $android_version)"
        commit_msg=$(echo "$commit_msg" | tr -s '[:space:]' ' ' | sed 's/^ *//;s/ *$//')
        echo "commit:$commit_msg"
        echo "log:Firmware: $model CSC:$csc created with version $latest_version"
    fi
}

export -f process_model

# Start timer
start_time=$(date +%s)

# Generate CSC-model pairs from JSON
csp_model_list=$(mktemp)
for csc in $(jq -r 'keys[]' "$json_file"); do
    models=$(jq -r ".\"$csc\" | keys[]" "$json_file")
    for model in $models; do
        echo "$csc $model"
    done
done > "$csp_model_list"

# Run processing in parallel
parallel_args=()
[ -n "$max_cores" ] && parallel_args+=("-j" "$max_cores")

cat "$csp_model_list" | parallel "${parallel_args[@]}" --colsep ' ' process_model {1} {2} > "$cmd_file"

# Handle commands
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
