#!/bin/bash

set -Eeuxo pipefail

# Run this script by running: docker exec -it aa-data-import--web /scripts/load_aac_other_metadata.sh
# Feel free to comment out steps in order to retry failed parts of this script, when necessary.
# Load scripts are idempotent, and can be rerun without losing too much work.

cd /temp-dir/aac_other_metadata

rm -f /file-data/annas_archive_meta__aacid__ebscohost_records*
rm -f /file-data/annas_archive_meta__aacid__cerlalc_records*
rm -f /file-data/annas_archive_meta__aacid__czech_oo42hcks_records*
rm -f /file-data/annas_archive_meta__aacid__gbooks_records*
rm -f /file-data/annas_archive_meta__aacid__goodreads_records*
rm -f /file-data/annas_archive_meta__aacid__isbngrp_records*
rm -f /file-data/annas_archive_meta__aacid__libby_records*
rm -f /file-data/annas_archive_meta__aacid__rgb_records*
rm -f /file-data/annas_archive_meta__aacid__trantor_records*

mv annas_archive_meta__aacid__ebscohost_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__cerlalc_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__czech_oo42hcks_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__gbooks_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__goodreads_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__isbngrp_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__libby_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__rgb_records*.jsonl.seekable.zst /file-data/
mv annas_archive_meta__aacid__trantor_records*.jsonl.seekable.zst /file-data/
