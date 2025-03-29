import gzip
import orjson
import os
import uuid
import shortuuid
import subprocess
import datetime
import pairtree
from tqdm import tqdm

input_file = "/temp-dir/hathitrust_records/hathi_full.txt.gz"
temp_output_file = "annas_archive_meta__aacid__hathitrust_records__temp.jsonl"
namespace = uuid.UUID('8c39c613-64dd-42ea-a49e-25e0af52d8de')

earliest_dt = None
latest_dt = None

with open(temp_output_file, "wb") as out_f:
    with gzip.open(input_file, "rt", encoding="utf-8") as in_f:
        for line in tqdm(in_f, desc="Processing lines"):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 26:
                print(f"Warning: malformed line: {line=}")
                continue

            htid_part1, htid_part2 = fields[0].strip().split('.', 1);
            pairtree_filename = "/".join([htid_part1, 'pairtree_root', pairtree.id2path(htid_part2), pairtree.id_encode(htid_part2), pairtree.id_encode(htid_part2) + '.zip'])
            
            # Build the metadata dictionary (1:1 with TSV columns)
            metadata_dict = {
                "htid": fields[0],
                "pairtree_filename": pairtree_filename,
                "access": fields[1],
                "rights": fields[2],
                "ht_bib_key": fields[3],
                "description": fields[4],
                "source": fields[5],
                "source_bib_num": fields[6],
                "oclc_num": fields[7],
                "isbn": fields[8],
                "issn": fields[9],
                "lccn": fields[10],
                "title": fields[11],
                "imprint": fields[12],
                "rights_reason_code": fields[13],
                "rights_timestamp": fields[14],
                "us_gov_doc_flag": fields[15],
                "rights_date_used": fields[16],
                "pub_place": fields[17],
                "lang": fields[18],
                "bib_fmt": fields[19],
                "collection_code": fields[20],
                "content_provider_code": fields[21],
                "responsible_entity_code": fields[22],
                "digitization_agent_code": fields[23],
                "access_profile_code": fields[24],
                "author": fields[25],
            }

            
            dt = datetime.datetime.strptime(metadata_dict["rights_timestamp"], "%Y-%m-%d %H:%M:%S")
            if earliest_dt is None or dt < earliest_dt:
                earliest_dt = dt
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
            
            timestamp_formatted = dt.strftime("%Y%m%dT%H%M%SZ")
            unique_id = shortuuid.encode(uuid.uuid5(namespace, orjson.dumps(metadata_dict).decode()))
            aacid = f"aacid__hathitrust_records__{timestamp_formatted}__{unique_id}"
            
            out_f.write(orjson.dumps({
                "aacid": aacid,
                "metadata": metadata_dict
            }, option=orjson.OPT_APPEND_NEWLINE))

earliest_str = earliest_dt.strftime("%Y%m%dT%H%M%SZ")
latest_str = latest_dt.strftime("%Y%m%dT%H%M%SZ")
compressed_full_path = f"/file-data/annas_archive_meta__aacid__hathitrust_records__{earliest_str}--{latest_str}.jsonl.seekable.zst"
if os.path.exists(compressed_full_path):
    raise Exception(f"Path already exists: {compressed_full_path=}")

# t2sz {input} -l 11 -s 10M -T 32 -o {output}
subprocess.run(['t2sz', temp_output_file, '-l', '11', '-s', '10M', '-T', '32', '-o', compressed_full_path], check=True)
os.remove(temp_output_file)
print(f"Generated {compressed_full_path}")
