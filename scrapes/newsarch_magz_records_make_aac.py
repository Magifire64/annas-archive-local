import shortuuid
import datetime
import orjson
from collections import OrderedDict

# unzstd --keep periodicals.2024-06-02.json.zst

timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
output_filename = f"annas_archive_meta__aacid__newsarch_magz_records__{timestamp}--{timestamp}.jsonl"

input_filenames = [
    'periodicals.2024-06-02.json',
]

def process_record(record):
    uuid = shortuuid.uuid()
    return {
        "aacid": f"aacid__newsarch_magz_records__{timestamp}__{uuid}",
        "metadata": {
            "file.path": record['file.path'],
            "md5": record['hash.md5'].lower(),
            **record,
        },
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
