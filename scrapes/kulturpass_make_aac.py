import glob
import orjson
import shortuuid
import datetime

timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
output_file = f"annas_archive_meta__aacid__kulturpass_records__{timestamp}--{timestamp}.jsonl"

json_files = glob.glob('metadata/*.json')
with open(output_file, 'wb') as outfile:
    for filename in json_files:
        with open(filename, 'rb') as infile:
            data = infile.read()
            json_obj = orjson.loads(data)
            uuid = shortuuid.uuid()
            outfile.write(orjson.dumps({
                "aacid": f"aacid__kulturpass_records__{timestamp}__{uuid}",
                "metadata": {
                    "code": json_obj['code'],
                    **json_obj,
                },
            }, option=orjson.OPT_APPEND_NEWLINE))
