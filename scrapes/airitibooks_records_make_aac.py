import os
import orjson
import re
import shortuuid
import datetime
from bs4 import BeautifulSoup, NavigableString, Tag

timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
output_file = f"annas_archive_meta__aacid__airitibooks_records__{timestamp}--{timestamp}.jsonl"

seen_ids = set()

def process_li(li, source_filename):
    global seen_ids

    # Initialize the result dictionary
    result = {}
    
    # Extract the publication ID from the onclick attribute
    publication_id = None
    a_tags = li.find_all('a', onclick=True)
    for a in a_tags:
        onclick = a.get('onclick')
        if 'Detail(' in onclick:
            id_start = onclick.find("Detail('") + len("Detail('")
            id_end = onclick.find("')", id_start)
            publication_id = onclick[id_start:id_end]
            break
    if publication_id is None:
        raise Exception(f"publication_id is None for {source_filename=} {li=}")
    result['id'] = publication_id
    if publication_id in seen_ids:
        return None
    seen_ids.add(publication_id)
    
    # Extract the ISBN from the image source
    isbn = None
    src = None
    img = li.find('img', src=True)
    if img:
        src = img['src']
        filename = src.split('/')[-1]
        isbn = os.path.splitext(filename)[0]
    result['isbn'] = isbn
    result['cover_url'] = src

    result['source_filename'] = source_filename
    
    # Extract the book name
    bookname_div = li.find('div', class_='bookname')
    bookname = bookname_div.get_text(strip=True) if bookname_div else None
    result['bookname'] = bookname
    
    # Extract the publication year
    year_span = li.find('span', class_='year')
    year = year_span.get_text(strip=True) if year_span else None
    result['year'] = year
    
    # Extract the authors
    authors = []
    author_divs = li.find_all('div', class_='book_all_info_line')
    for div in author_divs:
        t_div = div.find('div', class_=lambda x: x and 'book_all_info_t' in x)
        if t_div and t_div.get_text(strip=True) == '作者':
            c_div = div.find('div', class_='book_all_info_c')
            if c_div:
                contents = c_div.contents
                i = 0
                while i < len(contents):
                    content = contents[i]
                    if isinstance(content, Tag) and content.name == 'a':
                        name = content.get_text(strip=True)
                        type = None
                        i += 1
                        # Collect following NavigableStrings to get type if any
                        while i < len(contents):
                            next_content = contents[i]
                            if isinstance(next_content, NavigableString):
                                text = next_content.strip()
                                i += 1
                                if text:
                                    # Extract type from text if in parentheses
                                    match = re.match(r'^\((.*?)\)', text)
                                    if match:
                                        type = match.group(1)
                                    # Break after processing this text
                                    break
                            else:
                                # Not NavigableString, possibly another Tag
                                break
                        authors.append({'name': name, 'type': type})
                    else:
                        i += 1
            break
    result['authors'] = authors

    result['bookmark_json'] = None
    if isbn is not None:
        try:
            with open(f"/raw_bookmark_jsons/{isbn}.json", 'r', encoding='utf-8') as fin:
                result['bookmark_json'] = orjson.loads(fin.read())
        except:
            pass
    
    uuid = shortuuid.uuid()
    return {
        "aacid": f"aacid__airitibooks_records__{timestamp}__{publication_id}__{uuid}",
        "metadata": result,
    }

html_dir = "/htmls/htmls"
html_files = [os.path.join(html_dir, f) for f in os.listdir(html_dir) if f.endswith('.html')]

with open(output_file, 'wb') as fout:
    for html_file in html_files:
        # print(f"{html_file=}")
        with open(html_file, 'r', encoding='utf-8') as fin:
            soup = BeautifulSoup(fin, 'html.parser')
            li_elements = soup.find_all('li', attrs={'name': 'PublicationID'})
            for li in li_elements:
                # print(f"{li=}")
                result = process_li(li, html_file.rsplit('/', 1)[-1])
                # Write the result as a JSON line
                if result is not None:
                    fout.write(orjson.dumps(result, option=orjson.OPT_APPEND_NEWLINE))
