import py7zr
import pikepdf
import natsort
import orjson
import os
import tqdm
import concurrent.futures
import traceback

def handle_file(input_tuple):
    input_filename_index, input_filename_7z = input_tuple

    abnt_text = None
    try:
        abnt_text = orjson.loads(open(input_filename_7z.rsplit('/', 1)[0] + '/abnt.txt', 'r').read())
    except Exception as e:
        print(f"Warning, abnt_text didn't work {input_filename_7z=} {e=}")
    with py7zr.SevenZipFile(input_filename_7z, 'r') as zipfile:
        zip_contents = zipfile.readall()
        sorted_filenames = natsort.natsorted(zip_contents.keys())
        pdf = pikepdf.Pdf.new()
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta['pdf:Producer'] = "Anna’s Archive, 2024"
            if abnt_text is not None:
                meta['dc:title'] = abnt_text
        for filename in sorted_filenames:
            if not filename.endswith('.pdf'):
                raise Exception(f"Filename not ending in pdf: {filename=}")

            src_pdf = pikepdf.Pdf.open(zip_contents[filename])
            pdf.pages.extend(src_pdf.pages)
        if abnt_text is not None:
            abnt_text_for_filename = abnt_text.replace('/','\\')
            output_filename = f"/output/{input_filename_index}__ {abnt_text_for_filename}.pdf"
        else:
            output_filename = f"/output/{input_filename_index}.pdf"
        pdf.save(output_filename, deterministic_id=True, linearize=True, recompress_flate=True)
        print(f"Saved to {output_filename=}")

if __name__=='__main__':
    input_prefix_directory = '/input/'
    input_filenames = set()
    for walk_root, walk_dirs, walk_files in os.walk(input_prefix_directory):
        if walk_root.startswith(input_prefix_directory):
            walk_root = walk_root[len(input_prefix_directory):]
        for walk_filename in walk_files:
            if walk_filename.endswith('.7z'):
                if walk_root == '':
                    input_filenames.add(walk_filename)
                else:
                    input_filenames.add(walk_root + '/' + walk_filename)
    print(f"Found {len(input_filenames)=}")

    THREADS=55

    with tqdm.tqdm(total=len(input_filenames)) as pbar:
        # with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        with concurrent.futures.ProcessPoolExecutor(max_workers=THREADS, max_tasks_per_child=1) as executor:
            futures = set()
            def process_future():
                # print(f"Futures waiting: {len(futures)}")
                (done, not_done) = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                # print(f"Done!")
                for future_done in done:
                    futures.remove(future_done)
                    pbar.update(1)
                    err = future_done.exception()
                    if err:
                        print(f"ERROR IN FUTURE RESOLUTION!!!!! {repr(err)}\n\n/////\n\n{traceback.format_exc()}")
                    else:
                        future_done.result()
            for input_filename_index, input_filename_7z in enumerate(input_filenames):
                futures.add(executor.submit(handle_file, (input_filename_index, input_filename_7z)))
                if len(futures) > THREADS*2:
                    process_future()
            while len(futures) > 0:
                process_future()
