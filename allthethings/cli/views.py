import os
import orjson
import re
import isbnlib
import collections
import tqdm
import concurrent
import elasticsearch.helpers
import time
import pathlib
import traceback
import flask_mail
import click
import pymysql.cursors
import more_itertools
import indexed_zstd
import hashlib
import zstandard
import datetime
import io
import base64

import allthethings.utils

from flask import Blueprint
from allthethings.extensions import engine, mariadb_url_no_timeout, mail, mariapersist_url, mariapersist_engine
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pymysql.constants import CLIENT
from config.settings import SLOW_DATA_IMPORTS

from allthethings.page.views import get_aarecords_internal_mysql, get_isbndb_dicts

cli = Blueprint("cli", __name__, template_folder="templates")

#################################################################################################
# ./run flask cli dbreset
@cli.cli.command('dbreset')
def dbreset():
    print("Erasing entire database (2 MariaDB databases servers + 1 ElasticSearch)! Did you double-check that any production/large databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 2 seconds to abort..")
    time.sleep(2)

    mariapersist_reset_internal()
    nonpersistent_dbreset_internal()
    done_message()

def done_message():
    print("Done!")
    print("Search for example for 'Rhythms of the brain': http://localtest.me:8000/search?q=Rhythms+of+the+brain")
    print("To test SciDB: http://localtest.me:8000/scidb/10.5822/978-1-61091-843-5_15")
    print("See mariadb_dump.sql for various other records you can look at.")

#################################################################################################
# ./run flask cli nonpersistent_dbreset
@cli.cli.command('nonpersistent_dbreset')
def nonpersistent_dbreset():
    print("Erasing nonpersistent databases (1 MariaDB databases servers + 1 ElasticSearch)! Did you double-check that any production/large databases are offline/inaccessible from here?")
    nonpersistent_dbreset_internal()
    done_message()


def nonpersistent_dbreset_internal():
    # Per https://stackoverflow.com/a/4060259
    __location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))

    engine_multi = create_engine(mariadb_url_no_timeout, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    cursor = engine_multi.raw_connection().cursor()

    # From https://stackoverflow.com/a/8248281
    cursor.execute("SELECT concat('DROP TABLE IF EXISTS `', table_name, '`;') FROM information_schema.tables WHERE table_schema = 'allthethings';")
    delete_all_query = "\n".join([item[0] for item in cursor.fetchall()])
    if len(delete_all_query) > 0:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        cursor.execute(delete_all_query)
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1; COMMIT;")

    # Generated with `docker compose exec mariadb mysqldump -u allthethings -ppassword --opt --where="1 limit 100" --skip-comments --ignore-table=computed_all_md5s allthethings > mariadb_dump.sql`
    mariadb_dump = pathlib.Path(os.path.join(__location__, 'mariadb_dump.sql')).read_text()
    cursor.execute(mariadb_dump)

    torrents_json = pathlib.Path(os.path.join(__location__, 'torrents.json')).read_text()
    cursor.execute('DROP TABLE IF EXISTS torrents_json; CREATE TABLE torrents_json (json JSON NOT NULL, PRIMARY KEY(json(100))) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin; INSERT INTO torrents_json (json) VALUES (%(json)s); COMMIT', {'json': torrents_json})

    mysql_reset_aac_tables_internal()
    mysql_build_aac_tables_internal()

    engine_multi.raw_connection().ping(reconnect=True)
    cursor.execute(mariadb_dump)
    cursor.close()

    mysql_build_computed_all_md5s_internal()

    time.sleep(1)
    elastic_reset_aarecords_internal()
    elastic_build_aarecords_all_internal()
    mysql_build_aarecords_codes_numbers_internal()

def query_yield_batches(conn, qry, pk_attr, maxrq):
    """specialized windowed query generator (using LIMIT/OFFSET)

    This recipe is to select through a large number of rows thats too
    large to fetch at once. The technique depends on the primary key
    of the FROM clause being an integer value, and selects items
    using LIMIT."""

    firstid = None
    while True:
        q = qry
        if firstid is not None:
            q = qry.where(pk_attr > firstid)
        batch = conn.execute(q.order_by(pk_attr).limit(maxrq)).all()
        if len(batch) == 0:
            break
        yield batch
        firstid = batch[-1][0]

#################################################################################################
# Reset "annas_archive_meta_*" tables so they are built from scratch.
# ./run flask cli mysql_reset_aac_tables
#
# To dump computed_all_md5s to txt:
#   docker exec mariadb mariadb -uallthethings -ppassword allthethings --skip-column-names -e 'SELECT LOWER(HEX(md5)) from computed_all_md5s;' > md5.txt
@cli.cli.command('mysql_reset_aac_tables')
def mysql_reset_aac_tables():
    mysql_reset_aac_tables_internal()

def mysql_reset_aac_tables_internal():
    print("Resetting aac tables...")
    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        cursor.execute('DROP TABLE IF EXISTS annas_archive_meta_aac_filenames')
    print("Done!")

#################################################################################################
# Rebuild "annas_archive_meta_*" tables, if they have changed.
# ./run flask cli mysql_build_aac_tables
@cli.cli.command('mysql_build_aac_tables')
def mysql_build_aac_tables():
    mysql_build_aac_tables_internal()

def mysql_build_aac_tables_internal():
    print("Building aac tables...")
    file_data_files_by_collection = collections.defaultdict(list)

    COLLECTIONS_WITH_MULTIPLE_MD5 = ['magzdb_records', 'nexusstc_records']

    for filename in os.listdir(allthethings.utils.aac_path_prefix()):
        if not (filename.startswith('annas_archive_meta__aacid__') and filename.endswith('.jsonl.seekable.zst')):
            continue
        collection = filename.split('__')[2]
        file_data_files_by_collection[collection].append(filename)

    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        cursor.execute('CREATE TABLE IF NOT EXISTS annas_archive_meta_aac_filenames (`collection` VARCHAR(250) NOT NULL, `filename` VARCHAR(250) NOT NULL, PRIMARY KEY (`collection`)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        cursor.execute('SELECT * FROM annas_archive_meta_aac_filenames')
        existing_filenames_by_collection = { row['collection']: row['filename'] for row in cursor.fetchall() }

        collections_need_indexing = {}
        for collection, filenames in file_data_files_by_collection.items():
            filenames.sort()
            previous_filename = existing_filenames_by_collection.get(collection) or ''
            collection_needs_indexing = filenames[-1] != previous_filename
            if collection_needs_indexing:
                collections_need_indexing[collection] = filenames[-1]
            print(f"{collection:20}   files found: {len(filenames):02}    latest: {filenames[-1].split('__')[3].split('.')[0]}    {'previous filename: ' + previous_filename if collection_needs_indexing else '(no change)'}")

        for collection, filename in collections_need_indexing.items():
            print(f"[{collection}] Starting indexing...")

            extra_index_fields = {}
            if collection == 'duxiu_records':
                extra_index_fields['filename_decoded_basename'] = 'VARCHAR(250) NULL'
            elif collection == 'upload_records':
                extra_index_fields['filepath_raw_md5'] = 'CHAR(32) CHARACTER SET ascii NOT NULL'
                extra_index_fields['dont_index_file'] = 'TINYINT NOT NULL'
            elif collection in ['hathitrust_records', 'hathitrust_files']:
                extra_index_fields['pairtree_filename'] = 'VARCHAR(250) NOT NULL'

            def build_insert_data(line, byte_offset):
                if SLOW_DATA_IMPORTS:
                    try:
                        orjson.loads(line)
                    except Exception as err:
                        raise Exception(f"Error parsing AAC JSON: {collection=} {filename=} {line=} {err=}")

                # Parse "canonical AAC" more efficiently than parsing all the JSON
                matches = re.match(rb'\{"aacid":"([^"]+)",("data_folder":"([^"]+)",)?"metadata":\{"[^"]+":([^,]+),("md5":"([^"]+)")?', line)
                if matches is None:
                    raise Exception(f"Line is not in canonical AAC format: '{line}'")
                aacid = matches[1]
                # data_folder = matches[3]
                primary_id = matches[4].replace(b'"', b'')

                worldcat_edition_cluster_pairs = []
                if collection == 'worldcat':
                    if (b'not_found_title_json' in line) or (b'redirect_title_json' in line) or (b'"other_meta_type":"successful_range_query"' in line) or (b'"other_meta_type":"status_internal_server_error"' in line) or (b'"other_meta_type":"todo_range_query"' in line):
                        return None
                    elif b'"other_meta_type":"library"' in line:
                        primary_id = f"library__{orjson.loads(line)['metadata']['registry_id']}".encode()
                    elif b'"other_meta_type":"search_editions_response"' in line:
                        primary_id = orjson.loads(line)['metadata']['query'].encode()

                    if b'"search_editions_response/' in line:
                        for filename in orjson.loads(line)['metadata']['from_filenames']:
                            if filename.startswith('search_editions_response/'):
                                query_oclc_id = int(filename.replace('search_editions_response/', ''))
                                record_oclc_id = int(primary_id.decode())
                                worldcat_edition_cluster_pairs.append({ 
                                    'query_oclc_id': query_oclc_id, 
                                    'record_oclc_id': record_oclc_id, 
                                })
                elif collection == 'nexusstc_records':
                    if b'"type":["wiki"]' in line:
                        return None
                    if line.startswith(b'{"aacid":"aacid__nexusstc_records__20240516T181305Z__78xFBbXdi1dSBZxyoVNAdn","metadata":{"nexus_id":"6etg0wq0q8nsoufh9gtj4n9s5","record":{"abstract":[],"authors":[{"family":"Fu","given":"Ke-Ang","sequence":"first"},{"family":"Wang","given":"Jiangfeng","sequence":"additional"}],"ctr":[0.1],"custom_score":[1.0],"embeddings":[],"id":[{"dois":["10.1080/03610926.2022.2027451"],"nexus_id":"6etg0wq0q8nsoufh9gtj4n9s5"}],"issued_at":[1642982400],"languages":["en"],"links":[],"metadata":[{"container_title":"Communications in Statistics - Theory and Methods","first_page":6266,"issns":["0361-0926","1532-415X"],"issue":"17","last_page":6274,"publisher":"Informa UK Limited","volume":"52"}],"navigational_facets":[],"page_rank":[0.15],"reference_texts":[],"referenced_by_count":[0],"references":[{"doi":"10.1080/03461230802700897","type":"reference"},{"doi":"10.1239/jap/1238592120","type":"reference"},{"doi":"10.1016/j.insmatheco.2012.06.010","type":"reference"},{"doi":"10.1016/j.insmatheco.2020.12.003","type":"reference"},{"doi":"10.1007/s11009-019-09722-8","type":"reference"},{"doi":"10.1016/0304-4149(94)90113-9","type":"reference"},{"doi":"10.1016/j.insmatheco.2008.08.009","type":"reference"},{"doi":"10.1080/03610926.2015.1060338","type":"reference"},{"doi":"10.3150/17-bej948","type":"reference"},{"doi":"10.1093/biomet/58.1.83"("type":"reference"},{"doi":"10.1239/aap/1293113154","type":"reference"},{"doi":"10.1016/j.spl.2020.108857","type":"reference"},{"doi":"10.1007/s11424-019-8159-3","type":"reference"},{"doi":"10.1007/s11425-010-4012-9","type":"reference"},{"doi":"10.1007/s10114-017-6433-7","type":"reference"},{"doi":"10.1016/j.spl.2011.08.024","type":"reference"},{"doi":"10.1007/s11009-008-9110-6","type":"reference"},{"doi":"10.1016/j.insmatheco.2020.12.005","type":"reference"},{"doi":"10.1016/j.spa.2003.07.001","type":"reference"},{"doi":"10.1016/j.insmatheco.2013.08.008","type":"reference"}],"signature":[],"tags":["Statistics and Probability"],"title":["Moderate deviations for a Hawkes-type risk model with arbitrary dependence between claim sizes and waiting times"],"type":["journal-article"],"updated_at":[1715883185]}}}'):
                        # Bad record
                        return None
                elif collection == 'ebscohost_records':
                    ebscohost_matches = re.search(rb'"plink":"https://search\.ebscohost\.com/login\.aspx\?direct=true\\u0026db=edsebk\\u0026AN=([0-9]+)\\u0026site=ehost-live"', line)
                    if ebscohost_matches is None:
                        raise Exception(f"Incorrect ebscohost line: '{line}'")
                    primary_id = ebscohost_matches[1]
                elif collection == 'goodreads_records':
                    if line.endswith(b',"record":""}}\n'):
                        # Bad record
                        return None

                md5 = matches[6]
                if ('duxiu_files' in collection and b'"original_md5"' in line):
                    # For duxiu_files, md5 is the primary id, so we stick original_md5 in the md5 column so we can query that as well.
                    original_md5_matches = re.search(rb'"original_md5":"([^"]*)"', line)
                    if original_md5_matches is None:
                        raise Exception(f"'original_md5' found, but not in an expected format! '{line}'")
                    md5 = original_md5_matches[1]
                elif md5 is None:
                    if b'"md5_reported"' in line:
                        md5_reported_matches = re.search(rb'"md5_reported":"([^"]*)"', line)
                        if md5_reported_matches is None:
                            raise Exception(f"'md5_reported' found, but not in an expected format! '{line}'")
                        md5 = md5_reported_matches[1]
                if (md5 is not None) and (not bool(re.match(rb"^[a-f\d]{32}$", md5))):
                    # Remove if it's not md5.
                    md5 = None

                multiple_md5s = []
                if collection in COLLECTIONS_WITH_MULTIPLE_MD5:
                    multiple_md5s = [md5 for md5 in set([md5.decode().lower() for md5 in re.findall(rb'"md5":"([^"]+)"', line)]) if allthethings.utils.validate_canonical_md5s([md5])]

                return_data = {
                    'aacid': aacid.decode(),
                    'primary_id': primary_id.decode(),
                    'md5': md5.decode().lower() if md5 is not None else None,
                    'multiple_md5s': multiple_md5s,
                    'worldcat_edition_cluster_pairs': worldcat_edition_cluster_pairs,
                    'byte_offset': byte_offset,
                    'byte_length': len(line),
                }

                if collection == 'duxiu_records':
                    return_data['filename_decoded_basename'] = None
                    if b'"filename_decoded"' in line:
                        json = orjson.loads(line)
                        filename_decoded = json['metadata']['record']['filename_decoded']
                        return_data['filename_decoded_basename'] = filename_decoded.rsplit('.', 1)[0]
                    elif b'"full_filepath_raw_base64"' in line:
                        json = orjson.loads(line)
                        filename_decoded = base64.b64decode(json['metadata']['record']['full_filepath_raw_base64']).decode('utf8','replace')
                        return_data['filename_decoded_basename'] = filename_decoded.rsplit('.', 1)[0]
                elif collection == 'upload_records':
                    json = orjson.loads(line)
                    filepath_raw_suffix = allthethings.utils.get_filepath_raw_from_upload_aac_metadata(json['metadata'])
                    subcollection = json['aacid'].split('__')[1].removeprefix('upload_records_')
                    return_data['filepath_raw_md5'] = hashlib.md5(subcollection.encode() + b'/' + filepath_raw_suffix).hexdigest()
                    filepath_raw_suffix_lower = filepath_raw_suffix.lower()
                    return_data['dont_index_file'] = 0
                    if filepath_raw_suffix_lower.endswith(b'metadata.opf') or filepath_raw_suffix_lower.endswith(b'cover.jpg'):
                        return_data['dont_index_file'] = 1
                elif collection == 'hathitrust_records':
                    json = orjson.loads(line)
                    return_data['pairtree_filename'] = json['metadata']['pairtree_filename']
                elif collection == 'hathitrust_files':
                    json = orjson.loads(line)
                    return_data['pairtree_filename'] = json['metadata']['filepath']
                return return_data

            AAC_CHUNK_SIZE = 100000

            filepath = f'{allthethings.utils.aac_path_prefix()}{filename}'
            table_name = f'annas_archive_meta__aacid__{collection}'
            print(f"[{collection}] Reading from {filepath} to {table_name}")

            filepath_decompressed = filepath.replace('.seekable.zst', '')
            file = None
            uncompressed_size = None
            if os.path.exists(filepath_decompressed):
                print(f"[{collection}] Found decompressed version, using that for performance: {filepath_decompressed}")
                print("Note that using the compressed version for linear operations is sometimes faster than running into drive read limits (even with NVMe), so be sure to performance-test this on your machine if the files are large, and commenting out these lines if necessary.")
                file = open(filepath_decompressed, 'rb')
                uncompressed_size = os.path.getsize(filepath_decompressed)
            else:
                file = indexed_zstd.IndexedZstdFile(filepath)
                uncompressed_size = file.size()
            print(f"[{collection}] {uncompressed_size=}")

            table_extra_fields = ''.join([f', {index_name} {index_type}' for index_name, index_type in extra_index_fields.items()])
            table_extra_index = ''.join([f', INDEX({index_name})' for index_name, index_type in extra_index_fields.items()])
            insert_extra_names = ''.join([f', {index_name}' for index_name, index_type in extra_index_fields.items()])
            insert_extra_values = ''.join([f', %({index_name})s' for index_name, index_type in extra_index_fields.items()])

            tables = []

            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
            cursor.execute(f"CREATE TABLE {table_name} (`aacid` VARCHAR(250) CHARACTER SET ascii NOT NULL, `primary_id` VARCHAR(250) NULL, `md5` CHAR(32) CHARACTER SET ascii NULL, `byte_offset` BIGINT NOT NULL, `byte_length` BIGINT NOT NULL {table_extra_fields}, PRIMARY KEY (`aacid`), INDEX `primary_id` (`primary_id`), INDEX `md5` (`md5`) {table_extra_index}) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
            tables.append(table_name)

            if collection in COLLECTIONS_WITH_MULTIPLE_MD5:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}__multiple_md5")
                cursor.execute(f"CREATE TABLE {table_name}__multiple_md5 (`md5` CHAR(32) CHARACTER SET ascii NOT NULL, `aacid` VARCHAR(250) CHARACTER SET ascii NOT NULL, PRIMARY KEY (`md5`, `aacid`), INDEX `aacid_md5` (`aacid`, `md5`)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
                tables.append(f"{table_name}__multiple_md5")
            if collection == 'worldcat':
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}__edition_cluster_pairs")
                cursor.execute(f"CREATE TABLE {table_name}__edition_cluster_pairs (`query_oclc_id` BIGINT NOT NULL, `record_oclc_id` BIGINT NOT NULL, PRIMARY KEY (`query_oclc_id`, `record_oclc_id`), INDEX `record_oclc_id` (`record_oclc_id`, `query_oclc_id`)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
                tables.append(f"{table_name}__edition_cluster_pairs")

            cursor.execute(f"LOCK TABLES {' WRITE, '.join(tables)} WRITE")
            # From https://github.com/indygreg/python-zstandard/issues/13#issuecomment-1544313739
            with tqdm.tqdm(total=uncompressed_size, bar_format='{l_bar}{bar}{r_bar} {eta}', unit='B', unit_scale=True) as pbar:
                byte_offset = 0
                for lines in more_itertools.ichunked(file, AAC_CHUNK_SIZE):
                    bytes_in_batch = 0
                    insert_data = []
                    insert_data_multiple_md5s = []
                    insert_data_worldcat_edition_cluster_pairs = []
                    for line in lines:
                        allthethings.utils.aac_spot_check_line_bytes(line, {})
                        insert_data_line = build_insert_data(line, byte_offset)
                        if insert_data_line is not None:
                            for md5 in insert_data_line['multiple_md5s']:
                                insert_data_multiple_md5s.append({ "md5": md5, "aacid": insert_data_line['aacid'] })
                            del insert_data_line['multiple_md5s']
                            insert_data_worldcat_edition_cluster_pairs += insert_data_line['worldcat_edition_cluster_pairs']
                            del insert_data_line['worldcat_edition_cluster_pairs']
                            insert_data.append(insert_data_line)
                        line_len = len(line)
                        byte_offset += line_len
                        bytes_in_batch += line_len
                    action = 'INSERT'
                    if collection == 'duxiu_records':
                        # This collection inadvertently has a bunch of exact duplicate lines.
                        action = 'REPLACE'
                    if len(insert_data) > 0:
                        connection.connection.ping(reconnect=True)
                        cursor.executemany(f'{action} INTO {table_name} (aacid, primary_id, md5, byte_offset, byte_length {insert_extra_names}) VALUES (%(aacid)s, %(primary_id)s, %(md5)s, %(byte_offset)s, %(byte_length)s {insert_extra_values})', insert_data)
                    if len(insert_data_multiple_md5s) > 0:
                        connection.connection.ping(reconnect=True)
                        cursor.executemany(f'{action} INTO {table_name}__multiple_md5 (md5, aacid) VALUES (%(md5)s, %(aacid)s)', insert_data_multiple_md5s)
                    if len(insert_data_worldcat_edition_cluster_pairs) > 0:
                        connection.connection.ping(reconnect=True)
                        cursor.executemany(f'INSERT IGNORE INTO {table_name}__edition_cluster_pairs (query_oclc_id, record_oclc_id) VALUES (%(query_oclc_id)s, %(record_oclc_id)s)', insert_data_worldcat_edition_cluster_pairs)
                    pbar.update(bytes_in_batch)
            connection.connection.ping(reconnect=True)
            cursor.execute("UNLOCK TABLES")
            cursor.execute("REPLACE INTO annas_archive_meta_aac_filenames (collection, filename) VALUES (%(collection)s, %(filename)s)", { "collection": collection, "filename": filepath.rsplit('/', 1)[-1] })
            cursor.execute("COMMIT")
            print(f"[{collection}] Done!")


#################################################################################################
# Rebuild "computed_all_md5s" table in MySQL. At the time of writing, this isn't
# used in the app, but it is used for `./run flask cli elastic_build_aarecords_main`.
# ./run flask cli mysql_build_computed_all_md5s
#
# To dump computed_all_md5s to txt:
#   docker exec mariadb mariadb -uallthethings -ppassword allthethings --skip-column-names -e 'SELECT LOWER(HEX(md5)) from computed_all_md5s;' > md5.txt
@cli.cli.command('mysql_build_computed_all_md5s')
def mysql_build_computed_all_md5s():
    print("Erasing entire MySQL 'computed_all_md5s' table! Did you double-check that any production/large databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 2 seconds to abort..")
    time.sleep(2)

    mysql_build_computed_all_md5s_internal()

def mysql_build_computed_all_md5s_internal():
    engine_multi = create_engine(mariadb_url_no_timeout, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    cursor = engine_multi.raw_connection().cursor()
    print("Removing table computed_all_md5s (if exists)")
    cursor.execute('DROP TABLE IF EXISTS computed_all_md5s')
    print("Load indexes of libgenli_files")
    cursor.execute('LOAD INDEX INTO CACHE libgenli_files')
    print("Creating table computed_all_md5s and load with libgenli_files")
    # NOTE: first_source is currently purely for debugging!
    cursor.execute('CREATE TABLE computed_all_md5s (md5 BINARY(16) NOT NULL, first_source TINYINT NOT NULL, PRIMARY KEY (md5)) ENGINE=MyISAM ROW_FORMAT=FIXED SELECT UNHEX(md5) AS md5, 1 AS first_source FROM libgenli_files WHERE md5 IS NOT NULL')
    print("Load indexes of computed_all_md5s")
    cursor.execute('LOAD INDEX INTO CACHE computed_all_md5s')
    # Fully superseded by aac_zlib3
    # print("Load indexes of zlib_book")
    # cursor.execute('LOAD INDEX INTO CACHE zlib_book')
    # print("Inserting from 'zlib_book' (md5_reported)")
    # cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5_reported), 2 FROM zlib_book WHERE md5_reported != "" AND md5_reported IS NOT NULL')
    # print("Inserting from 'zlib_book' (md5)")
    # cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 3 FROM zlib_book WHERE zlib_book.md5 != "" AND md5 IS NOT NULL')
    print("Load indexes of libgenrs_fiction")
    cursor.execute('LOAD INDEX INTO CACHE libgenrs_fiction')
    print("Inserting from 'libgenrs_fiction'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 4 FROM libgenrs_fiction WHERE md5 IS NOT NULL')
    print("Load indexes of libgenrs_updated")
    cursor.execute('LOAD INDEX INTO CACHE libgenrs_updated')
    print("Inserting from 'libgenrs_updated'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 5 FROM libgenrs_updated WHERE md5 IS NOT NULL')
    print("Load indexes of aa_ia_2023_06_files and aa_ia_2023_06_metadata")
    cursor.execute('LOAD INDEX INTO CACHE aa_ia_2023_06_files, aa_ia_2023_06_metadata')
    print("Inserting from 'aa_ia_2023_06_files'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 6 FROM aa_ia_2023_06_metadata USE INDEX (libgen_md5) JOIN aa_ia_2023_06_files USING (ia_id) WHERE aa_ia_2023_06_metadata.libgen_md5 IS NULL')
    print("Load indexes of annas_archive_meta__aacid__ia2_acsmpdf_files and aa_ia_2023_06_metadata")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__ia2_acsmpdf_files, aa_ia_2023_06_metadata')
    print("Inserting from 'annas_archive_meta__aacid__ia2_acsmpdf_files'")
    # Note: annas_archive_meta__aacid__ia2_records / files are all after 2023, so no need to filter out the old libgen ones!
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(annas_archive_meta__aacid__ia2_acsmpdf_files.md5), 7 FROM aa_ia_2023_06_metadata USE INDEX (libgen_md5) JOIN annas_archive_meta__aacid__ia2_acsmpdf_files ON (ia_id=primary_id) WHERE aa_ia_2023_06_metadata.libgen_md5 IS NULL')
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(annas_archive_meta__aacid__ia2_acsmpdf_files.md5), 8 FROM annas_archive_meta__aacid__ia2_records JOIN annas_archive_meta__aacid__ia2_acsmpdf_files USING (primary_id)')
    print("Load indexes of annas_archive_meta__aacid__zlib3_records")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__zlib3_records')
    print("Inserting from 'annas_archive_meta__aacid__zlib3_records'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 9 FROM annas_archive_meta__aacid__zlib3_records WHERE md5 IS NOT NULL')
    # We currently don't support loading a zlib3_file without a corresponding zlib3_record. Should we ever?
    # print("Load indexes of annas_archive_meta__aacid__zlib3_files")
    # cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__zlib3_files')
    # print("Inserting from 'annas_archive_meta__aacid__zlib3_files'")
    # cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 10 FROM annas_archive_meta__aacid__zlib3_files WHERE md5 IS NOT NULL')
    print("Load indexes of annas_archive_meta__aacid__duxiu_files")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__duxiu_files')
    print("Inserting from 'annas_archive_meta__aacid__duxiu_files'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(primary_id), 11 FROM annas_archive_meta__aacid__duxiu_files WHERE primary_id IS NOT NULL')
    print("Load indexes of annas_archive_meta__aacid__upload_records and annas_archive_meta__aacid__upload_files")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__upload_records, annas_archive_meta__aacid__upload_files')
    print("Inserting from 'annas_archive_meta__aacid__upload_files'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(annas_archive_meta__aacid__upload_files.primary_id), 12 FROM annas_archive_meta__aacid__upload_files JOIN annas_archive_meta__aacid__upload_records ON (annas_archive_meta__aacid__upload_records.md5 = annas_archive_meta__aacid__upload_files.primary_id) WHERE annas_archive_meta__aacid__upload_files.primary_id IS NOT NULL AND annas_archive_meta__aacid__upload_records.dont_index_file = 0')
    print("Load indexes of annas_archive_meta__aacid__upload_records and annas_archive_meta__aacid__magzdb_records__multiple_md5")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__upload_records, annas_archive_meta__aacid__magzdb_records__multiple_md5')
    print("Inserting from 'annas_archive_meta__aacid__magzdb_records__multiple_md5'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 13 FROM annas_archive_meta__aacid__magzdb_records__multiple_md5 WHERE UNHEX(md5) IS NOT NULL')
    print("Load indexes of annas_archive_meta__aacid__upload_records and annas_archive_meta__aacid__nexusstc_records__multiple_md5")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__upload_records, annas_archive_meta__aacid__nexusstc_records__multiple_md5')
    print("Inserting from 'annas_archive_meta__aacid__nexusstc_records__multiple_md5'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(md5), 14 FROM annas_archive_meta__aacid__nexusstc_records__multiple_md5 WHERE UNHEX(md5) IS NOT NULL')
    print("Load indexes of annas_archive_meta__aacid__hathitrust_files")
    cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__hathitrust_files')
    print("Inserting from 'annas_archive_meta__aacid__hathitrust_files'")
    cursor.execute('INSERT IGNORE INTO computed_all_md5s (md5, first_source) SELECT UNHEX(annas_archive_meta__aacid__hathitrust_files.primary_id), 15 FROM annas_archive_meta__aacid__hathitrust_files WHERE annas_archive_meta__aacid__hathitrust_files.primary_id IS NOT NULL')
    cursor.close()
    print("Done mysql_build_computed_all_md5s_internal!")
    # engine_multi = create_engine(mariadb_url_no_timeout, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    # cursor = engine_multi.raw_connection().cursor()
    # print("Removing table computed_all_md5s (if exists)")
    # cursor.execute('DROP TABLE IF EXISTS computed_all_md5s')
    # print("Load indexes of libgenli_files")
    # cursor.execute('LOAD INDEX INTO CACHE libgenli_files')
    # # print("Creating table computed_all_md5s and load with libgenli_files")
    # # cursor.execute('CREATE TABLE computed_all_md5s (md5 CHAR(32) NOT NULL, PRIMARY KEY (md5)) ENGINE=MyISAM DEFAULT CHARSET=ascii COLLATE ascii_bin ROW_FORMAT=FIXED SELECT md5 FROM libgenli_files')

    # # print("Load indexes of computed_all_md5s")
    # # cursor.execute('LOAD INDEX INTO CACHE computed_all_md5s')
    # print("Load indexes of zlib_book")
    # cursor.execute('LOAD INDEX INTO CACHE zlib_book')
    # # print("Inserting from 'zlib_book' (md5_reported)")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT md5_reported FROM zlib_book LEFT JOIN computed_all_md5s ON (computed_all_md5s.md5 = zlib_book.md5_reported) WHERE md5_reported != "" AND computed_all_md5s.md5 IS NULL')
    # # print("Inserting from 'zlib_book' (md5)")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT md5 FROM zlib_book LEFT JOIN computed_all_md5s USING (md5) WHERE zlib_book.md5 != "" AND computed_all_md5s.md5 IS NULL')
    # print("Load indexes of libgenrs_fiction")
    # cursor.execute('LOAD INDEX INTO CACHE libgenrs_fiction')
    # # print("Inserting from 'libgenrs_fiction'")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT LOWER(libgenrs_fiction.MD5) FROM libgenrs_fiction LEFT JOIN computed_all_md5s ON (computed_all_md5s.md5 = LOWER(libgenrs_fiction.MD5)) WHERE computed_all_md5s.md5 IS NULL')
    # print("Load indexes of libgenrs_updated")
    # cursor.execute('LOAD INDEX INTO CACHE libgenrs_updated')
    # # print("Inserting from 'libgenrs_updated'")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT MD5 FROM libgenrs_updated LEFT JOIN computed_all_md5s USING (md5) WHERE computed_all_md5s.md5 IS NULL')
    # print("Load indexes of aa_ia_2023_06_files")
    # cursor.execute('LOAD INDEX INTO CACHE aa_ia_2023_06_files')
    # # print("Inserting from 'aa_ia_2023_06_files'")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT MD5 FROM aa_ia_2023_06_files LEFT JOIN aa_ia_2023_06_metadata USING (ia_id) LEFT JOIN computed_all_md5s USING (md5) WHERE aa_ia_2023_06_metadata.libgen_md5 IS NULL AND computed_all_md5s.md5 IS NULL')
    # print("Load indexes of annas_archive_meta__aacid__zlib3_records")
    # cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__zlib3_records')
    # # print("Inserting from 'annas_archive_meta__aacid__zlib3_records'")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT md5 FROM annas_archive_meta__aacid__zlib3_records LEFT JOIN computed_all_md5s USING (md5) WHERE md5 IS NOT NULL AND computed_all_md5s.md5 IS NULL')
    # print("Load indexes of annas_archive_meta__aacid__zlib3_files")
    # cursor.execute('LOAD INDEX INTO CACHE annas_archive_meta__aacid__zlib3_files')
    # # print("Inserting from 'annas_archive_meta__aacid__zlib3_files'")
    # # cursor.execute('INSERT INTO computed_all_md5s SELECT md5 FROM annas_archive_meta__aacid__zlib3_files LEFT JOIN computed_all_md5s USING (md5) WHERE md5 IS NOT NULL AND computed_all_md5s.md5 IS NULL')
    # print("Creating table computed_all_md5s")
    # cursor.execute('CREATE TABLE computed_all_md5s (md5 CHAR(32) NOT NULL, PRIMARY KEY (md5)) ENGINE=MyISAM DEFAULT CHARSET=ascii COLLATE ascii_bin ROW_FORMAT=FIXED IGNORE SELECT DISTINCT md5 AS md5 FROM libgenli_files UNION DISTINCT (SELECT DISTINCT md5_reported AS md5 FROM zlib_book WHERE md5_reported != "") UNION DISTINCT (SELECT DISTINCT md5 AS md5 FROM zlib_book WHERE md5 != "") UNION DISTINCT (SELECT DISTINCT LOWER(libgenrs_fiction.MD5) AS md5 FROM libgenrs_fiction) UNION DISTINCT (SELECT DISTINCT MD5 AS md5 FROM libgenrs_updated) UNION DISTINCT (SELECT DISTINCT md5 AS md5 FROM aa_ia_2023_06_files LEFT JOIN aa_ia_2023_06_metadata USING (ia_id) WHERE aa_ia_2023_06_metadata.libgen_md5 IS NULL) UNION DISTINCT (SELECT DISTINCT md5 AS md5 FROM annas_archive_meta__aacid__zlib3_records WHERE md5 IS NOT NULL) UNION DISTINCT (SELECT DISTINCT md5 AS md5 FROM annas_archive_meta__aacid__zlib3_files WHERE md5 IS NOT NULL)')
    # cursor.close()

es_create_index_body = {
    "mappings": {
        "dynamic": False,
        "properties": {
            "search_only_fields": {
                "properties": {
                    "search_filesize": { "type": "long", "index": False, "doc_values": True },
                    "search_year": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_extension": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_content_type": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_most_likely_language_code": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_isbn13": { "type": "keyword", "index": True, "doc_values": True },
                    "search_doi": { "type": "keyword", "index": True, "doc_values": True },
                    "search_title": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_author": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_publisher": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_edition_varia": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_original_filename": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_description_comments": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_text": { "type": "text", "index": True, "index_phrases": True, "analyzer": "custom_icu_analyzer" },
                    "search_score_base_rank": { "type": "rank_feature" },
                    "search_access_types": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_record_primary_source": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_record_sources": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    "search_bulk_torrents": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                    # ES limit https://github.com/langchain-ai/langchain/issues/10218#issuecomment-1706481539
                    # dot_product because embeddings are already normalized. We run on an old version of ES so we shouldn't rely on the
                    # default behavior of normalization.
                    # "search_text_embedding_3_small_100_tokens_1024_dims": {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine"},
                    "search_added_date": { "type": "keyword", "index": True, "doc_values": True, "eager_global_ordinals": True },
                },
            },
        },
    },
    "settings": {
        "index": {
            "number_of_replicas": 0,
            "search.slowlog.threshold.query.warn": "4s",
            "store.preload": ["nvd", "dvd", "tim", "doc", "dim"],
            "codec": "best_compression",
            "analysis": {
                "analyzer": {
                    "custom_icu_analyzer": {
                        "tokenizer": "icu_tokenizer",
                        "char_filter": ["icu_normalizer"],
                        "filter": ["t2s", "icu_folding"],
                    },
                },
                "filter": { "t2s": { "type": "icu_transform", "id": "Traditional-Simplified" } },
            },
        },
    },
}

#################################################################################################
# Recreate "aarecords" index in ElasticSearch, without filling it with data yet.
# (That is done with `./run flask cli elastic_build_aarecords_*`)
# ./run flask cli elastic_reset_aarecords
@cli.cli.command('elastic_reset_aarecords')
def elastic_reset_aarecords():
    print("Erasing entire ElasticSearch 'aarecords' index! Did you double-check that any production/large databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 2 seconds to abort..")
    time.sleep(2)

    elastic_reset_aarecords_internal()

def elastic_reset_aarecords_internal():
    print("Deleting ES indices")
    for index_name, es_handle in allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING.items():
        es_handle.options(ignore_status=[400,404]).indices.delete(index=index_name) # Old
        for virtshard in range(0, 100): # Out of abundance, delete up to a large number
            es_handle.options(ignore_status=[400,404]).indices.delete(index=f'{index_name}__{virtshard}')
    print("Creating ES indices")
    for index_name, es_handle in allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING.items():
        for full_index_name in allthethings.utils.all_virtshards_for_index(index_name):
            es_handle.indices.create(wait_for_active_shards=1,index=full_index_name, body=es_create_index_body)

    print("Creating MySQL aarecords tables")
    with Session(engine) as session:
        session.connection().connection.ping(reconnect=True)
        cursor = session.connection().connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute('DROP TABLE IF EXISTS aarecords_all') # Old
        cursor.execute('DROP TABLE IF EXISTS aarecords_isbn13') # Old
        cursor.execute(f'CREATE TABLE IF NOT EXISTS aarecords_codes (code VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, aarecord_id VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH}) NOT NULL, aarecord_id_prefix VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_PREFIX_LENGTH}) NOT NULL, row_number_order_by_code BIGINT NOT NULL, dense_rank_order_by_code BIGINT NOT NULL, row_number_partition_by_aarecord_id_prefix_order_by_code BIGINT NOT NULL, dense_rank_partition_by_aarecord_id_prefix_order_by_code BIGINT NOT NULL, PRIMARY KEY (code, aarecord_id), INDEX aarecord_id_prefix (aarecord_id_prefix)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        cursor.execute(f'CREATE TABLE IF NOT EXISTS aarecords_codes_prefixes (code_prefix VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, PRIMARY KEY (code_prefix)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        # cursor.execute('CREATE TABLE IF NOT EXISTS model_cache_text_embedding_3_small_100_tokens (hashed_aarecord_id BINARY(16) NOT NULL, aarecord_id VARCHAR(1000) NOT NULL, embedding_text LONGTEXT, embedding LONGBLOB, PRIMARY KEY (hashed_aarecord_id)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        cursor.execute('COMMIT')

# These tables always need to be created new if they don't exist yet.
# They should only be used when doing a full refresh, but things will
# crash if they don't exist.
def new_tables_internal(codes_table_name, codes_for_lookup_table_name=None):
    with Session(engine) as session:
        session.connection().connection.ping(reconnect=True)
        cursor = session.connection().connection.cursor(pymysql.cursors.DictCursor)
        print(f"Creating fresh table {codes_table_name}")
        cursor.execute(f'DROP TABLE IF EXISTS {codes_table_name}')
        cursor.execute(f'CREATE TABLE {codes_table_name} (code VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, aarecord_id VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH}) NOT NULL) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        cursor.execute('COMMIT')
        if codes_for_lookup_table_name is not None:
            print(f"Creating fresh table {codes_for_lookup_table_name}")
            cursor.execute(f'DROP TABLE IF EXISTS {codes_for_lookup_table_name}')
            cursor.execute(f'CREATE TABLE {codes_for_lookup_table_name} (code VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, aarecord_id VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH}) NOT NULL, PRIMARY KEY (code, aarecord_id)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
            cursor.execute('COMMIT')

#################################################################################################
# ./run flask cli update_aarecords_index_mappings
@cli.cli.command('update_aarecords_index_mappings')
def update_aarecords_index_mappings():
    print("Updating ES indices")
    for index_name, es_handle in allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING.items():
        for full_index_name in allthethings.utils.all_virtshards_for_index(index_name):
            es_handle.indices.put_mapping(body=es_create_index_body['mappings'], index=full_index_name)
    print("Done!")

def elastic_build_aarecords_job_init_pool():
    global elastic_build_aarecords_job_app
    global elastic_build_aarecords_compressor
    # print("Initializing pool worker (elastic_build_aarecords_job_init_pool)")
    from allthethings.app import create_app
    elastic_build_aarecords_job_app = create_app()

    # Per https://stackoverflow.com/a/4060259
    __location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
    elastic_build_aarecords_compressor = zstandard.ZstdCompressor(level=3, dict_data=zstandard.ZstdCompressionDict(pathlib.Path(os.path.join(__location__, 'aarecords_dump_for_dictionary.bin')).read_bytes()))

AARECORD_ID_PREFIX_TO_CODES_TABLE_NAME = {
    'edsebk': 'aarecords_codes_edsebk',
    'ia': 'aarecords_codes_ia',
    'isbndb': 'aarecords_codes_isbndb',
    'ol': 'aarecords_codes_ol',
    'duxiu_ssid': 'aarecords_codes_duxiu',
    'cadal_ssno': 'aarecords_codes_duxiu',
    'oclc': 'aarecords_codes_oclc',
    'magzdb': 'aarecords_codes_magzdb',
    'nexusstc': 'aarecords_codes_nexusstc',
    'md5': 'aarecords_codes_main',
    'doi': 'aarecords_codes_main',
    'nexusstc_download': 'aarecords_codes_main',

    'cerlalc': 'aarecords_codes_cerlalc',
    'czech_oo42hcks': 'aarecords_codes_czech_oo42hcks',
    'gbooks': 'aarecords_codes_gbooks',
    'goodreads': 'aarecords_codes_goodreads',
    'isbngrp': 'aarecords_codes_isbngrp',
    'libby': 'aarecords_codes_libby',
    'rgb': 'aarecords_codes_rgb',
    'trantor': 'aarecords_codes_trantor',

    'hathi': 'aarecords_codes_hathi',
}

AARECORD_ID_PREFIX_TO_CODES_FOR_LOOKUP = {
    'isbndb': { 'table_name': 'aarecords_codes_isbndb_for_lookup', 'code_names': ['collection'] }, # TODO: Use aarecord_id code here instead.
    'ol': { 'table_name': 'aarecords_codes_ol_for_lookup', 'code_names': ['isbn13', 'ocaid', 'md5'] },
    'oclc': { 'table_name': 'aarecords_codes_oclc_for_lookup', 'code_names': ['isbn13'] },
    'edsebk': { 'table_name': 'aarecords_codes_edsebk_for_lookup', 'code_names': ['isbn13'] },
    'trantor': { 'table_name': 'aarecords_codes_trantor_for_lookup', 'code_names': ['isbn13', 'sha256'] },
    'gbooks': { 'table_name': 'aarecords_codes_gbooks_for_lookup', 'code_names': ['isbn13', 'oclc'] },
    'goodreads': { 'table_name': 'aarecords_codes_goodreads_for_lookup', 'code_names': ['isbn13'] },
    'libby': { 'table_name': 'aarecords_codes_libby_for_lookup', 'code_names': ['isbn13'] },
    'czech_oo42hcks': { 'table_name': 'aarecords_codes_czech_oo42hcks_for_lookup', 'code_names': ['czech_oo42hcks_filename'] },
    'cerlalc': { 'table_name': 'aarecords_codes_cerlalc_for_lookup', 'code_names': ['isbn13'] },
    'rgb': { 'table_name': 'aarecords_codes_rgb_for_lookup', 'code_names': ['isbn13'] },
    'isbngrp': { 'table_name': 'aarecords_codes_isbngrp_for_lookup', 'code_names': ['isbn13', 'isbn13_prefix'] },
}

def elastic_build_aarecords_job(aarecord_ids):
    global elastic_build_aarecords_job_app
    global elastic_build_aarecords_compressor

    with elastic_build_aarecords_job_app.app_context():
        try:
            aarecord_ids = list(aarecord_ids)
            # print(f"[{os.getpid()}] elastic_build_aarecords_job start {len(aarecord_ids)}")
            with Session(engine) as session:
                operations_by_es_handle = collections.defaultdict(list)
                session.connection().connection.ping(reconnect=True)
                cursor = session.connection().connection.cursor(pymysql.cursors.DictCursor)
                cursor.execute('SELECT 1')
                list(cursor.fetchall())

                isbndb_canonical_isbn13s = [aarecord_id[len('isbndb:'):] for aarecord_id in aarecord_ids if aarecord_id.startswith('isbndb:')]
                bad_isbn13_aarecord_ids = []
                if len(isbndb_canonical_isbn13s) > 0:
                    # Filter out records that are filtered in get_isbndb_dicts, because there are some bad records there.
                    valid_isbndb_aarecord_ids = set(f"isbndb:{isbndb_dict['ean13']}" for isbndb_dict in get_isbndb_dicts(session, 'isbn13', isbndb_canonical_isbn13s))
                    bad_isbn13_aarecord_ids += set([aarecord_id for aarecord_id in aarecord_ids if aarecord_id.startswith('isbndb:') and aarecord_id not in valid_isbndb_aarecord_ids])
                    # Also filter out existing isbndb: aarecord_ids, which we can get since we do two passes (isbn13 and isbn10).
                    cursor = allthethings.utils.get_cursor_ping(session)
                    cursor.execute('SELECT aarecord_id FROM aarecords_codes_isbndb_for_lookup WHERE code="collection:isbndb" AND aarecord_id IN %(aarecord_ids)s', { "aarecord_ids": [aarecord_id for aarecord_id in aarecord_ids if aarecord_id.startswith('isbndb:')]})
                    bad_isbn13_aarecord_ids += set([aarecord_id.decode() for aarecord_id in allthethings.utils.fetch_scalars(cursor)])
                bad_isbn13_aarecord_ids = set(bad_isbn13_aarecord_ids)

                aarecord_ids = [aarecord_id for aarecord_id in aarecord_ids if (aarecord_id not in bad_isbn13_aarecord_ids) and (aarecord_id not in allthethings.utils.SEARCH_FILTERED_BAD_AARECORD_IDS)]
                if len(aarecord_ids) == 0:
                    return False

                # print(f"[{os.getpid()}] elastic_build_aarecords_job set up aa_records_all")
                aarecords = get_aarecords_internal_mysql(session, aarecord_ids)
                # print(f"[{os.getpid()}] elastic_build_aarecords_job got aarecords {len(aarecords)}")
                aarecords_all_md5_insert_data = []
                nexusstc_cid_only_insert_data = []
                temp_md5_with_doi_seen_insert_data = []
                aarecords_codes_insert_data_by_codes_table_name = collections.defaultdict(list)
                for aarecord in aarecords:
                    aarecord_id_split = aarecord['id'].split(':', 1)
                    hashed_aarecord_id = hashlib.md5(aarecord['id'].encode()).digest()
                    if aarecord_id_split[0] == 'md5':
                        # TODO: bring back for other records if necessary, but keep it possible to rerun
                        # only _main with recreating the table, and not needing INSERT .. ON DUPLICATE KEY UPDATE (deadlocks).
                        aarecords_all_md5_insert_data.append({
                            # 'hashed_aarecord_id': hashed_aarecord_id,
                            # 'aarecord_id': aarecord['id'],
                            'md5': bytes.fromhex(aarecord_id_split[1]) if aarecord['id'].startswith('md5:') else None,
                            'json_compressed': elastic_build_aarecords_compressor.compress(orjson.dumps({
                                # Note: used in external code.
                                'search_only_fields': {
                                    'search_access_types': aarecord['search_only_fields']['search_access_types'],
                                    'search_record_sources': aarecord['search_only_fields']['search_record_sources'],
                                    'search_bulk_torrents': aarecord['search_only_fields']['search_bulk_torrents'],
                                }
                            })),
                        })
                        for doi in aarecord['file_unified_data']['identifiers_unified'].get('doi') or []:
                            temp_md5_with_doi_seen_insert_data.append({ "doi": doi.lower().encode() })
                    elif aarecord_id_split[0] == 'nexusstc':
                        source_records_by_type = allthethings.utils.groupby(aarecord['source_records'], 'source_type', 'source_record')
                        for source_record in source_records_by_type['aac_nexusstc']:
                            if len(source_record['aa_nexusstc_derived']['cid_only_links']) > 0:
                                nexusstc_cid_only_insert_data.append({ "nexusstc_id": source_record['id'] })

                    for index in aarecord['indexes']:
                        virtshard = allthethings.utils.virtshard_for_hashed_aarecord_id(hashed_aarecord_id)
                        operations_by_es_handle[allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING[index]].append({ **aarecord, '_op_type': 'index', '_index': f'{index}__{virtshard}', '_id': aarecord['id'] })

                    codes = []
                    for code_name in aarecord['file_unified_data']['identifiers_unified'].keys():
                        for code_value in aarecord['file_unified_data']['identifiers_unified'][code_name]:
                            codes.append((code_name, code_value))
                    for code_name in aarecord['file_unified_data']['classifications_unified'].keys():
                        for code_value in aarecord['file_unified_data']['classifications_unified'][code_name]:
                            codes.append((code_name, code_value))
                    for code in codes:
                        code_text = f"{code[0]}:{code[1]}".encode()
                        codes_table_name = AARECORD_ID_PREFIX_TO_CODES_TABLE_NAME[aarecord_id_split[0]]
                        aarecords_codes_insert_data_by_codes_table_name[codes_table_name].append({ 'code': code_text, 'aarecord_id': aarecord['id'].encode() })
                        if aarecord_id_split[0] in AARECORD_ID_PREFIX_TO_CODES_FOR_LOOKUP:
                            if code[0] in AARECORD_ID_PREFIX_TO_CODES_FOR_LOOKUP[aarecord_id_split[0]]['code_names']:
                                codes_for_lookup_table_name = AARECORD_ID_PREFIX_TO_CODES_FOR_LOOKUP[aarecord_id_split[0]]['table_name']
                                aarecords_codes_insert_data_by_codes_table_name[codes_for_lookup_table_name].append({ 'code': code_text, 'aarecord_id': aarecord['id'].encode() })

                # print(f"[{os.getpid()}] elastic_build_aarecords_job finished for loop")

                try:
                    for es_handle, operations in operations_by_es_handle.items():
                        for operation in operations:
                            # List of known long records, which we have manually vetted.
                            if operation['id'] not in ['isbngrp:b76feac3cc5a1258aa68f9d6b304dd50']:
                                operation_json = orjson.dumps(operation)
                                if len(operation_json) >= 1000000: # 1MB
                                    print(f"WARNING! WARNING! Extremely long operation: {len(operation_json)=} {operation_json[0:500]}")
                                    # return True
                        elasticsearch.helpers.bulk(es_handle, operations, request_timeout=30)
                except Exception as err:
                    if hasattr(err, 'errors'):
                        print(err.errors)
                    print(f"{repr(err)} ///// {traceback.format_exc()}")
                    print("Got the above error; retrying..")
                    try:
                        for es_handle, operations in operations_by_es_handle.items():
                            elasticsearch.helpers.bulk(es_handle, operations, request_timeout=30)
                    except Exception as err:
                        if hasattr(err, 'errors'):
                            print(err.errors)
                        print(repr(err))
                        print("Got the above error; retrying one more time..")
                        for es_handle, operations in operations_by_es_handle.items():
                            elasticsearch.helpers.bulk(es_handle, operations, request_timeout=30)

                # print(f"[{os.getpid()}] elastic_build_aarecords_job inserted into ES")

                if len(aarecords_all_md5_insert_data) > 0:
                    session.connection().connection.ping(reconnect=True)
                    # Avoiding IGNORE / ON DUPLICATE KEY here because of locking.
                    # WARNING: when trying to optimize this (e.g. if you see this in SHOW PROCESSLIST) know that this is a bit of a bottleneck, but
                    # not a huge one. Commenting out all these inserts doesn't speed up the job by that much.
                    cursor.executemany('INSERT DELAYED INTO aarecords_all_md5 (md5, json_compressed) VALUES (%(md5)s, %(json_compressed)s)', aarecords_all_md5_insert_data)
                    cursor.execute('COMMIT')

                if len(nexusstc_cid_only_insert_data) > 0:
                    session.connection().connection.ping(reconnect=True)
                    # Avoiding IGNORE / ON DUPLICATE KEY here because of locking.
                    # WARNING: when trying to optimize this (e.g. if you see this in SHOW PROCESSLIST) know that this is a bit of a bottleneck, but
                    # not a huge one. Commenting out all these inserts doesn't speed up the job by that much.
                    cursor.executemany('INSERT DELAYED INTO nexusstc_cid_only (nexusstc_id) VALUES (%(nexusstc_id)s)', nexusstc_cid_only_insert_data)
                    cursor.execute('COMMIT')

                if len(temp_md5_with_doi_seen_insert_data) > 0:
                    session.connection().connection.ping(reconnect=True)
                    # Avoiding IGNORE / ON DUPLICATE KEY here because of locking.
                    # WARNING: when trying to optimize this (e.g. if you see this in SHOW PROCESSLIST) know that this is a bit of a bottleneck, but
                    # not a huge one. Commenting out all these inserts doesn't speed up the job by that much.
                    cursor.executemany('INSERT DELAYED INTO temp_md5_with_doi_seen (doi) VALUES (%(doi)s)', temp_md5_with_doi_seen_insert_data)
                    cursor.execute('COMMIT')

                for codes_table_name, aarecords_codes_insert_data in aarecords_codes_insert_data_by_codes_table_name.items():
                    if len(aarecords_codes_insert_data) > 0:
                        for insert_item in aarecords_codes_insert_data:
                            if len(insert_item['code']) > allthethings.utils.AARECORDS_CODES_CODE_LENGTH:
                                raise Exception(f"Code length exceeds allthethings.utils.AARECORDS_CODES_CODE_LENGTH for {insert_item=}")
                            if len(insert_item['aarecord_id']) > allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH:
                                raise Exception(f"Code length exceeds allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH for {insert_item=}")

                        session.connection().connection.ping(reconnect=True)
                        # Avoiding IGNORE / ON DUPLICATE KEY here because of locking.
                        # WARNING: when trying to optimize this (e.g. if you see this in SHOW PROCESSLIST) know that this is a bit of a bottleneck, but
                        # not a huge one. Commenting out all these inserts doesn't speed up the job by that much.
                        cursor.executemany(f"INSERT DELAYED INTO {codes_table_name} (code, aarecord_id) VALUES (%(code)s, %(aarecord_id)s)", aarecords_codes_insert_data)
                        cursor.execute('COMMIT')

                # print(f"[{os.getpid()}] elastic_build_aarecords_job inserted into aarecords_all")
                # print(f"[{os.getpid()}] Processed {len(aarecords)} md5s")

                return False

        except Exception as err:
            print(repr(err))
            traceback.print_tb(err.__traceback__)
            return True

THREADS = 200
CHUNK_SIZE = 25
BATCH_SIZE = 50000

# Locally
if SLOW_DATA_IMPORTS:
    THREADS = 1
    CHUNK_SIZE = 10
    BATCH_SIZE = 1000

# Uncomment to isolate timeouts
# CHUNK_SIZE = 1

# Uncomment to do them one by one
# THREADS = 1
# CHUNK_SIZE = 1
# BATCH_SIZE = 1

#################################################################################################
# ./run flask cli elastic_build_aarecords_all
@cli.cli.command('elastic_build_aarecords_all')
def elastic_build_aarecords_all():
    elastic_build_aarecords_all_internal()

def elastic_build_aarecords_all_internal():
    elastic_build_aarecords_oclc_internal()
    elastic_build_aarecords_edsebk_internal()
    elastic_build_aarecords_cerlalc_internal()
    elastic_build_aarecords_czech_oo42hcks_internal()
    elastic_build_aarecords_gbooks_internal()
    elastic_build_aarecords_goodreads_internal()
    elastic_build_aarecords_isbngrp_internal()
    elastic_build_aarecords_libby_internal()
    elastic_build_aarecords_rgb_internal()
    elastic_build_aarecords_trantor_internal()
    elastic_build_aarecords_hathitrust_internal()
    elastic_build_aarecords_magzdb_internal()
    elastic_build_aarecords_nexusstc_internal()
    elastic_build_aarecords_isbndb_internal()
    elastic_build_aarecords_ol_internal()
    elastic_build_aarecords_duxiu_internal()
    elastic_build_aarecords_ia_internal() # IA depends on tables generated above, so we do it last.
    elastic_build_aarecords_main_internal() # Main depends on tables generated above, so we do it last.
    elastic_build_aarecords_forcemerge_internal()

def build_common(table_name, batch_to_aarecord_ids, primary_id_column='primary_id', additional_where='', additional_select_AGGREGATES='', before_first_primary_id_WARNING_WARNING_THERE_ARE_OTHER_TABLES_THAT_GET_REBUILT=''):
    before_first_primary_id=before_first_primary_id_WARNING_WARNING_THERE_ARE_OTHER_TABLES_THAT_GET_REBUILT
    if before_first_primary_id != '':
        for i in range(5):
            print(f"WARNING! before_first_primary_id set in {table_name} to {before_first_primary_id} (total will be off)!!!!!!!!!!!!")

    with engine.connect() as connection:
        # Get cursor and total count
        cursor = allthethings.utils.get_cursor_ping_conn(connection)
        where_clause = f" WHERE {additional_where} " if additional_where else ""
        sql_count = f"SELECT COUNT(*) AS count FROM {table_name}{where_clause} LIMIT 1"
        cursor.execute(sql_count, {"from": before_first_primary_id})
        total = list(cursor.fetchall())[0]['count']

        with tqdm.tqdm(total=total, bar_format='{l_bar}{bar}{r_bar} {eta}') as pbar:
            def fetch_batch(current_primary_id):
                cursor = allthethings.utils.get_cursor_ping_conn(connection)
                cursor.execute(f'SELECT {primary_id_column} AS primary_id, COUNT(*) AS count {additional_select_AGGREGATES} FROM {table_name} WHERE {additional_where} {"AND" if additional_where else ""} {primary_id_column} > %(from)s GROUP BY {primary_id_column} ORDER BY {primary_id_column} LIMIT %(limit)s', { "from": current_primary_id, "limit": BATCH_SIZE })
                return list(cursor.fetchall())

            batch = fetch_batch(before_first_primary_id)

            while True:
                if not batch:
                    break

                print(
                    f"Processing with {THREADS=} {len(batch)=} records from {table_name} "
                    f"(starting primary_id: {batch[0]['primary_id']}, "
                    f"ending primary_id: {batch[-1]['primary_id']})..."
                )

                # Create a new executor pool for just this batch
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=THREADS,
                    initializer=elastic_build_aarecords_job_init_pool
                ) as executor:
                    futures = []
                    debug_info_by_future = {}
                    batch_count = 0
                    for subbatch in more_itertools.chunked(batch, CHUNK_SIZE):
                        aarecord_ids = batch_to_aarecord_ids(subbatch)
                        future = executor.submit(
                            elastic_build_aarecords_job,
                            aarecord_ids
                        )
                        # Store the future along with how many rows it represents
                        batch_count += sum(row['count'] for row in subbatch)
                        futures.append(future)
                        debug_info_by_future[future] = { 'subbatch': subbatch, 'aarecord_ids': aarecord_ids }

                    # Preload next batch already
                    batch = fetch_batch(batch[-1]['primary_id'])

                    # Wait for futures to complete or time out
                    done, not_done = concurrent.futures.wait(
                        futures,
                        timeout=300,
                        return_when=concurrent.futures.ALL_COMPLETED
                    )
                    if not_done:
                        debug_info_for_not_done = [debug_info_by_future[future] for future in not_done]
                        raise Exception("Some tasks did not finish before timeout." + f"{debug_info_for_not_done=}"[:3000])
                    for future in done:
                        try:
                            result = future.result()
                        except Exception as err:
                            print(f"ERROR in future resolution: {repr(err)}\n\nTraceback:\n{traceback.format_exc()}\n\n" + f"{future=}"[:500])
                            os._exit(1)
                        # If the result object signals an internal error:
                        if result:
                            print("Error detected; exiting")
                            os._exit(1)
                    pbar.update(batch_count)

    print(f"Done with {table_name}!")

#################################################################################################
# ./run flask cli elastic_build_aarecords_ia
@cli.cli.command('elastic_build_aarecords_ia')
def elastic_build_aarecords_ia():
    elastic_build_aarecords_ia_internal()
def elastic_build_aarecords_ia_internal():
    new_tables_internal('aarecords_codes_ia') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.

    with engine.connect() as connection:
        print("Processing from aa_ia_2023_06_metadata+annas_archive_meta__aacid__ia2_records")
        cursor = allthethings.utils.get_cursor_ping_conn(connection)

        # Sanity check: we assume that in annas_archive_meta__aacid__ia2_records we have no libgen-imported records.
        print("Running sanity check on aa_ia_2023_06_metadata")
        cursor.execute('SELECT ia_id FROM aa_ia_2023_06_metadata JOIN annas_archive_meta__aacid__ia2_records ON (aa_ia_2023_06_metadata.ia_id = annas_archive_meta__aacid__ia2_records.primary_id) WHERE aa_ia_2023_06_metadata.libgen_md5 IS NOT NULL LIMIT 500')
        sanity_check_result = list(cursor.fetchall())
        if len(sanity_check_result) > 0:
            raise Exception(f"Sanity check failed: libgen records found in annas_archive_meta__aacid__ia2_records {sanity_check_result=}")

        print("Generating table temp_ia_ids")
        cursor.execute('DROP TABLE IF EXISTS temp_ia_ids')
        cursor.execute('CREATE TABLE temp_ia_ids (ia_id VARCHAR(250) NOT NULL, PRIMARY KEY(ia_id)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin SELECT ia_id FROM (SELECT ia_id, libgen_md5 FROM aa_ia_2023_06_metadata UNION SELECT primary_id AS ia_id, NULL AS libgen_md5 FROM annas_archive_meta__aacid__ia2_records) combined LEFT JOIN aa_ia_2023_06_files USING (ia_id) LEFT JOIN annas_archive_meta__aacid__ia2_acsmpdf_files ON (combined.ia_id = annas_archive_meta__aacid__ia2_acsmpdf_files.primary_id) WHERE aa_ia_2023_06_files.md5 IS NULL AND annas_archive_meta__aacid__ia2_acsmpdf_files.md5 IS NULL AND combined.libgen_md5 IS NULL')

    build_common('temp_ia_ids', lambda batch: [f"ia:{row['primary_id']}" for row in batch], primary_id_column='ia_id')

    with engine.connect() as connection:
        print("Removing table temp_ia_ids")
        cursor = allthethings.utils.get_cursor_ping_conn(connection)
        cursor.execute('DROP TABLE IF EXISTS temp_ia_ids')
        print("Done with IA!")


#################################################################################################
# ./run flask cli elastic_build_aarecords_isbndb
@cli.cli.command('elastic_build_aarecords_isbndb')
def elastic_build_aarecords_isbndb():
    elastic_build_aarecords_isbndb_internal()
def elastic_build_aarecords_isbndb_internal():
    new_tables_internal('aarecords_codes_isbndb', 'aarecords_codes_isbndb_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('isbndb_isbns', lambda batch: [f"isbndb:{row['primary_id']}" for row in batch], primary_id_column='isbn13')
    build_common('isbndb_isbns', lambda batch: [f"isbndb:{isbnlib.ean13(row['primary_id'])}" for row in batch], primary_id_column='isbn10')

#################################################################################################
# ./run flask cli elastic_build_aarecords_ol
@cli.cli.command('elastic_build_aarecords_ol')
def elastic_build_aarecords_ol():
    elastic_build_aarecords_ol_internal()
def elastic_build_aarecords_ol_internal():
    new_tables_internal('aarecords_codes_ol', 'aarecords_codes_ol_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('ol_base', lambda batch: [f"ol:{row['primary_id'].replace('/books/','')}" for row in batch],
        primary_id_column='ol_key', additional_where='ol_key LIKE "/books/OL%%" AND ol_key LIKE "%%M"')

#################################################################################################
# ./run flask cli elastic_build_aarecords_duxiu
@cli.cli.command('elastic_build_aarecords_duxiu')
def elastic_build_aarecords_duxiu():
    elastic_build_aarecords_duxiu_internal()
def elastic_build_aarecords_duxiu_internal():
    new_tables_internal('aarecords_codes_duxiu') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    def duxiu_batch_to_aarecord_ids(batch):
        with engine.connect() as connection:
            cursor = allthethings.utils.get_cursor_ping_conn(connection)
            unrolled_rows = [{"primary_id": row['primary_id'], "byte_offset": int(byte_offset), "byte_length": int(byte_length)} for row in batch for byte_offset, byte_length in zip(row['byte_offsets'].split(','), row['byte_lengths'].split(',')) ]
            lines_bytes = allthethings.utils.get_lines_from_aac_file(cursor, 'duxiu_records', [(row['byte_offset'], row['byte_length']) for row in unrolled_rows])
            ids = []
            for item_index, item in enumerate(unrolled_rows):
                line_bytes = lines_bytes[item_index]
                if item['primary_id'] == 'duxiu_ssid_-1':
                    continue
                if item['primary_id'].startswith('cadal_ssno_hj'):
                    # These are collections.
                    continue
                # TODO: pull these things out into the table?
                if b'dx_20240122__books' in line_bytes:
                    # Skip, because 512w_final_csv is the authority on these records, and has a bunch of records from dx_20240122__books deleted.
                    continue
                if (b'dx_toc_db__dx_toc' in line_bytes) and (b'"toc_xml":null' in line_bytes):
                    # Skip empty TOC records.
                    continue
                if b'dx_20240122__remote_files' in line_bytes:
                    # Skip for now because a lot of the DuXiu SSIDs are actual CADAL SSNOs, and stand-alone records from
                    # remote_files are not useful anyway since they lack metadata like title, author, etc.
                    continue
                ids.append(item['primary_id'].replace('duxiu_ssid_','duxiu_ssid:').replace('cadal_ssno_','cadal_ssno:'))
            return list(set(ids))
    build_common('annas_archive_meta__aacid__duxiu_records', duxiu_batch_to_aarecord_ids,
        additional_where='(primary_id LIKE "duxiu_ssid_%%" OR primary_id LIKE "cadal_ssno_%%")',
        additional_select_AGGREGATES=', GROUP_CONCAT(byte_offset) AS byte_offsets, GROUP_CONCAT(byte_length) AS byte_lengths')

#################################################################################################
# ./run flask cli elastic_build_aarecords_oclc
@cli.cli.command('elastic_build_aarecords_oclc')
def elastic_build_aarecords_oclc():
    elastic_build_aarecords_oclc_internal()
def elastic_build_aarecords_oclc_internal():
    new_tables_internal('aarecords_codes_oclc', 'aarecords_codes_oclc_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__worldcat', lambda batch: [f"oclc:{int(row['primary_id'])}" for row in batch],
        additional_where='primary_id NOT LIKE "library__%%"')

#################################################################################################
# ./run flask cli elastic_build_aarecords_edsebk
@cli.cli.command('elastic_build_aarecords_edsebk')
def elastic_build_aarecords_edsebk():
    elastic_build_aarecords_edsebk_internal()
def elastic_build_aarecords_edsebk_internal():
    new_tables_internal('aarecords_codes_edsebk', 'aarecords_codes_edsebk_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__ebscohost_records', lambda batch: [f"edsebk:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_cerlalc
@cli.cli.command('elastic_build_aarecords_cerlalc')
def elastic_build_aarecords_cerlalc():
    elastic_build_aarecords_cerlalc_internal()
def elastic_build_aarecords_cerlalc_internal():
    new_tables_internal('aarecords_codes_cerlalc', 'aarecords_codes_cerlalc_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__cerlalc_records', lambda batch: [f"cerlalc:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_czech_oo42hcks
@cli.cli.command('elastic_build_aarecords_czech_oo42hcks')
def elastic_build_aarecords_czech_oo42hcks():
    elastic_build_aarecords_czech_oo42hcks_internal()
def elastic_build_aarecords_czech_oo42hcks_internal():
    new_tables_internal('aarecords_codes_czech_oo42hcks', 'aarecords_codes_czech_oo42hcks_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__czech_oo42hcks_records', lambda batch: [f"czech_oo42hcks:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_gbooks
@cli.cli.command('elastic_build_aarecords_gbooks')
def elastic_build_aarecords_gbooks():
    elastic_build_aarecords_gbooks_internal()
def elastic_build_aarecords_gbooks_internal():
    new_tables_internal('aarecords_codes_gbooks', 'aarecords_codes_gbooks_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__gbooks_records', lambda batch: [f"gbooks:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_goodreads
@cli.cli.command('elastic_build_aarecords_goodreads')
def elastic_build_aarecords_goodreads():
    elastic_build_aarecords_goodreads_internal()
def elastic_build_aarecords_goodreads_internal():
    new_tables_internal('aarecords_codes_goodreads', 'aarecords_codes_goodreads_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__goodreads_records', lambda batch: [f"goodreads:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_isbngrp
@cli.cli.command('elastic_build_aarecords_isbngrp')
def elastic_build_aarecords_isbngrp():
    elastic_build_aarecords_isbngrp_internal()
def elastic_build_aarecords_isbngrp_internal():
    new_tables_internal('aarecords_codes_isbngrp', 'aarecords_codes_isbngrp_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__isbngrp_records', lambda batch: [f"isbngrp:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_libby
@cli.cli.command('elastic_build_aarecords_libby')
def elastic_build_aarecords_libby():
    elastic_build_aarecords_libby_internal()
def elastic_build_aarecords_libby_internal():
    new_tables_internal('aarecords_codes_libby', 'aarecords_codes_libby_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__libby_records', lambda batch: [f"libby:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_rgb
@cli.cli.command('elastic_build_aarecords_rgb')
def elastic_build_aarecords_rgb():
    elastic_build_aarecords_rgb_internal()
def elastic_build_aarecords_rgb_internal():
    new_tables_internal('aarecords_codes_rgb', 'aarecords_codes_rgb_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__rgb_records', lambda batch: [f"rgb:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_trantor
@cli.cli.command('elastic_build_aarecords_trantor')
def elastic_build_aarecords_trantor():
    elastic_build_aarecords_trantor_internal()
def elastic_build_aarecords_trantor_internal():
    new_tables_internal('aarecords_codes_trantor', 'aarecords_codes_trantor_for_lookup') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__trantor_records', lambda batch: [f"trantor:{row['primary_id']}" for row in batch])


#################################################################################################
# ./run flask cli elastic_build_aarecords_magzdb
@cli.cli.command('elastic_build_aarecords_magzdb')
def elastic_build_aarecords_magzdb():
    elastic_build_aarecords_magzdb_internal()
def elastic_build_aarecords_magzdb_internal():
    new_tables_internal('aarecords_codes_magzdb') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__magzdb_records', lambda batch: [f"magzdb:{row['primary_id'][len('record_'):]}" for row in batch],
        additional_where='primary_id LIKE "record%%"')

#################################################################################################
# ./run flask cli elastic_build_aarecords_nexusstc
@cli.cli.command('elastic_build_aarecords_nexusstc')
def elastic_build_aarecords_nexusstc():
    elastic_build_aarecords_nexusstc_internal()
def elastic_build_aarecords_nexusstc_internal():
    new_tables_internal('aarecords_codes_nexusstc') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    with Session(engine) as session:
        session.connection().connection.ping(reconnect=True)
        cursor = session.connection().connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute('DROP TABLE IF EXISTS nexusstc_cid_only')
        cursor.execute('CREATE TABLE nexusstc_cid_only (nexusstc_id VARCHAR(200) NOT NULL, PRIMARY KEY (nexusstc_id)) ENGINE=MyISAM DEFAULT CHARSET=ascii COLLATE=ascii_bin ROW_FORMAT=FIXED')
    build_common('annas_archive_meta__aacid__nexusstc_records', lambda batch: [f"nexusstc:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_hathitrust
@cli.cli.command('elastic_build_aarecords_hathitrust')
def elastic_build_aarecords_hathitrust():
    elastic_build_aarecords_hathitrust_internal()
def elastic_build_aarecords_hathitrust_internal():
    new_tables_internal('aarecords_codes_hathi') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.
    build_common('annas_archive_meta__aacid__hathitrust_records', lambda batch: [f"hathi:{row['primary_id']}" for row in batch])

#################################################################################################
# ./run flask cli elastic_build_aarecords_main
@cli.cli.command('elastic_build_aarecords_main')
def elastic_build_aarecords_main():
    elastic_build_aarecords_main_internal()
def elastic_build_aarecords_main_internal():
    new_tables_internal('aarecords_codes_main') # WARNING! Update the upload excludes, and dump_mariadb_omit_tables.txt.

    print("Deleting main ES indices")
    for index_name, es_handle in allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING.items():
        if index_name in allthethings.utils.MAIN_SEARCH_INDEXES:
            es_handle.options(ignore_status=[400,404]).indices.delete(index=index_name) # Old
            for virtshard in range(0, 100): # Out of abundance, delete up to a large number
                es_handle.options(ignore_status=[400,404]).indices.delete(index=f'{index_name}__{virtshard}')
    if not SLOW_DATA_IMPORTS:
        print("Sleeping 3 minutes (no point in making this less)")
        time.sleep(60*3)
    print("Creating main ES indices")
    for index_name, es_handle in allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING.items():
        if index_name in allthethings.utils.MAIN_SEARCH_INDEXES:
            for full_index_name in allthethings.utils.all_virtshards_for_index(index_name):
                es_handle.indices.create(wait_for_active_shards=1,index=full_index_name, body=es_create_index_body)

    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        cursor.execute('DROP TABLE IF EXISTS aarecords_all_md5')
        cursor.execute('CREATE TABLE aarecords_all_md5 (md5 BINARY(16) NOT NULL, json_compressed LONGBLOB NOT NULL) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        cursor.execute('DROP TABLE IF EXISTS temp_md5_with_doi_seen')
        cursor.execute('CREATE TABLE temp_md5_with_doi_seen (doi VARBINARY(1000)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')

    build_common('computed_all_md5s', lambda batch: [f"md5:{row['primary_id'].hex()}" for row in batch], primary_id_column='md5')

    with engine.connect() as connection:
        print("Adding index to temp_md5_with_doi_seen")
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        cursor.execute('ALTER TABLE temp_md5_with_doi_seen ADD INDEX (doi)')

        print("Creating scihub_dois_not_yet_seen: Filter out 'doi:' records that already have an md5. We don't need standalone records for those.")
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        cursor.execute('DROP TABLE IF EXISTS scihub_dois_not_yet_seen')
        cursor.execute('CREATE TABLE scihub_dois_not_yet_seen (doi varchar(250) NOT NULL, PRIMARY KEY(doi)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin SELECT scihub_dois.doi FROM scihub_dois LEFT JOIN temp_md5_with_doi_seen ON (CONVERT(LOWER(scihub_dois.doi) USING BINARY) = temp_md5_with_doi_seen.doi) WHERE temp_md5_with_doi_seen.doi IS NULL')

    build_common('scihub_dois_not_yet_seen', lambda batch: [f"doi:{row['primary_id'].lower()}" for row in batch], primary_id_column='doi')
    build_common('nexusstc_cid_only', lambda batch: [f"nexusstc_download:{row['primary_id']}" for row in batch], primary_id_column='nexusstc_id')

    print("Adding index to aarecords_all_md5")
    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        # IGNORE in case we got some duplicates from rerunning the above.
        cursor.execute('ALTER IGNORE TABLE aarecords_all_md5 ADD PRIMARY KEY (md5)')

    print("Cleanup")
    with Session(engine) as session:
        session.connection().connection.ping(reconnect=True)
        cursor = session.connection().connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute('DROP TABLE temp_md5_with_doi_seen')
        cursor.execute('DROP TABLE scihub_dois_not_yet_seen')

    print("Done with main!")

#################################################################################################
# ./run flask cli elastic_build_aarecords_forcemerge
@cli.cli.command('elastic_build_aarecords_forcemerge')
def elastic_build_aarecords_forcemerge():
    elastic_build_aarecords_forcemerge_internal()
def elastic_build_aarecords_forcemerge_internal():
    for index_name, es_handle in allthethings.utils.SEARCH_INDEX_TO_ES_MAPPING.items():
        for full_index_name in allthethings.utils.all_virtshards_for_index(index_name):
            print(f'Calling forcemerge on {full_index_name=}')
            es_handle.options(ignore_status=[400,404]).indices.forcemerge(index=full_index_name, wait_for_completion=True, request_timeout=300)

#################################################################################################
# Fill make aarecords_codes with numbers based off ROW_NUMBER and
# DENSE_RANK MySQL functions, but precomupted because they're expensive.
#
# ./run flask cli mysql_build_aarecords_codes_numbers
@cli.cli.command('mysql_build_aarecords_codes_numbers')
def mysql_build_aarecords_codes_numbers():
    mysql_build_aarecords_codes_numbers_internal()
def mysql_build_aarecords_codes_numbers_internal():
    CODE_PREFIX_SAMPLING = 0.05             # COLUMN_ADD is too expensive in this case, so we compromise
    CODE_PREFIX_PARTITION_SIZE = 50_000_000 # When crossed, next prefix will begin a new partition
    MAX_DB_CONNECTIONS = 5               
    if SLOW_DATA_IMPORTS:
        CODE_PREFIX_PARTITION_SIZE = 500000

    tables = list(set(AARECORD_ID_PREFIX_TO_CODES_TABLE_NAME.values()))
    
    prefix_counts_summed = {}
    partition_defs = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_DB_CONNECTIONS, THREADS)) as executor:
        def collect_code_prefixes(tablename):
            with engine.connect() as connection:
                connection.connection.ping(reconnect=True)
                cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)

                cursor.execute(f'SET @code_prefix = "", @cnt := 0, @prev = ""')
                cursor.execute(f"""ANALYZE SELECT 
                                LAST_VALUE(
                                    IF(RAND() < {CODE_PREFIX_SAMPLING} OR NOT COLUMN_EXISTS(@code_prefix, SUBSTRING_INDEX(code, ":", 1)),
                                        IF(
                                            SUBSTRING_INDEX(code, ":", 1) = @prev, 
                                            @cnt := @cnt + 1, 
                                            LAST_VALUE(@cnt := NVL(COLUMN_GET(@code_prefix := COLUMN_ADD(@code_prefix, @prev, @cnt), SUBSTRING_INDEX(code, ":", 1) as int), 0) + 1, @prev := CONVERT(SUBSTRING_INDEX(code, ":", 1) USING utf8mb4)) 
                                        ), NULL
                                    ), 0) as p
                                FROM {tablename}""")
                cursor.execute(f'SET @code_prefix = COLUMN_ADD(@code_prefix, @prev, @cnt);')

                cursor.execute(f'SELECT COLUMN_JSON(@code_prefix) as code_prefix;')
                return orjson.loads(cursor.fetchone()['code_prefix'])

        start = time.perf_counter()
        results = list(executor.map(collect_code_prefixes, tables))

        prefix_counts_by_table = dict(zip(tables, results))
        prefix_counts_summed = {k: sum(d.get(k, 0) for d in results) for k in {k for d in results for k in d if k}} 
        prefix_counts_in_idx_order = sorted(prefix_counts_summed.items(), key=lambda x: x[0]) 

        print(f'Code prefix list ({len(prefix_counts_summed)}) collected in {time.perf_counter() - start:.2f}')
        est_multiplier = max(1 / CODE_PREFIX_SAMPLING, 1)
        
        partition_defs = []
        acc = [0, [], 0, ""]
        for code_prefix, count in prefix_counts_in_idx_order:
            acc[0] += round(count * est_multiplier)
            acc[1].append(code_prefix)
            if acc[0] > CODE_PREFIX_PARTITION_SIZE or code_prefix == prefix_counts_in_idx_order[-1][0]:
                partition_defs.append({"aprox_count": acc[0], "n": acc[2], "bounds": (acc[3], code_prefix + ";"), "prefix_list": acc[1]})
                acc = [0, [], len(partition_defs), code_prefix + ";"] 

        print(f"Deleting old aarecords_codes table if it exists (not ideal, but it takes up too much space otherwise)...")
        with engine.connect() as connection:
            connection.connection.ping(reconnect=True)
            cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
            cursor.execute('DROP TABLE IF EXISTS aarecords_codes')
        
        print(f"Will build {len(partition_defs)} partitions of aprox {sum([x['aprox_count'] for x in partition_defs])} records")

        def build_partition(opts):
            with engine.connect() as connection:
                connection.connection.ping(reconnect=True)
                cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)

                # If a table doesn't have anything from the code range, skip it
                needed_tables = list(filter(lambda x: any([prefix in opts["prefix_list"] for prefix in prefix_counts_by_table[x]]), tables))
                cursor.execute(f'CREATE OR REPLACE TEMPORARY TABLE aarecords_codes_union (code VARBINARY(680) NOT NULL, aarecord_id VARBINARY(300) NOT NULL) ENGINE=MERGE UNION=({", ".join(needed_tables)}) INSERT_METHOD=NO;')

                start = time.perf_counter()
                # This temptable would be created by the query below anyway (except with udfs), just making it more obvious, 
                # also there's a good chance it's faster this way
                cursor.execute('CREATE OR REPLACE TEMPORARY TABLE aarecords_codes_new_internal (code VARBINARY(680) NOT NULL, aarecord_id VARBINARY(300) NOT NULL, prefix VARBINARY(20) NOT NULL) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin SELECT code, aarecord_id, SUBSTRING_INDEX(aarecord_id, ":", 1) as prefix FROM aarecords_codes_union WHERE code < %(upper_bound)s AND code >=  %(lower_bound)s ORDER BY code, aarecord_id', {"lower_bound": opts["bounds"][0], "upper_bound": opts["bounds"][1]})
                sort_time = time.perf_counter() - start

                # Substitute for ROW_NUMBER and DENSE_RANK window functions (proven too slow)
                # --------------------------------------------------------------
                start = time.perf_counter()
                cursor.execute(f'SET @rownum := 0, @drank := 0, @prev_code := "", @prev_aarecord_id := "", @prn := "", @pdr := "", @prn_val := 0, @pdr_val := 0, @prev_prefix := "", @code_same := 0, @prefix_same := 0, @code_and_id_same := 0')
                cursor.execute(f"""CREATE OR REPLACE TABLE aarecords_codes_new_p{opts["n"]} (code VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, aarecord_id VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH}) NOT NULL, aarecord_id_prefix VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_PREFIX_LENGTH}) NOT NULL, row_number_order_by_code BIGINT NOT NULL DEFAULT 0, dense_rank_order_by_code BIGINT NOT NULL DEFAULT 0, row_number_partition_by_aarecord_id_prefix_order_by_code BIGINT NOT NULL DEFAULT 0, dense_rank_partition_by_aarecord_id_prefix_order_by_code BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (code, aarecord_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
                                IGNORE SELECT LAST_VALUE(@code_and_id_same := (y.code = @prev_code AND y.aarecord_id = @prev_aarecord_id), @code_same := y.code = @prev_code, @prefix_same := prefix = @prev_prefix, IF(@code_and_id_same, @rownum, @rownum := @rownum + 1)) AS row_number_order_by_code,
                                IF(@code_same, @drank, @drank := @drank + 1) AS dense_rank_order_by_code,
                                IF(@code_and_id_same, @prn_val, IF(@prefix_same, 
                                  @prn_val := @prn_val + 1, 
                                  @prn_val := IFNULL(COLUMN_GET(@prn := COLUMN_ADD(@prn, @prev_prefix, @prn_val), prefix as int), 0) + 1
                                )) as row_number_partition_by_aarecord_id_prefix_order_by_code,
                                IF(@code_and_id_same, @pdr_val, IF(@prefix_same, 
                                  IF(@code_same, @pdr_val, @pdr_val := @pdr_val + 1), 
                                  @pdr_val := IFNULL(COLUMN_GET(@pdr := COLUMN_ADD(@pdr, @prev_prefix, @pdr_val), prefix as int), 0) + 1
                                )) as dense_rank_partition_by_aarecord_id_prefix_order_by_code,
                                IF(@code_same, @prev_code, @prev_code := code) as code,
                                (@prev_aarecord_id := aarecord_id) as aarecord_id,
                                IF(@prefix_same, prefix, @prev_prefix := prefix) AS aarecord_id_prefix
                                FROM aarecords_codes_new_internal y 
                                ;""") # No order by, expecting the table to be sorted
                agg_time = time.perf_counter() - start

                start = time.perf_counter()
                cursor.execute(f'ALTER TABLE aarecords_codes_new_p{opts["n"]} ADD INDEX (aarecord_id_prefix, code, aarecord_id)');
                index_time = time.perf_counter() - start

                opts["time"] = {
                    "1_sort": sort_time,
                    "2_agg": agg_time,
                    "3_index": index_time,
                }

        start = time.perf_counter()
        futures = list(map(lambda x: executor.submit(build_partition, x), partition_defs))
        complete = [future.result() for future in concurrent.futures.as_completed(futures)] # fail fast

        if SLOW_DATA_IMPORTS:
            print(f'Partitioning breakdown: { orjson.dumps(partition_defs, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2 ).decode("utf-8") }')

        print(f'Partitions built in {time.perf_counter() - start:.2f} (sort, agg, index) = {(sum([x["time"]["1_sort"] for x in partition_defs]), sum([x["time"]["2_agg"] for x in partition_defs]), sum([x["time"]["3_index"] for x in partition_defs]))}')
    
    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)

        if SLOW_DATA_IMPORTS:
            cursor.execute('DROP TABLE IF EXISTS aarecords_codes_new')
            cursor.execute('DROP TABLE IF EXISTS aarecords_codes_prefixes_new')

        cursor.execute(f'CREATE TABLE aarecords_codes_new (row_number_order_by_code BIGINT NOT NULL DEFAULT 0, dense_rank_order_by_code BIGINT NOT NULL DEFAULT 0, row_number_partition_by_aarecord_id_prefix_order_by_code BIGINT NOT NULL DEFAULT 0, dense_rank_partition_by_aarecord_id_prefix_order_by_code BIGINT NOT NULL DEFAULT 0, code VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, aarecord_id VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH}) NOT NULL, aarecord_id_prefix VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_PREFIX_LENGTH}) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin PARTITION BY RANGE COLUMNS(code) (partition discard values less than (""))')
        cursor.execute(f'ALTER TABLE aarecords_codes_new ADD PRIMARY KEY (code, aarecord_id), add INDEX (aarecord_id_prefix, code, aarecord_id)');

        #   Partitions are checked for unique pk and respecting partition rules when we already know the data is correct
        # ! MariaDB 11.4 onwards supports CONVERT TABLE ... WITHOUT VALIDATION, change to that when updated
        start = time.perf_counter()
        for opts in partition_defs:
            cursor.execute(f'ALTER TABLE aarecords_codes_new CONVERT TABLE aarecords_codes_new_p{opts["n"]} TO PARTITION p{opts["n"]} VALUES LESS THAN ("{opts["bounds"][1]}")')

        print(f'Partitions validated in {time.perf_counter() - start:.2f}')

        cursor.execute(f'CREATE TABLE aarecords_codes_prefixes_new (code_prefix VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, PRIMARY KEY (code_prefix)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
        cursor.executemany(f'INSERT INTO aarecords_codes_prefixes_new (code_prefix) VALUES (%s)', [(code_prefix,) for code_prefix in prefix_counts_summed.keys()])

        if SLOW_DATA_IMPORTS:
            connection.connection.ping(reconnect=True)
            cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
            for partition in partition_defs:
                cursor.execute(f'SELECT * FROM aarecords_codes_new WHERE code >= "{partition["bounds"][0]}" and code < "{partition["bounds"][1]}" ORDER BY code, aarecord_id LIMIT 1')
                first_in_prefix = cursor.fetchone()
                start = time.perf_counter()
                cursor.execute(f'SELECT MIN(correct) AS min_correct FROM (SELECT ((row_number_order_by_code = ROW_NUMBER() OVER (ORDER BY code, aarecord_id) + {first_in_prefix["row_number_order_by_code"] - 1}) AND (dense_rank_order_by_code = DENSE_RANK() OVER (ORDER BY code) + {first_in_prefix["dense_rank_order_by_code"] - 1}) AND (row_number_partition_by_aarecord_id_prefix_order_by_code = ROW_NUMBER() OVER (PARTITION BY aarecord_id_prefix ORDER BY code, aarecord_id) + {first_in_prefix["row_number_partition_by_aarecord_id_prefix_order_by_code"] - 1}) AND (dense_rank_partition_by_aarecord_id_prefix_order_by_code = DENSE_RANK() OVER (PARTITION BY aarecord_id_prefix ORDER BY code) + {first_in_prefix["dense_rank_partition_by_aarecord_id_prefix_order_by_code"] - 1})) AS correct FROM (select * from aarecords_codes_new WHERE code >= "{partition["bounds"][0]}" and code < "{partition["bounds"][1]}" ORDER BY code, aarecord_id LIMIT 1000000) y) x')
                if str(cursor.fetchone()['min_correct']) != '1':
                    raise Exception(f'mysql_build_aarecords_codes_numbers_internal final sanity check failed for p#{partition["n"]}!')

        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        cursor.execute('DROP TABLE IF EXISTS aarecords_codes')
        cursor.execute('COMMIT')
        cursor.execute('ALTER TABLE aarecords_codes_new RENAME aarecords_codes')
        cursor.execute('COMMIT')
        cursor.execute('DROP TABLE IF EXISTS aarecords_codes_prefixes')
        cursor.execute('COMMIT')
        cursor.execute('ALTER TABLE aarecords_codes_prefixes_new RENAME aarecords_codes_prefixes')
        cursor.execute('COMMIT')
    print(f"Done!")

#################################################################################################
# Add a better primary key to the aarecords_codes_* tables so we get better diffs in bin/check-dumps.
#
# ./run flask cli mysql_make_aarecords_codes_tables_without_id_for_check_dumps
@cli.cli.command('mysql_make_aarecords_codes_tables_without_id_for_check_dumps')
def mysql_make_aarecords_codes_tables_without_id_for_check_dumps():
    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)
        for table_name in list(dict.fromkeys(AARECORD_ID_PREFIX_TO_CODES_TABLE_NAME.values())):
            cursor.execute(f'DROP TABLE IF EXISTS {table_name}_without_id')
            cursor.execute(f'CREATE TABLE {table_name}_without_id (code VARBINARY({allthethings.utils.AARECORDS_CODES_CODE_LENGTH}) NOT NULL, aarecord_id VARBINARY({allthethings.utils.AARECORDS_CODES_AARECORD_ID_LENGTH}) NOT NULL, PRIMARY KEY (code, aarecord_id)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin SELECT code, aarecord_id FROM {table_name};')
        

    print("Done!")


#################################################################################################
# ./run flask cli mariapersist_reset
@cli.cli.command('mariapersist_reset')
def mariapersist_reset():
    print("Erasing entire persistent database ('mariapersist')! Did you double-check that any production databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 2 seconds to abort..")
    time.sleep(2)
    mariapersist_reset_internal()

def mariapersist_reset_internal():
    # Per https://stackoverflow.com/a/4060259
    __location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))

    mariapersist_engine_multi = create_engine(mariapersist_url, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    cursor = mariapersist_engine_multi.raw_connection().cursor()

    # From https://stackoverflow.com/a/8248281
    cursor.execute("SELECT concat('DROP TABLE IF EXISTS `', table_name, '`;') FROM information_schema.tables WHERE table_schema = 'mariapersist' AND table_name LIKE 'mariapersist_%';")
    delete_all_query = "\n".join([item[0] for item in cursor.fetchall()])
    if len(delete_all_query) > 0:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        cursor.execute(delete_all_query)
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1; COMMIT;")

    cursor.execute(pathlib.Path(os.path.join(__location__, 'mariapersist_migration.sql')).read_text())
    cursor.execute("COMMIT")
    cursor.close()

    annatst_secret_key = allthethings.utils.secret_key_from_account_id('ANNATST')
    print(f"Login to ANNTST account with secret key: {annatst_secret_key}")

#################################################################################################
# Send test email
# ./run flask cli send_test_email <email_addr>
@cli.cli.command('send_test_email')
@click.argument("email_addr")
def send_test_email(email_addr):
    email_msg = flask_mail.Message(subject="Hello", body="Hi there, this is a test!", recipients=[email_addr])
    mail.send(email_msg)

#################################################################################################
# Reprocess gift cards from "email_data" for the last few days.
# ./run flask cli reprocess_gift_cards <since_days>
@cli.cli.command('reprocess_gift_cards')
@click.argument("since_days")
def reprocess_gift_cards(since_days):
    with Session(mariapersist_engine) as mariapersist_session:
        cursor = allthethings.utils.get_cursor_ping(mariapersist_session)
        datetime_from = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=int(since_days))
        cursor.execute('SELECT * FROM mariapersist_donations WHERE created >= %(datetime_from)s AND processing_status IN (0,1,2,3,4) AND json LIKE \'%%"gc_notify_debug"%%\'', { "datetime_from": datetime_from })
        donations = list(cursor.fetchall())
        for donation in tqdm.tqdm(donations, bar_format='{l_bar}{bar}{r_bar} {eta}'):
            for debug_data in orjson.loads(donation['json'])['gc_notify_debug']:
                if 'email_data' in debug_data:
                    allthethings.utils.gc_notify(cursor, debug_data['email_data'].encode(), dont_store_errors=True)

#################################################################################################
# Check payment2 API for all payments in the last few days.
# ./run flask cli payment2_check_recent_days <since_days> <sleep_seconds>
@cli.cli.command('payment2_check_recent_days')
@click.argument("since_days")
@click.argument("sleep_seconds")
def payment2_check_recent_days(since_days, sleep_seconds):
    with Session(mariapersist_engine) as mariapersist_session:
        cursor = allthethings.utils.get_cursor_ping(mariapersist_session)
        datetime_from = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=int(since_days))
        # Don't close "payment2" quote, so we also catch strings like "payment2cashapp".
        cursor.execute('SELECT * FROM mariapersist_donations WHERE created >= %(datetime_from)s AND processing_status IN (0,2,3,4) AND json LIKE \'%%"method":"payment2%%\'', { "datetime_from": datetime_from })
        donations = list(cursor.fetchall())
        for donation in tqdm.tqdm(donations, bar_format='{l_bar}{bar}{r_bar} {eta}'):
            donation_json = orjson.loads(donation['json'])
            payment2_status, payment2_request_success, payment2_confirmed = allthethings.utils.payment2_check(lambda: allthethings.utils.get_cursor_ping(mariapersist_session), donation_json['payment2_request']['payment_id'])
            if not payment2_request_success:
                raise Exception("Not payment2_request_success in donation_page")
            if payment2_confirmed:
                print(f"CONFIRMED: {donation['donation_id']=}")
            time.sleep(int(sleep_seconds))
        # Ping before closing, otherwise it might error at the very end!
        allthethings.utils.get_cursor_ping(mariapersist_session)
    print("Done")


#################################################################################################
# Dump `isbn13:` codes to a file.
#
# Format is bencoded file (compressed with zstd), with the following layout:
#
# * dictionary with `aarecord_id_prefix` string mapped to bitmap of 2 million ISBNs (978 and 979).
#   * bitmap specification: pairs of 32 bit numbers (<isbn_streak> <gap_size>)* followed by a
#     single final <isbn_streak>.
#     * "isbn_streak" represents how many ISBNs we have in a row (starting with 9780000000002).
#       When iterating ISBNs we omit the final check digit, so in the 978* and 979* ranges we
#       find 1 billion ISBNs each, or 2 billion total.
#     * "gap_size" represents how many ISBNs are missing in a row. The last one is implied and
#       therefore omitted.
#   * `aarecord_id_prefix` values without any `isbn13:` codes are not included.
#
# We considered the [binsparse spec](https://graphblas.org/binsparse-specification/) but it's not
# mature enough.
#
# ./run flask cli dump_isbn13_codes_benc
@cli.cli.command('dump_isbn13_codes_benc')
def dump_isbn13_codes_benc():
    with engine.connect() as connection:
        connection.connection.ping(reconnect=True)
        cursor = connection.connection.cursor(pymysql.cursors.SSDictCursor)

        timestamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"/exports/codes_benc/aa_isbn13_codes_{timestamp}.benc.zst"
        print(f"Writing to {filename}...")

        with open(filename, "wb") as fh:
            with zstandard.ZstdCompressor(level=22, threads=-1).stream_writer(fh) as compressor:
                compressor.write(b'd')

                cursor.execute('SELECT DISTINCT aarecord_id_prefix FROM aarecords_codes')
                aarecord_id_prefixes = [s.decode() for s in allthethings.utils.fetch_scalars(cursor)]
                print(f"{aarecord_id_prefixes=}")

                for aarecord_id_prefix in aarecord_id_prefixes:
                    print(f"Processing aarecord_id_prefix '{aarecord_id_prefix}'...")

                    cursor.execute('SELECT code FROM aarecords_codes WHERE code LIKE "isbn13:%%" AND aarecord_id_prefix = %(aarecord_id_prefix)s LIMIT 1', {"aarecord_id_prefix": aarecord_id_prefix});
                    if len(list(cursor.fetchall())) == 0:
                        print(f"No isbn13: codes in '{aarecord_id_prefix}', skipping...")
                        continue

                    compressor.write(f"{len(aarecord_id_prefix)}:{aarecord_id_prefix}".encode())

                    prefix_buffer = io.BytesIO()
                    last_isbn = 978000000000-1
                    isbn_streak = 0
                    with tqdm.tqdm(total=2000000000, bar_format='{l_bar}{bar}{r_bar} {eta}') as pbar:
                        while True:
                            cursor.execute('SELECT DISTINCT code FROM aarecords_codes WHERE aarecord_id_prefix = %(aarecord_id_prefix)s AND code > CONCAT("isbn13:", %(last_isbn)s, "Z") AND code LIKE "isbn13:%%" ORDER BY code LIMIT 10000', { "aarecord_id_prefix": aarecord_id_prefix, "last_isbn": str(last_isbn) })
                            # Strip off "isbn13:" and check digit, then deduplicate.
                            isbns = list(dict.fromkeys([int(code[7:-1]) for code in allthethings.utils.fetch_scalars(cursor)]))
                            if len(isbns) == 0:
                                break
                            for isbn in isbns:
                                gap_size = isbn-last_isbn-1
                                # print(f"{isbn=} {last_isbn=} {gap_size=}")
                                if gap_size == 0:
                                    isbn_streak += 1
                                else:
                                    prefix_buffer.write(isbn_streak.to_bytes(4, byteorder='little', signed=False))
                                    prefix_buffer.write(gap_size.to_bytes(4, byteorder='little', signed=False))
                                    isbn_streak = 1
                                pbar.update(isbn - last_isbn)
                                last_isbn = isbn
                        pbar.update((978000000000+2000000000-1) - last_isbn)
                        prefix_buffer.write(isbn_streak.to_bytes(4, byteorder='little', signed=False))

                    prefix_buffer_bytes = prefix_buffer.getvalue()
                    compressor.write(f"{len(prefix_buffer_bytes)}:".encode())
                    compressor.write(prefix_buffer_bytes)
                compressor.write(b'e')
                print("Done")

