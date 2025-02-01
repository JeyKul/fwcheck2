
#!/bin/bash

# Log file
log_file=~/fw.log

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

# CSC codes and their corresponding files
declare -A csc_files
csc_files["AUT"]="./csc/eux.txt"
csc_files["EUX"]="./csc/eux.txt"
csc_files["DBT"]="./csc/eux.txt"
csc_files["BTB"]="./csc/eux.txt"
csc_files["VIP"]="./csc/eux.txt"
csc_files["KOO"]="./csc/koo.txt"
csc_files["SKC"]="./csc/koo.txt"
csc_files["KTC"]="./csc/koo.txt"
csc_files["LUC"]="./csc/koo.txt"
csc_files["TMK"]="./csc/tmk.txt"
csc_files["TMB"]="./csc/tmk.txt"
csc_files["DSH"]="./csc/tmk.txt"
csc_files["DSA"]="./csc/tmk.txt"
csc_files["ATT"]="./csc/tmk.txt"
csc_files["VZW"]="./csc/tmk.txt"
csc_files["SPR"]="./csc/tmk.txt"
csc_files["XID"]="./csc/eux.txt"
csc_files["TUR"]="./csc/eux.txt"

# Temporary file for processing commands
cmd_file=$(mktemp)

# Function to process each model
process_model() {
    local csc=$1
    local model=$2

    # Fetch latest version via curl
    local latest_version=$(curl --retry 5 --retry-delay 5 -s "http://fota-cloud-dn.ospserver.net/firmware/$csc/$model/version.xml" | grep latest | sed 's/^[^>]*>//' | sed 's/<.*//')

    # Check if we got a valid response
    if [ -z "$latest_version" ]; then
        echo "log:Firmware: $model CSC:$csc not found"
        return
    fi

    # Determine action based on current version
    if [ -f "current.$csc.$model" ]; then
        local current_version=$(cat "current.$csc.$model")
        if [ "$current_version" != "$latest_version" ]; then
            echo "$latest_version" > "current.$csc.$model"
            echo "add:current.$csc.$model"
            echo "commit:$csc/$model: updated to $latest_version"
            echo "log:Firmware: $model CSC:$csc updated to $latest_version"
        fi
    else
        echo "$latest_version" > "current.$csc.$model"
        echo "add:current.$csc.$model"
        echo "commit:$csc/$model: created with $latest_version"
        echo "log:Firmware: $model CSC:$csc created with version $latest_version"
    fi
}

export -f process_model

# Start timer
start_time=$(date +%s)

# Generate all CSC-model pairs
csp_model_list=$(mktemp)
for csc in "${!csc_files[@]}"; do
    csc_file=${csc_files[$csc]}
    
    if [ ! -f "$csc_file" ]; then
        echo "log:$csc_file not found for CSC $csc" | tee -a "$log_file"
        continue
    fi

    grep -vE '^#|^$' "$csc_file" | while read -r model; do
        echo "$csc $model"
    done
done > "$csp_model_list"

# Process all pairs in parallel
parallel_args=()
[ -n "$max_cores" ] && parallel_args+=("-j" "$max_cores")

cat "$csp_model_list" | parallel "${parallel_args[@]}" --colsep ' ' process_model {1} {2} > "$cmd_file"

# Process accumulated commands
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

# Push changes once at the end
git push


# Cleanup temporary files
rm "$cmd_file" "$csp_model_list"

# End timer and calculate duration
end_time=$(date +%s)
duration=$((end_time - start_time))
echo "Finished in $duration seconds." | tee -a "$log_file"
