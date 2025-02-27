#!/bin/bash

set -Eeuxo pipefail

# Run this script by running: docker exec -it aa-data-import--web /scripts/download_aac_other_metadata.sh
# Download scripts are idempotent but will RESTART the download from scratch!

rm -rf /temp-dir/aac_other_metadata
mkdir /temp-dir/aac_other_metadata

cd /temp-dir/aac_other_metadata

curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/ebscohost_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/cerlalc_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/czech_oo42hcks_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/gbooks_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/goodreads_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/isbngrp_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/libby_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/rgb_records.torrent
curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/trantor_records.torrent

if [ -z "${AAC_SFTP_IP:-}" ] || [ -z "${AAC_SFTP_PORT:-}" ] || [ -z "${AAC_SFTP_USERNAME:-}" ] || [ -z "${AAC_SFTP_PASSWORD:-}" ] || [ -z "${AAC_SFTP_REMOTE_PATH:-}" ]; then
    echo "Environment variables not set, proceeding to download via torrent."
    # Proceed to download via webtorrent
    webtorrent --verbose download ebscohost_records.torrent || webtorrent --verbose download ebscohost_records.torrent || webtorrent --verbose download ebscohost_records.torrent
    webtorrent --verbose download cerlalc_records.torrent || webtorrent --verbose download cerlalc_records.torrent || webtorrent --verbose download cerlalc_records.torrent
    webtorrent --verbose download czech_oo42hcks_records.torrent || webtorrent --verbose download czech_oo42hcks_records.torrent || webtorrent --verbose download czech_oo42hcks_records.torrent
    webtorrent --verbose download gbooks_records.torrent || webtorrent --verbose download gbooks_records.torrent || webtorrent --verbose download gbooks_records.torrent
    webtorrent --verbose download goodreads_records.torrent || webtorrent --verbose download goodreads_records.torrent || webtorrent --verbose download goodreads_records.torrent
    webtorrent --verbose download isbngrp_records.torrent || webtorrent --verbose download isbngrp_records.torrent || webtorrent --verbose download isbngrp_records.torrent
    webtorrent --verbose download libby_records.torrent || webtorrent --verbose download libby_records.torrent || webtorrent --verbose download libby_records.torrent
    webtorrent --verbose download rgb_records.torrent || webtorrent --verbose download rgb_records.torrent || webtorrent --verbose download rgb_records.torrent
    webtorrent --verbose download trantor_records.torrent || webtorrent --verbose download trantor_records.torrent || webtorrent --verbose download trantor_records.torrent
else
    echo "Environment variables are set, attempting to copy files via rclone."
    # Parse the list of files from the torrent file
    webtorrent info other_metadata.torrent | jq -r '.files[].path' > files_to_include.txt

    # Obscure the SFTP password
    SFTP_PASS_OBSCURED=$(rclone obscure "${AAC_SFTP_PASSWORD}")

    # Perform the copy using rclone
    rclone copy \
        :sftp:"${AAC_SFTP_REMOTE_PATH}" \
        . \
        --sftp-host="${AAC_SFTP_IP}" \
        --sftp-port="${AAC_SFTP_PORT}" \
        --sftp-user="${AAC_SFTP_USERNAME}" \
        --sftp-pass="${SFTP_PASS_OBSCURED}" \
        --multi-thread-streams=60 \
        --transfers=60 \
        --checksum \
        --no-unicode-normalization \
        --check-first \
        --include-from files_to_include.txt
fi