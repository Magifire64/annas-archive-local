import shortuuid
import datetime
import orjson
import pandas as pd
import argparse

def convert_value(value):
    """Convert values to string, handling datetime and other types"""
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, pd.DatetimeTZDtype)):
        return value.strftime("%Y-%m-%d")  # Format date as 'YYYY-MM-DD'
    return str(value).strip()  # Convert to string and strip extra spaces


def excel_to_json(excel_file):
    # Hardcoded values
    sheet_name = "ALL Titles"
    
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    output_filename = f"annas_archive_meta__aacid__bloomsbury_records__{timestamp}--{timestamp}.jsonl"

    # Specify the columns to treat as strings (ISBNs)
    dtype_columns = {
        "ONLINE ISBN": str,
        "HB ISBN": str,
        "PB ISBN": str,
        "EPUB ISBN": str,
        "PDF EBOOK ISBN": str,
    }

    # Read the specified sheet from the Excel file, ensuring ISBNs are strings
    df = pd.read_excel(
        excel_file, sheet_name=sheet_name, engine="openpyxl", dtype=dtype_columns
    )

    # Convert DataFrame to JSON
    with open(output_filename, "wb") as f:
        for _, row in df.iterrows():
            uuid = shortuuid.uuid()
            f.write(orjson.dumps({
                "aacid": f"aacid__bloomsbury_records__{timestamp}__{uuid}",
                "metadata": {col: convert_value(val) for col, val in row.items()},
            }, option=orjson.OPT_APPEND_NEWLINE))

    print(f"Data from sheet '{sheet_name}' has been successfully saved to {output_filename}")

parser = argparse.ArgumentParser(description="Convert 'Title List' Excel file from https://www.bloomsburycollections.com/for-librarians to JSON")
parser.add_argument(
    "excel_file", help="Path to the 'Title List' Excel file from https://www.bloomsburycollections.com/for-librarians"
)
args = parser.parse_args()
excel_to_json(args.excel_file)
