#!/usr/bin/env python3
import os
import sys
import tarfile
import hashlib
import json
from datetime import datetime

def compute_md5_fileobj(fobj, chunk_size=128*1024*1024):
    # Compute MD5 hash for a file object
    md5 = hashlib.md5()
    while True:
        chunk = fobj.read(chunk_size)
        if not chunk:
            break
        md5.update(chunk)
    return md5.hexdigest()

def process_tar(path_tar):
    if not os.path.isfile(path_tar):
        print(f"[ERROR] File not found: {path_tar}")
        return 1

    basename = os.path.basename(path_tar)
    torrent_filename = basename if basename.endswith(".torrent") else basename + ".torrent"
    dirbase = os.path.dirname(path_tar) or "."
    output_path = os.path.join(dirbase, "offsets.jsonl")

    try:
        with tarfile.open(path_tar, "r:") as tar, \
             open(output_path, "a", encoding="utf-8") as out_fh:
            idx = 0
            member = tar.next()
            while member is not None:
                if member.isfile():
                    idx += 1
                    print(f"Processing ({idx}): {member.name}")
                    fobj = tar.extractfile(member)
                    if fobj is not None:
                        md5_hash = compute_md5_fileobj(fobj)
                        byte_start = getattr(member, "offset_data", None)
                        record = {
                            "md5": md5_hash,
                            "torrent_filename": torrent_filename,
                            "byte_start": byte_start
                        }
                        out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                member = tar.next()

            if idx > 0:
                print(f"[OK] Done: {idx} files in {path_tar}")
            else:
                print(f"[WARN] No regular files found in: {path_tar}")

        os.remove(path_tar)
        print(f"[INFO] Processed and removed: {path_tar}")
        print(f"[INFO] Offsets added to: {output_path}")
        return 0
    except Exception as e:
        print(f"[ERROR] Processing {path_tar}: {e}")
        return 1

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 script.py /path/to/directory")
        sys.exit(1)

    dir_path = sys.argv[1]
    if not os.path.isdir(dir_path):
        print(f"[ERROR] {dir_path} is not a valid directory")
        sys.exit(1)

    tar_files = [f for f in os.listdir(dir_path) if f.endswith(".tar")]
    if not tar_files:
        print("[INFO] No .tar files found in the directory.")
        sys.exit(0)

    print(f"=== [{datetime.now()}] Starting in: {dir_path} ===")
    for tar_name in tar_files:
        tar_path = os.path.join(dir_path, tar_name)
        print(f"\n=== Processing {tar_name} ===")
        process_tar(tar_path)

if __name__ == "__main__":
    main()
