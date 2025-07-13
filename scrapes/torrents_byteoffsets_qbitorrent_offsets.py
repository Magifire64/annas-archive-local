#!/usr/bin/env python3
"""
Invocation from qBittorrent (normal mode):
    /usr/bin/python3 "/mnt/2tb/scihub/qb.py" "%F" "%N" "%I"
"""

import sys
import os
import logging
import shutil
import argparse
import hashlib
import json
import zipfile
import tarfile
from struct import unpack

import qbittorrentapi
import bencodepy 
import libtorrent as lt

# --- Configuration ---
TORRENTS_DIR = "/mnt/2/scihub/torrents"      
OUTPUT_JSONL = "/mnt/2/scihub/offsets.jsonl"
LOG_PATH     = "/mnt/2/scihub/qb_process.log"
QBT_HOST     = "localhost:8080"
QBT_USER     = "admin"
QBT_PASS     = "qbpass"
# ---------------------

def setup_logging():
    log_dir = os.path.dirname(LOG_PATH)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s"
    )
    # Also log errors to stderr for manual testing
    ch = logging.StreamHandler()
    ch.setLevel(logging.ERROR)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logging.getLogger().addHandler(ch)

def md5_of_fileobj(fobj):
    """Compute the MD5 of a file-like object in chunks."""
    m = hashlib.md5()
    for chunk in iter(lambda: fobj.read(1024*1024), b''):
        m.update(chunk)
    return m.hexdigest()

def get_zip_data_offset(zip_path, zi):
    """
    Returns the absolute offset (within the ZIP) where the raw file data of 'zi' starts.
    """
    with open(zip_path, 'rb') as zf:
        zf.seek(zi.header_offset)
        local_file_header = zf.read(30)  # Fixed header size
        if len(local_file_header) != 30:
            raise ValueError("Failed to read complete local file header")

        # Unpack the local file header (see APPNOTE.TXT)
        signature, ver, flag, comp, modtime, moddate, crc32, comp_size, uncomp_size, \
            fname_len, extra_len = unpack('<IHHHHHIIIHH', local_file_header)
        if signature != 0x04034b50:
            raise ValueError("Invalid local file header signature")
        offset = zi.header_offset + 30 + fname_len + extra_len
        return offset

def extract_offsets(torrent_path, downloads_dir, output_handle, torrent_basename):
    """
    Processes the files listed in the torrent and writes JSONL with:
      md5, torrent_filename, byte_start
    If a file is compressed (ZIP entry), also adds: "compressed": true, "compress_size": <int>
    If reading/extracting a file fails, md5 will be "CORRUPT:<filename>"
    """
    info = lt.torrent_info(torrent_path)
    files = info.files()
    cumulative = 0
    base = downloads_dir
    prefix = info.name()           # e.g., "50700000"
    torrent_fname = torrent_basename

    print(f"[extract_offsets] Processing {torrent_fname} with {files.num_files()} files...")

    for idx in range(files.num_files()):
        relative_path = files.file_path(idx)
        # Remove prefix if present
        if prefix and relative_path.startswith(prefix + os.sep):
            rel_stripped = relative_path[len(prefix) + 1:]
        else:
            rel_stripped = relative_path

        size = files.file_size(idx)
        fullpath = os.path.join(base, rel_stripped) if rel_stripped else base

        if not os.path.isfile(fullpath):
            print(f"[WARN] Not found: {fullpath}")
            cumulative += size
            continue

        print(f"[extract_offsets] File {idx+1}/{files.num_files()}: {rel_stripped or prefix} (size={size})")

        # ZIP file
        if fullpath.endswith('.zip'):
            try:
                with zipfile.ZipFile(fullpath, 'r') as zf:
                    for zi in zf.infolist():
                        if zi.is_dir():
                            continue
                        offset = get_zip_data_offset(fullpath, zi)
                        try:
                            with zf.open(zi) as entry:
                                h = md5_of_fileobj(entry)
                        except Exception as e:
                            h = f"CORRUPT:{zi.filename}"
                        record = {
                            "md5": h,
                            "torrent_filename": torrent_fname,
                            "byte_start": offset
                        }
                        if zi.compress_type != 0:
                            record["compressed"] = True
                            record["compress_size"] = zi.compress_size
                        output_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        output_handle.flush()
            except Exception as e:
                print(f"[ERROR] ZIP {fullpath}: {e}")

        # TAR file
        elif fullpath.endswith('.tar'):
            try:
                with tarfile.open(fullpath, 'r:') as tf:
                    for ti in tf:
                        if not ti.isfile():
                            continue
                        offset = cumulative + ti.offset_data
                        try:
                            entry = tf.extractfile(ti)
                            if entry is None:
                                raise Exception("extractfile returned None")
                            h = md5_of_fileobj(entry)
                        except Exception as e:
                            h = f"CORRUPT:{ti.name}"
                        record = {
                            "md5": h,
                            "torrent_filename": torrent_fname,
                            "byte_start": offset
                        }
                        output_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        output_handle.flush()
            except Exception as e:
                print(f"[ERROR] TAR {fullpath}: {e}")

        # Regular file
        else:
            try:
                with open(fullpath, 'rb') as fh:
                    h = md5_of_fileobj(fh)
            except Exception as e:
                h = f"CORRUPT:{os.path.basename(fullpath)}"
            offset = cumulative
            record = {
                "md5": h,
                "torrent_filename": torrent_fname,
                "byte_start": offset
            }
            output_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            output_handle.flush()

        cumulative += size

def find_torrent_file(info_hash, torrent_name):
    # 1) Try by info_hash
    p = os.path.join(TORRENTS_DIR, f"{info_hash}.torrent")
    if os.path.isfile(p):
        logging.info(f"Found .torrent by hash: {p}")
        return p
    # 2) Try by torrent_name
    p = os.path.join(TORRENTS_DIR, f"{torrent_name}.torrent")
    if os.path.isfile(p):
        logging.info(f"Found .torrent by name: {p}")
        return p
    # 3) Scan and compare info.name field
    for fname in os.listdir(TORRENTS_DIR):
        if not fname.endswith(".torrent"):
            continue
        full = os.path.join(TORRENTS_DIR, fname)
        try:
            data = bencodepy.decode_from_file(full)
            info = data.get(b"info", {})
            name = info.get(b"name", b"").decode('utf-8', errors='ignore')
            if name == torrent_name:
                logging.info(f"Found .torrent by info.name: {full}")
                return full
        except Exception as e:
            logging.warning(f"Could not read {full}: {e}")
    logging.error(f"No .torrent found for hash={info_hash} or name={torrent_name}")
    return None

def delete_torrent_via_api(info_hash):
    client = qbittorrentapi.Client(host=QBT_HOST, username=QBT_USER, password=QBT_PASS)
    client.auth_log_in()
    client.torrents.delete(delete_files=True, torrent_hashes=info_hash)

def manual_delete_path(path):
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            logging.info(f"Manually deleted folder: {path}")
        except Exception as e:
            logging.error(f"Error deleting {path} manually: {e}")
    else:
        logging.info(f"content_path does not exist (already deleted): {path}")

def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Process a completed torrent; with --test it does not delete anything.")
    parser.add_argument('--test', action='store_true', help="Only process offsets, do not delete anything")
    parser.add_argument('content_path', help="Download path, e.g. /mnt/2tb/scihub/downloads/50700000")
    parser.add_argument('torrent_name', help="Torrent name, e.g. 50700000")
    parser.add_argument('info_hash', help="Torrent info hash")
    args = parser.parse_args()

    content_path = args.content_path
    torrent_name = args.torrent_name
    info_hash = args.info_hash
    test_mode = args.test

    logging.info(f"Start processing: name={torrent_name}, hash={info_hash}, path={content_path}, test_mode={test_mode}")

    if not os.path.isdir(content_path):
        logging.error(f"content_path does not exist or is not a directory: {content_path}")
        sys.exit(1)

    # 1) Locate .torrent
    torrent_file = find_torrent_file(info_hash, torrent_name)
    if torrent_file:
        # 2) Process offsets
        try:
            os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)
            with open(OUTPUT_JSONL, "a", encoding="utf-8") as out_f:
                extract_offsets(torrent_file, content_path, out_f, os.path.basename(torrent_file))
            logging.info(f"extract_offsets OK in {content_path}")
        except Exception as e:
            logging.error(f"Error in extract_offsets: {e}")
    else:
        logging.error("Skipping extract_offsets (missing .torrent)")

    if test_mode:
        logging.info("TEST MODE: No deletion of torrent or files will be performed.")
        return

    # 3) Delete torrent + files via API
    api_ok = True
    try:
        delete_torrent_via_api(info_hash)
        logging.info(f"Torrent deleted via API: {info_hash}")
    except Exception as e:
        api_ok = False
        logging.error(f"API deletion failed: {e}")

    # 4) If API failed or files remain, delete manually
    if not api_ok or os.path.exists(content_path):
        manual_delete_path(content_path)

    logging.info(f"Finished processing for {torrent_name} ({info_hash})")

if __name__ == "__main__":
    main()
