import glob
import orjson
import shortuuid
import datetime
import uuid
import shortuuid


# {"md5": "21f2f92cf999f9d8d7577b7923343b1a", "torrent_filename": "annas-archive-ia-acsm-a.tar.torrent", "byte_start": 115651584}
# {"md5":"ec0731a139a942e27d84e5dc2e76b7b1","torrent_filename":"sm_00000000-00099999.torrent","byte_start":1835279}

# For some:
# "compressed":true,"compress_size":325800

# Or:
# "md5":"CORRUPT:10.1145/2413076.2413091.pdf"

timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
output_file = f"annas_archive_meta__aacid__torrents_byteoffsets_records__{timestamp}--{timestamp}.jsonl"

namespace = uuid.UUID('67ead0d7-9c7f-4a22-a379-24e31d49308e')

json_files = glob.glob('*.jsonl')
if len(json_files) == 0:
    raise Exception("No JSON files found")
with open(output_file, 'wb') as outfile:
    for filename in json_files:
        print(f"Processing {filename=}")
        with open(filename, 'r') as infile:
            for line in infile:
                line = line.strip()
                if not line:
                    continue  # Skip empty lines

                try:
                    json_obj = orjson.loads(line)
                except Exception as err:
                    print(f"Exception parsing JSON: {line=}")
                    raise

                if not ((len(json_obj['md5']) == 32) or (json_obj['md5'].startswith('CORRUPT:'))):
                    raise Exception(f"Invalid md5: {json_obj['md5']=}")
                metadata_json = {
                    "md5": json_obj["md5"],
                    **json_obj,
                }

                record_uuid = shortuuid.encode(uuid.uuid5(namespace, orjson.dumps(metadata_json).decode()))
                outfile.write(orjson.dumps({
                    "aacid": f"aacid__torrents_byteoffsets_records__{timestamp}__{record_uuid}",
                    "metadata": metadata_json,
                }, option=orjson.OPT_APPEND_NEWLINE))
