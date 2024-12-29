import shortuuid
import datetime
import orjson
from collections import OrderedDict

# unzstd --keep *.seekable.zst

timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
output_filename = f"annas_archive_meta__aacid__hentai_records__{timestamp}--{timestamp}.jsonl"

input_filenames = [
    'annas_archive_meta__aacid__upload_files_erotic__20241017T050546Z--20241017T055721Z.jsonl.seekable',
    'annas_archive_meta__aacid__upload_files_erotic__20241020T155304Z--20241020T172225Z.jsonl.seekable',
    'annas_archive_meta__aacid__upload_files_erotic__20241023T050044Z--20241023T063154Z.jsonl.seekable',
    'annas_archive_meta__aacid__upload_files_erotic__20241023T064658Z--20241023T081650Z.jsonl.seekable',
    'annas_archive_meta__aacid__upload_files_erotic__20241023T165214Z--20241023T191453Z.jsonl.seekable',
    'annas_archive_meta__aacid__upload_files_erotic__20241023T234350Z--20241024T024020Z.jsonl.seekable',
]

def process_record(record):
    aacid = record['aacid']
    metadata = record['metadata']
    ordered_record = OrderedDict()

    # Add 'old_aacid' with value from 'aacid'
    ordered_record['old_aacid'] = aacid

    # Add 'md5' next if it exists in metadata
    if 'md5' in metadata:
        ordered_record['md5'] = metadata['md5']

    # Add 'id' first if it exists in metadata
    if 'id' in metadata:
        ordered_record['id'] = metadata['id']

    # Add the rest of the metadata keys, excluding 'id' and 'md5'
    for key in metadata:
        if key not in ('id', 'md5'):
            ordered_record[key] = metadata[key]

    uuid = shortuuid.uuid()
    return {
        "aacid": f"aacid__hentai_records__{timestamp}__{uuid}",
        "metadata": dict(ordered_record),
    }

with open(output_filename, 'wb') as outfile:
    for filename in input_filenames:
        with open(filename, 'r', encoding='utf-8') as infile:
            for line in infile:
                line = line.strip()
                if not line:
                    continue  # Skip empty lines
                try:
                    record = orjson.loads(line)
                    ordered_record = process_record(record)
                    outfile.write(orjson.dumps(ordered_record, option=orjson.OPT_APPEND_NEWLINE))
                except json.JSONDecodeError as e:
                    print(f"Skipping invalid JSON line in {filename}: {e}")
                    continue
