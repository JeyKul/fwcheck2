
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
csc_files["AUT"]="aut.txt"
csc_files["EUX"]="eux.txt"
csc_files["DBT"]="dbt.txt"
csc_files["BTB"]="btb.txt"
csc_files["VIP"]="vip.txt"
csc_files["KOO"]="koo.txt"
csc_files["SKC"]="koo.txt"
csc_files["KTC"]="koo.txt"
csc_files["LUC"]="koo.txt"
csc_files["TMK"]="tmk.txt"
csc_files["TMB"]="tmk.txt"
csc_files["DSH"]="tmk.txt"
csc_files["DSA"]="tmk.txt"
csc_files["ATT"]="tmk.txt"
csc_files["VZW"]="tmk.txt"
csc_files["SPR"]="tmk.txt"
csc_files["XID"]="xid.txt"

# Function to process each model
process_model() {
    csc=$1
    model=$2

    # Fetch latest version via curl
    latest_version=$(curl --retry 5 --retry-delay 5 -s "http://fota-cloud-dn.ospserver.net/firmware/$csc/$model/version.xml" | grep latest | sed 's/^[^>]*>//' | sed 's/<.*//')

    # Check if we got a valid response
    if [ -z "$latest_version" ]; then
        echo "Firmware: $model CSC:$csc not found" | tee -a "$log_file"
        return
    fi

    # If current version exists, compare with latest
    if [ -f "current.$csc.$model" ]; then
        current_version=$(cat "current.$csc.$model")
        if [ "$current_version" != "$latest_version" ]; then
            echo "$latest_version" > "current.$csc.$model"
            git add "current.$csc.$model"
            git commit -m "$csc/$model: updated to $latest_version"
            echo "Firmware: $model CSC:$csc updated to $latest_version" | tee -a "$log_file"
        fi
    else
        # Create a new version file if it doesn't exist
        echo "$latest_version" > "current.$csc.$model"
        git add "current.$csc.$model"
        git commit -m "$csc/$model: created with $latest_version"
        echo "Firmware: $model CSC:$csc created with version $latest_version" | tee -a "$log_file"
    fi
}

export -f process_model

# Start timer
start_time=$(date +%s)

# Main loop to process each CSC
for csc in "${!csc_files[@]}"
do
    csc_file=${csc_files[$csc]}

    # Skip if CSC file doesn't exist
    if [ ! -f "$csc_file" ]; then
        echo "$csc_file not found for CSC $csc" | tee -a "$log_file"
        continue
    fi

    # Remove comments (#) and empty lines, then process each model
    if [ ! -z "$max_cores" ]; then
        grep -vE '^#|^$' "$csc_file" | parallel -j "$max_cores" process_model $csc
    else
        grep -vE '^#|^$' "$csc_file" | while IFS= read -r model; do
            process_model $csc "$model"
        done
    fi
done

# Push changes once at the end
git push

# End timer and calculate duration
end_time=$(date +%s)
duration=$((end_time - start_time))
echo "Finished in $duration seconds." | tee -a "$log_file"
