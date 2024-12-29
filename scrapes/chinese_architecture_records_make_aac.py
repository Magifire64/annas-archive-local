import csv
import shortuuid
import datetime
import orjson
from collections import OrderedDict

timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
output_filename = f"annas_archive_meta__aacid__chinese_architecture_records__{timestamp}--{timestamp}.jsonl"

with open('metadata.csv', 'r', encoding='utf-8', newline='') as csvfile, \
     open(output_filename, 'wb') as outfile:
    # Read the CSV file using DictReader
    reader = csv.DictReader(csvfile)
    for row in reader:
        uuid = shortuuid.uuid()
        output_json = {
            "aacid": f"aacid__chinese_architecture_records__{timestamp}__{uuid}",
            "metadata": {
                'Relative Path': row['Relative Path'],
                **row,
            },
        }
        outfile.write(orjson.dumps(output_json, option=orjson.OPT_APPEND_NEWLINE))
