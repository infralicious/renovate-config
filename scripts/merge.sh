#!/bin/sh
# This script is to merge all the renovate configs into one file for local
# testing of configurations because of renovate's limitation on the inability
# to retrieve the convention of 'local>' or 'github>' external files.
# See https://docs.renovatebot.com/modules/platform/local/#limitations

# create dir
BUILD_DIR="build"
OUTPUT_FILE="merged.json"
BUILD_FILE="$BUILD_DIR/$OUTPUT_FILE"

mkdir -p "$BUILD_DIR/"

# strip comments from json
sed 's,\/\/ .*,,g' -i ./*.json

# for each json file follow this convention to slurp and merge
# '.[0] * .[1] * .[n]'
jq_string=""
json_file_count=$(find . -maxdepth 1 -name '*.json' | wc -l)
for i in $(seq 0 "$((json_file_count-1))")
do
  jq_string="${jq_string:+${jq_string} * }.[${i}]"
done

# combine all files into one
jq -s "${jq_string}" ./*.json | jq '. | del(.extends[] | select(contains("github>")))' > "$BUILD_FILE"
