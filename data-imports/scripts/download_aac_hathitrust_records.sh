#!/bin/bash

set -Eeuxo pipefail

# Run this script by running: docker exec -it aa-data-import--web /scripts/download_hathitrust.sh
# Download scripts are idempotent but will RESTART the download from scratch!

rm -rf /temp-dir/hathitrust_records
mkdir /temp-dir/hathitrust_records

cd /temp-dir/hathitrust_records

wget 'https://www.hathitrust.org/files/hathifiles/hathi_file_list.json'
DOWNLOAD_URL=$(jq -r '[.[]| select(.full == true)| select(.filename | startswith("hathi_full_"))]| sort_by(.filename)| last| .url' hathi_file_list.json)

aria2c -o 'hathi_full.txt.gz' -c -x4 -s4 -j4 "$DOWNLOAD_URL"
