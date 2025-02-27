#!/bin/bash

set -Eeuxo pipefail

# Run this script by running: docker exec -it aa-data-import--web /scripts/download_aac_ia2_acsmpdf_files.sh
# Download scripts are idempotent but will RESTART the download from scratch!

rm -rf /temp-dir/aac_ia2_acsmpdf_files
mkdir /temp-dir/aac_ia2_acsmpdf_files

cd /temp-dir/aac_ia2_acsmpdf_files

curl -C - -O https://annas-archive.li/dyn/torrents/latest_aac_meta/ia2_acsmpdf_files.torrent

if [ -z "${AAC_SFTP_IP:-}" ] || [ -z "${AAC_SFTP_PORT:-}" ] || [ -z "${AAC_SFTP_USERNAME:-}" ] || [ -z "${AAC_SFTP_PASSWORD:-}" ] || [ -z "${AAC_SFTP_REMOTE_PATH:-}" ]; then
    echo "Environment variables not set, proceeding to download via torrent."
    # Proceed to download via webtorrent
    webtorrent --verbose download ia2_acsmpdf_files.torrent || webtorrent --verbose download ia2_acsmpdf_files.torrent || webtorrent --verbose download ia2_acsmpdf_files.torrent
else
    echo "Environment variables are set, attempting to copy files via rclone."
    # Parse the list of files from the torrent file
    webtorrent info ia2_acsmpdf_files.torrent | jq -r '.files[].path' > files_to_include.txt

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