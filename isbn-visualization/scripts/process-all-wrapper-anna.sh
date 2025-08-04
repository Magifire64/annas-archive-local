#!/bin/bash
set -euo pipefail

export DATA_DIR=/app/data
rm -rf $DATA_DIR/*

export PUBLIC_BASE_PATH=/isbn-visualization
export INPUT_ISBNGRP_DUMP=/file-data/annas_archive_meta__aacid__isbngrp_records__20240920T194930Z--20240920T194930Z.jsonl.seekable.zst
export INPUT_WORLDCAT_DUMP=/file-data/annas_archive_meta__aacid__worldcat__20241230T203056Z--20241230T203056Z.jsonl.seekable.zst

export OUTPUT_DIR_PUBLIC=/file-data/isbn-visualization/$(date +%s)
mkdir -p OUTPUT_DIR_PUBLIC

export INPUT_BENC=$(ls /exports/codes_benc/aa_isbn13_codes_*T*.benc.zst 2>/dev/null | sort | tail -n 1)


./process-all.sh
