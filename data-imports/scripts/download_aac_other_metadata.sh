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

# Tried ctorrent and aria2, but webtorrent seems to work best overall.
webtorrent --verbose download ebscohost_records.torrent || webtorrent --verbose download ebscohost_records.torrent || webtorrent --verbose download ebscohost_records.torrent
webtorrent --verbose download cerlalc_records.torrent || webtorrent --verbose download cerlalc_records.torrent || webtorrent --verbose download cerlalc_records.torrent
webtorrent --verbose download czech_oo42hcks_records.torrent || webtorrent --verbose download czech_oo42hcks_records.torrent || webtorrent --verbose download czech_oo42hcks_records.torrent
webtorrent --verbose download gbooks_records.torrent || webtorrent --verbose download gbooks_records.torrent || webtorrent --verbose download gbooks_records.torrent
webtorrent --verbose download goodreads_records.torrent || webtorrent --verbose download goodreads_records.torrent || webtorrent --verbose download goodreads_records.torrent
webtorrent --verbose download isbngrp_records.torrent || webtorrent --verbose download isbngrp_records.torrent || webtorrent --verbose download isbngrp_records.torrent
webtorrent --verbose download libby_records.torrent || webtorrent --verbose download libby_records.torrent || webtorrent --verbose download libby_records.torrent
webtorrent --verbose download rgb_records.torrent || webtorrent --verbose download rgb_records.torrent || webtorrent --verbose download rgb_records.torrent
webtorrent --verbose download trantor_records.torrent || webtorrent --verbose download trantor_records.torrent || webtorrent --verbose download trantor_records.torrent
