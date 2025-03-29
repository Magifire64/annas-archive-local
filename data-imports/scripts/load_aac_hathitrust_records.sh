#!/bin/bash

set -Eeuxo pipefail

# Run this script by running: docker exec -it aa-data-import--web /scripts/load_aac_hathitrust_records.sh
# Feel free to comment out steps in order to retry failed parts of this script, when necessary.
# Load scripts are idempotent, and can be rerun without losing too much work.

cd /temp-dir/hathitrust_records

rm -f /file-data/annas_archive_meta__aacid__hathitrust_records__*
PYTHONIOENCODING=UTF8:ignore python3 /scripts/helpers/convert_hathitrust_records_to_aac.py
