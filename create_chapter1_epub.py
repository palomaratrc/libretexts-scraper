#!/usr/bin/env python3
"""
Create a complete EPUB file from HTML files in the book/ directory.
Extracts content from HTML files, downloads images, and creates a proper EPUB structure.

By default, auto-discovers all .html files in the book/ directory, sorts them naturally,
and extracts titles from the HTML content.

Usage:
    python create_chapter1_epub.py [options]

Options:
    --single-page              Generate EPUB with all content in a single page (default: multi-page)
    --files FILE [FILE ...]    Specific HTML files to process (supports wildcards)

Examples:
    python create_chapter1_epub.py                              # Auto-discover, multi-page
    python create_chapter1_epub.py --single-page                # Auto-discover, single-page
    python create_chapter1_epub.py --files 1.1*.html            # Process files matching pattern
    python create_chapter1_epub.py --files file1.html file2.html # Process specific files
"""

import os
import re
import sys
import hashlib
import urllib.request
import urllib.parse
import argparse
import glob
from bs4 import BeautifulSoup
from datetime import datetime
import zipfile
import uuid

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BOOK_DIR = os.path.join(SCRIPT_DIR, 'book')
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'epub-output')
OEBPS_DIR = os.path.join(OUTPUT_DIR, 'OEBPS')
IMAGES_DIR = os.path.join(OEBPS_DIR, 'images')
EPUB_FILE = os.path.join(SCRIPT_DIR, 'botany-chapter1.epub')

# Create necessary directories
os.makedirs(OEBPS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, 'META-INF'), exist_ok=True)

# Statistics tracking
stats = {
    'chapters': 0,
    'images_found': 0,
    'images_downloaded': 0,
    'images_failed': 0,
    'errors': []
}

def natural_sort_key(filename):
    """
    Generate a key for natural sorting of filenames.
    Handles version numbers like 1.1, 1.1.1, 1.2, 1.10 correctly.
    """
    def convert(text):
        return int(text) if text.isdigit() else text.lower()

    # Extract just the filename without path
    basename = os.path.basename(filename)
    # Split on non-alphanumeric characters and convert numbers to integers
    return [convert(c) for c in re.split('([0-9]+)', basename)]

def extract_title_from_html(filepath):
    """
    Extract a title from an HTML file.
    Tries <title> tag first, then falls back to first <h1>, then filename.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')

        # Try <title> tag first
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            # Remove common suffixes like " - LibreTexts"
            title = re.sub(r'\s*[-|]\s*(LibreTexts|Botany).*$', '', title)
            if title:
                return title

        # Try first <h1> tag
        h1 = soup.find('h1')
        if h1 and h1.get_text():
            return h1.get_text().strip()

        # Fall back to filename (remove .html and replace hyphens/underscores)
        basename = os.path.basename(filepath)
        title = os.path.splitext(basename)[0]
        title = title.replace('-', ' ').replace('_', ' ')
        # Capitalize each word
        return ' '.join(word.capitalize() for word in title.split())

    except Exception as e:
        # If anything fails, use filename
        basename = os.path.basename(filepath)
        return os.path.splitext(basename)[0].replace('-', ' ').replace('_', ' ')

def discover_html_files(book_dir):
    """
    Auto-discover HTML files in the book directory.
    Returns a list of (filename, title) tuples sorted naturally.
    """
    # Find all HTML files
    html_files = glob.glob(os.path.join(book_dir, '*.html'))

    if not html_files:
        print(f"WARNING: No HTML files found in {book_dir}")
        return []

    # Sort naturally (1.1, 1.1.1, 1.2, not 1.1, 1.10, 1.2)
    html_files.sort(key=natural_sort_key)

    # Extract titles
    chapter_files = []
    for filepath in html_files:
        basename = os.path.basename(filepath)
        title = extract_title_from_html(filepath)
        chapter_files.append((basename, title))
        print(f"  Found: {basename} -> {title}")

    return chapter_files

def clean_html_content(soup):
    """Remove scripts, footers, and other non-content elements."""
    # Remove script tags
    for script in soup.find_all('script'):
        script.decompose()

    # Remove footer tags
    for footer in soup.find_all('footer'):
        footer.decompose()

    # Remove style tags
    for style in soup.find_all('style'):
        style.decompose()

    # Remove MathJax containers (they contain complex SVG and attributes)
    for mathjax in soup.find_all(['mjx-container', 'mjx-assistive-mml', 'mjx-speech']):
        mathjax.decompose()

    # Remove navigation elements
    for nav in soup.find_all(['nav', 'div'], class_=['mt-guide-listings', 'mt-topic-hierarchy-listings', 'autoattribution', 'mt-content-footer']):
        nav.decompose()

    return soup

def extract_image_urls(soup):
    """Extract all image URLs from the HTML."""
    images = []
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if src:
            # Handle relative URLs
            if not src.startswith('http'):
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    src = 'https://bio.libretexts.org' + src

            images.append({
                'url': src,
                'alt': img.get('alt', ''),
                'tag': img
            })

    return images

def download_image(url, filename):
    """Download an image from URL to the images directory."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()

            filepath = os.path.join(IMAGES_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(content)

            return True
    except Exception as e:
        stats['errors'].append(f"Failed to download {url}: {str(e)}")
        return False

def get_safe_filename(url, alt_text=''):
    """Generate a safe filename for an image based on URL and alt text."""
    # Extract extension
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    ext = os.path.splitext(path)[1]
    if not ext or ext not in ['.jpg', '.jpeg', '.png', '.gif', '.svg']:
        ext = '.jpg'

    # Create filename from alt text or hash of URL
    if alt_text:
        base = re.sub(r'[^a-z0-9]+', '-', alt_text.lower())[:50]
    else:
        base = hashlib.md5(url.encode()).hexdigest()[:16]

    return f"{base}{ext}"

def process_html_file(filepath, title):
    """Process a single HTML file and create clean XHTML."""
    print(f"Processing: {title}")

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        soup = BeautifulSoup(content, 'html.parser')

        # Extract the main content
        main_content = soup.find('section', class_='mt-content-container')
        if not main_content:
            main_content = soup.find('section')
        if not main_content:
            main_content = soup

        # Clean the content
        main_content = clean_html_content(main_content)

        # Extract and process images
        images = extract_image_urls(main_content)
        stats['images_found'] += len(images)

        image_mapping = {}
        for img_data in images:
            url = img_data['url']
            alt = img_data['alt']

            # Generate safe filename
            safe_filename = get_safe_filename(url, alt)

            # Download image
            if download_image(url, safe_filename):
                stats['images_downloaded'] += 1
                image_mapping[url] = safe_filename
                # Update img tag to point to local file
                img_data['tag']['src'] = f'images/{safe_filename}'
            else:
                stats['images_failed'] += 1

        # Convert to clean XHTML
        xhtml = create_xhtml_chapter(main_content, title)

        return xhtml, image_mapping

    except Exception as e:
        error_msg = f"Error processing {filepath}: {str(e)}"
        stats['errors'].append(error_msg)
        print(f"ERROR: {error_msg}")
        return None, {}

def create_xhtml_chapter(content, title):
    """Create a proper XHTML document from content."""
    xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{title}</title>
    <link rel="stylesheet" type="text/css" href="styles.css"/>
</head>
<body>
    <section>
        <h1>{title}</h1>
        {str(content)}
    </section>
</body>
</html>'''

    return xhtml

def create_single_page_xhtml(chapters_content, book_title):
    """Create a single-page XHTML document with all chapters combined."""
    # Combine all chapter content sections
    combined_sections = []
    for title, content in chapters_content:
        section_html = f'''    <section id="{title.lower().replace(' ', '-').replace('.', '-')}">
        <h1>{title}</h1>
        {str(content)}
    </section>'''
        combined_sections.append(section_html)

    xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{book_title}</title>
    <link rel="stylesheet" type="text/css" href="styles.css"/>
</head>
<body>
{chr(10).join(combined_sections)}
</body>
</html>'''

    return xhtml

def create_css():
    """Create basic CSS for the EPUB."""
    css = '''
body {
    font-family: Georgia, serif;
    line-height: 1.6;
    margin: 1em;
    color: #333;
}

h1 {
    color: #2c5f2d;
    font-size: 1.8em;
    margin-top: 0;
}

h2 {
    color: #2c5f2d;
    font-size: 1.5em;
    margin-top: 1.5em;
}

h3 {
    color: #2c5f2d;
    font-size: 1.3em;
}

p {
    margin: 1em 0;
    text-align: justify;
}

img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1em auto;
}

figure {
    margin: 1.5em 0;
    text-align: center;
}

figcaption {
    font-style: italic;
    font-size: 0.9em;
    margin-top: 0.5em;
    color: #666;
}

strong, b {
    font-weight: bold;
}

em, i {
    font-style: italic;
}

ul, ol {
    margin: 1em 0;
    padding-left: 2em;
}

li {
    margin: 0.5em 0;
}

.box-objectives {
    background-color: #f0f7f0;
    border-left: 4px solid #2c5f2d;
    padding: 1em;
    margin: 1.5em 0;
}

.box-note {
    background-color: #f9f9f9;
    border: 1px solid #ddd;
    padding: 1em;
    margin: 1.5em 0;
}
'''

    with open(os.path.join(OEBPS_DIR, 'styles.css'), 'w', encoding='utf-8') as f:
        f.write(css)

def create_content_opf(chapters, images, single_page=False):
    """Create the content.opf manifest file."""
    book_id = str(uuid.uuid4())
    date = datetime.now().strftime('%Y-%m-%d')

    # Build manifest items
    manifest_items = []
    manifest_items.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    manifest_items.append('    <item id="css" href="styles.css" media-type="text/css"/>')

    # Add chapters
    if single_page:
        # Single page mode: just one content file
        manifest_items.append('    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>')
    else:
        # Multi-page mode: one file per chapter
        for i, (_, _title) in enumerate(chapters):
            xhtml_id = f"chapter{i+1}"
            xhtml_file = f"{xhtml_id}.xhtml"
            manifest_items.append(f'    <item id="{xhtml_id}" href="{xhtml_file}" media-type="application/xhtml+xml"/>')

    # Add images
    for i, img_file in enumerate(images):
        ext = os.path.splitext(img_file)[1].lower()
        media_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml'
        }.get(ext, 'image/jpeg')

        manifest_items.append(f'    <item id="img{i+1}" href="images/{img_file}" media-type="{media_type}"/>')

    # Build spine items
    spine_items = []
    if single_page:
        spine_items.append('    <itemref idref="content"/>')
    else:
        for i in range(len(chapters)):
            spine_items.append(f'    <itemref idref="chapter{i+1}"/>')

    opf_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="book-id">urn:uuid:{book_id}</dc:identifier>
        <dc:title>Botany Chapter 1: Introduction to Botany</dc:title>
        <dc:creator>Melissa Ha</dc:creator>
        <dc:creator>Maria Morrow</dc:creator>
        <dc:creator>Kammy Algiers</dc:creator>
        <dc:language>en</dc:language>
        <dc:date>{date}</dc:date>
        <dc:publisher>ASCCC Open Educational Resources Initiative</dc:publisher>
        <dc:rights>CC BY-NC 4.0</dc:rights>
        <meta property="dcterms:modified">{datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
    </metadata>
    <manifest>
{chr(10).join(manifest_items)}
    </manifest>
    <spine toc="ncx">
{chr(10).join(spine_items)}
    </spine>
</package>'''

    with open(os.path.join(OEBPS_DIR, 'content.opf'), 'w', encoding='utf-8') as f:
        f.write(opf_content)

def create_toc_ncx(chapters, single_page=False):
    """Create the toc.ncx navigation file."""
    book_id = str(uuid.uuid4())

    nav_points = []
    if single_page:
        # Single page mode: use anchor links to sections within the same page
        for i, (_, title) in enumerate(chapters):
            section_id = title.lower().replace(' ', '-').replace('.', '-')
            nav_points.append(f'''    <navPoint id="navPoint{i+1}" playOrder="{i+1}">
      <navLabel>
        <text>{title}</text>
      </navLabel>
      <content src="content.xhtml#{section_id}"/>
    </navPoint>''')
    else:
        # Multi-page mode: link to separate chapter files
        for i, (_, title) in enumerate(chapters):
            nav_points.append(f'''    <navPoint id="navPoint{i+1}" playOrder="{i+1}">
      <navLabel>
        <text>{title}</text>
      </navLabel>
      <content src="chapter{i+1}.xhtml"/>
    </navPoint>''')

    ncx_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>Botany Chapter 1: Introduction to Botany</text>
  </docTitle>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>'''

    with open(os.path.join(OEBPS_DIR, 'toc.ncx'), 'w', encoding='utf-8') as f:
        f.write(ncx_content)

def create_container_xml():
    """Create the META-INF/container.xml file."""
    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''

    with open(os.path.join(OUTPUT_DIR, 'META-INF', 'container.xml'), 'w', encoding='utf-8') as f:
        f.write(container)

def create_mimetype():
    """Create the mimetype file."""
    with open(os.path.join(OUTPUT_DIR, 'mimetype'), 'w', encoding='utf-8') as f:
        f.write('application/epub+zip')

def package_epub():
    """Package all files into an EPUB archive."""
    print("\nPackaging EPUB...")

    # Remove existing EPUB if it exists
    if os.path.exists(EPUB_FILE):
        os.remove(EPUB_FILE)

    with zipfile.ZipFile(EPUB_FILE, 'w', zipfile.ZIP_DEFLATED) as epub:
        # Add mimetype first (uncompressed)
        epub.write(os.path.join(OUTPUT_DIR, 'mimetype'), 'mimetype', compress_type=zipfile.ZIP_STORED)

        # Add META-INF
        epub.write(os.path.join(OUTPUT_DIR, 'META-INF', 'container.xml'), 'META-INF/container.xml')

        # Add OEBPS files
        for root, _dirs, files in os.walk(OEBPS_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, OUTPUT_DIR)
                epub.write(file_path, arcname)

    print(f"EPUB created: {EPUB_FILE}")

def main(single_page=False, chapter_files=None):
    """Main processing function."""
    mode_text = "single-page" if single_page else "multi-page"
    print(f"Creating Botany Chapter 1 EPUB ({mode_text} mode)\n" + "="*50)

    # Auto-discover HTML files if not provided
    if chapter_files is None:
        print("\nAuto-discovering HTML files...")
        chapter_files = discover_html_files(BOOK_DIR)
        if not chapter_files:
            print("ERROR: No HTML files found to process!")
            sys.exit(1)
        print(f"\nFound {len(chapter_files)} file(s) to process.\n")

    # Create basic EPUB structure
    create_mimetype()
    create_container_xml()
    create_css()

    all_images = set()
    chapters_info = []
    chapters_content = []  # Store content for single-page mode

    # Process each chapter
    for i, (html_file, title) in enumerate(chapter_files):
        filepath = os.path.join(BOOK_DIR, html_file)

        if not os.path.exists(filepath):
            print(f"WARNING: File not found: {filepath}")
            continue

        # Read and extract content
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            soup = BeautifulSoup(content, 'html.parser')

            # Extract the main content
            main_content = soup.find('section', class_='mt-content-container')
            if not main_content:
                main_content = soup.find('section')
            if not main_content:
                main_content = soup

            # Clean the content
            main_content = clean_html_content(main_content)

            # Extract and process images
            images = extract_image_urls(main_content)
            stats['images_found'] += len(images)

            image_mapping = {}
            for img_data in images:
                url = img_data['url']
                alt = img_data['alt']

                # Generate safe filename
                safe_filename = get_safe_filename(url, alt)

                # Download image
                if download_image(url, safe_filename):
                    stats['images_downloaded'] += 1
                    image_mapping[url] = safe_filename
                    # Update img tag to point to local file
                    img_data['tag']['src'] = f'images/{safe_filename}'
                else:
                    stats['images_failed'] += 1

            if single_page:
                # Store content for combining later
                chapters_content.append((title, main_content))
            else:
                # Create individual chapter file
                xhtml = create_xhtml_chapter(main_content, title)
                xhtml_filename = f"chapter{i+1}.xhtml"
                with open(os.path.join(OEBPS_DIR, xhtml_filename), 'w', encoding='utf-8') as f:
                    f.write(xhtml)

            chapters_info.append((html_file, title))
            all_images.update(image_mapping.values())
            stats['chapters'] += 1

        except Exception as e:
            error_msg = f"Error processing {filepath}: {str(e)}"
            stats['errors'].append(error_msg)
            print(f"ERROR: {error_msg}")

    # If single-page mode, create the combined content file
    if single_page and chapters_content:
        print("\nCombining all chapters into single page...")
        single_page_xhtml = create_single_page_xhtml(chapters_content, "Botany Chapter 1: Introduction to Botany")
        with open(os.path.join(OEBPS_DIR, 'content.xhtml'), 'w', encoding='utf-8') as f:
            f.write(single_page_xhtml)

    # Get list of actually downloaded images
    downloaded_images = [f for f in os.listdir(IMAGES_DIR) if os.path.isfile(os.path.join(IMAGES_DIR, f))]

    # Create OPF and NCX files
    create_content_opf(chapters_info, downloaded_images, single_page=single_page)
    create_toc_ncx(chapters_info, single_page=single_page)

    # Package into EPUB
    package_epub()

    # Get final file size
    epub_size = os.path.getsize(EPUB_FILE) if os.path.exists(EPUB_FILE) else 0
    epub_size_mb = epub_size / (1024 * 1024)

    # Print summary
    print("\n" + "="*50)
    print("EPUB Creation Summary")
    print("="*50)
    print(f"Mode: {mode_text}")
    print(f"Chapters processed: {stats['chapters']}")
    print(f"Images found: {stats['images_found']}")
    print(f"Images downloaded: {stats['images_downloaded']}")
    print(f"Images failed: {stats['images_failed']}")
    print(f"Final EPUB size: {epub_size_mb:.2f} MB")

    if stats['errors']:
        print(f"\nErrors encountered: {len(stats['errors'])}")
        for error in stats['errors'][:10]:  # Show first 10 errors
            print(f"  - {error}")
        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more errors")

    print("\nEPUB file created successfully!")
    print(f"Location: {EPUB_FILE}")

if __name__ == '__main__':
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Create EPUB file from HTML files in the book/ directory',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                              # Auto-discover all HTML files, multi-page mode
  %(prog)s --single-page                 # Auto-discover all HTML files, single-page mode
  %(prog)s --files 1.1*.html             # Process files matching pattern
  %(prog)s --files file1.html file2.html # Process specific files
        '''
    )
    parser.add_argument(
        '--single-page',
        action='store_true',
        help='Generate EPUB with all content in a single page (default: multi-page)'
    )
    parser.add_argument(
        '--files',
        nargs='+',
        metavar='FILE',
        help='Specific HTML files to process (supports wildcards). If not specified, auto-discovers all HTML files.'
    )

    args = parser.parse_args()

    # Handle file specification
    chapter_files = None
    if args.files:
        print("Using specified files...")
        chapter_files = []
        for file_pattern in args.files:
            # Support both absolute paths and basenames
            if os.path.isabs(file_pattern):
                matches = glob.glob(file_pattern)
            else:
                matches = glob.glob(os.path.join(BOOK_DIR, file_pattern))

            if not matches:
                print(f"WARNING: No files found matching: {file_pattern}")
                continue

            # Sort matches naturally
            matches.sort(key=natural_sort_key)

            for filepath in matches:
                basename = os.path.basename(filepath)
                title = extract_title_from_html(filepath)
                chapter_files.append((basename, title))
                print(f"  Added: {basename} -> {title}")

        if not chapter_files:
            print("ERROR: No files found to process!")
            sys.exit(1)

    try:
        main(single_page=args.single_page, chapter_files=chapter_files)
    except Exception as e:
        print(f"\nFATAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
