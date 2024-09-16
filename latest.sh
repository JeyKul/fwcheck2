#!/bin/bash

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

# Function to process each model
process_model() {
    csc=$1
    model=$2
    latest_version=$(curl --retry 5 --retry-delay 5 "http://fota-cloud-dn.ospserver.net/firmware/$csc/$model/version.xml" | grep latest | sed 's/^[^>]*>//' | sed 's/<.*//')
    if [ -z "$latest_version" ]; then
        echo "Failed to fetch version for $csc/$model"
        return
    fi

    if [ -f "current.$csc.$model" ]; then
        current_version=$(cat "current.$csc.$model")
        if [ "$current_version" != "$latest_version" ]; then
            echo "$latest_version" > "current.$csc.$model"
            git add "current.$csc.$model"
            git commit -m "$csc/$model: updated to $latest_version"
        fi
    else
        echo "$latest_version" > "current.$csc.$model"
        git add "current.$csc.$model"
        git commit -m "$csc/$model: created with $latest_version"
    fi
}

export -f process_model

# Start timer
start_time=$(date +%s)

# Main loop
for csc in "${!csc_files[@]}"
do
    csc_file=${csc_files[$csc]}
    if [ ! -f "$csc_file" ]; then
        echo "$csc_file not found for CSC $csc"
        continue
    fi
    # Use parallel to process models
    if [ ! -z "$max_cores" ]; then
        cat "$csc_file" | parallel -j $max_cores process_model $csc
    else
        while IFS= read -r model; do
            process_model $csc $model
        done < "$csc_file"
    fi
done

# Push changes
git push

# End timer and calculate duration
end_time=$(date +%s)
duration=$((end_time - start_time))
echo "Finished in $duration seconds."
