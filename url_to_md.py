import os
import glob
import datetime
import random
import time
import requests
from lxml import html
from trafilatura import extract
from markdownify import markdownify
import html2text
from playwright.sync_api import sync_playwright
from types import SimpleNamespace


from logger import logger

from pathlib import Path

from youtube_content_extractor import extract_content_from_youtube


def get_deepest_folder(path):
    p = Path(path)
    if p.is_file() or p.suffix:  # has a file extension
        return p.parent.name
    return p.name

def html_to_markdown_fallback(raw_html, xpath_expr=None):
    """Convert selected HTML content (with headers and paragraphs) to Markdown using markdownify."""
    tree = html.fromstring(raw_html)

    # Extract title
    title = None
    h1 = tree.xpath('//h1')
    if h1 and h1[0].text_content().strip():
        title = h1[0].text_content().strip()
    elif tree.find(".//title") is not None:
        title = tree.find(".//title").text.strip()

    # Broad XPath to include headers and paragraphs
    if xpath_expr is None:
        xpath_expr = (
            '//article//*['
            'self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6 or self::p or self::table'
            '] | '
            '//div[contains(@class, "content")]//*['
            'self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6 or self::p or self::table'
            '] | '
            '//div[contains(@class, "body")]//*['
            'self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6 or self::p or self::table'
            '] | '
            '//div[@class="toptext"] | //div[contains(@class, "commtext")]'
        )

    elements = tree.xpath(xpath_expr)
    if not elements:
        return None

    html_fragment = "<div>" + "".join(
        html.tostring(el, encoding="unicode") for el in elements
    ) + "</div>"

    # Convert to Markdown with markdownify
    markdown_text = markdownify(html_fragment, heading_style="ATX")  # ATX means using `#` headers

    return f"# {title}\n\n{markdown_text.strip()}" if title else markdown_text.strip()


def playwright_extract_to_markdown(url: str):
    """
    Extracts markdown from a URL using Playwright and returns the markdown
    and a mock response object with the status code.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        response = None
        html_content = ""
        final_url = url
        try:
            response = page.goto(url, wait_until="networkidle")
            final_url = page.url
            if response and response.status == 404:
                logger.error(f"Playwright received status 404 for {url}")
                html_content = ""
            elif response:
                html_content = page.content()
        except Exception as e:
            logger.error(f"Playwright failed to navigate to {url}: {e}")
        finally:
            browser.close()

    markdown = html2text.html2text(html_content) if html_content else None
    status = response.status if response else None
    mock_response = SimpleNamespace(status_code=status, url=final_url, text=html_content)

    return markdown, mock_response

def _fetch_and_extract(url, session, headers=None):
    """
    Fetches content from a URL and extracts markdown using two methods,
    returning the longest result.
    """
    try:
        response = session.get(url, headers=headers, verify=False, allow_redirects=True, timeout=30)
        response.raise_for_status()
        html_string = response.text

        if '<html' not in html_string.lower():
            html_string = f'<html><body>{html_string}</body></html>'


        # Method 1: trafilatura.extract
        md_extract = extract(html_string, output_format="markdown", favor_recall=True)

        # Method 2: html_to_markdown_fallback (manual extraction)
        md_fallback = html_to_markdown_fallback(html_string)

        # Compare and return the longest markdown content
        len_extract = len(md_extract) if md_extract else 0
        len_fallback = len(md_fallback) if md_fallback else 0

        if len_extract > len_fallback:
            logger.info(f"extract() produced longer content for {url}")
            return md_extract, response
        elif len_fallback > 0:
            logger.info(f"html_to_markdown_fallback() produced longer content for {url}")
            return md_fallback, response
        else:
            return None, response

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch {url} with headers={headers is not None}. Error: {e}")
        return None, None


def get_markdown_from_url(url):

    if "youtube.com" in url or "youtu.be" in url:
        _, _, content = extract_content_from_youtube(url, lang='en')
        return content, SimpleNamespace(status_code=200, url=url)

    """
    Tries to get markdown from a URL by fetching with and without headers,
    and using two different extraction methods. Returns the best result.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.208 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
        "Cookie": "vmidv1=e6ccee59-bd0a-464e-b67b-067ac69e858b; blaize_session=b6a07e30-8a11-4f4b-8caa-33367a862a30; blaize_tracking_id=455ca7ab-51a3-40d3-92d4-9505eff29fc4; _awl=2.1749247632.5-c0f7ca89c84f26614349bc001dc571e2-6763652d6575726f70652d7765737431-1; AWSALB=XO1qwI5NI+NjLQkCtD8GfoKD+AXnNCjI0b5LOFBrc9E7t6Z9XZFmPaLdk4Cf/LAr2sig4XMLU4L1rhfBjhG9QM4TJySlR6o+j5TYrgpggkTO2M+r0aS7UsHUX98d; AWSALBCORS=XO1qwI5NI+NjLQkCtD8GfoKD+AXnNCjI0b5LOFBrc9E7t6Z9XZFmPaLdk4Cf/LAr2sig4XMLU4L1rhfBjhG9QM4TJySlR6o+j5TYrgpggkTO2M+r0aS7UsHUX98d; _vm_consent_type=opt-out; OptanonConsent=isGpcEnabled=0&datestamp=Thu+Jul+24+2025+15%3A33%3A47+GMT%2B0300+(Israel+Daylight+Time)&version=202504.1.0&browserGpcFlag=0&isIABGlobal=false&consentId=68abe6e0-243c-4f54-923e-c7edd8bdadd4&interactionCount=1&isAnonUser=1&landingPath=NotLandingPage&groups=C0001%3A1%2CBG136%3A1%2CC0002%3A1%2CC0003%3A1%2CC0004%3A1%2CC0005%3A1&hosts=H60%3A1%2CH369%3A1%2CH407%3A1%2CH236%3A1%2CH27%3A1%2CH42%3A1%2CH167%3A1%2CH486%3A1%2CH409%3A1%2CH410%3A1%2CH29%3A1%2CH62%3A1%2CH63%3A1%2CH4%3A1%2CH64%3A1%2CH231%3A1%2CH12%3A1%2CH251%3A1%2CH71%3A1%2CH74%3A1%2CH17%3A1%2CH488%3A1%2CH77%3A1%2CH275%3A1%2CH285%3A1%2CH82%3A1%2CH379%3A1%2CH381%3A1%2CH484%3A1%2CH89%3A1%2CH164%3A1%2CH90%3A1%2CH41%3A1%2CH46%3A1%2CH48%3A1%2CH244%3A1%2CH96%3A1%2CH290%3A1%2CH246%3A1%2CH489%3A1%2CH490%3A1%2CH304%3A1%2CH11%3A1%2CH487%3A1%2CH297%3A1&genVendors=&AwaitingReconsent=false; duet:identitySession=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzZXNzaW9uSUQiOiJjYzM1M2NhOS1mNGYwLTQ2NzktOGNhOS04MmQzNjA3YzI0OWEiLCJ1c2VySUQiOiJFMU44a3p0R0VnZ1hYa29yazZqS3kzeUlmcFYyIiwiZW50aXRsZW1lbnRzIjoidGhldmVyZ2Vfc3Vic2NyaXB0aW9uIiwiaWF0IjoxNzUzMzYwNDQ0LCJleHAiOjE3NjExMzY0NDR9.JGC0d_OyvmTY7MdFNdPL2hIEiHQlc0cfHqSofSJRZZEBdqwsVWw382DXtX6C9LiN_3DO56UuaJVreJpWbj5BIebEOZryyIeM_NEnDn7HSUx4BMbTm8DuaqLy-iHU8pMmPZ5RQGKDWfVI88VLCrLx5V4iyQsZiYr7OZYg3TabZsW0nWgyezw1KiVLTzitdZRkRT-To6nrMUOQSQr0uoDhF8h1ADJZHu_Q94yR9zCm3QZFEEnC_zwdS3o9-I0SJRsfBG4H1jGPC4q7Hlbh8hKxdAPdddRNSly-n4AkgNFsUpWv2Kt0hQXxg6_sJKgQ66d65ihgxkewNQyGKlADqeM4Jb8Ws3QXW00KykxQK1S75D72yVPizL7jDIeGGSuUyS_LaBJ0R1I8OSYwqiS-9ucEf3fLiKBgW6pcpHesRaUX1dXTnGq5OBQjOzYQ-mvbm4fq-bVz8h02NARjXxcP5v2hsvF7kIETICjGJuvv9rr91R1VANiQiMZW1YKtcK3nAD8ze8sFe_HqsVN_seDeYoSmcsIeUejuF44ob_P4aG4ze9xdUTBZeUoANv6cby9nFnhp8gEWqRukCVQgEIj3deMgqrPoAjWvMjjCgnQ8KE_qi8SwCYFjVnOh-Ots1ZJCPysk1xS4K_jCdsSsJ9fU60X1iZrLXWeQH0zrRsF853P2Vl4; duet:identityAuthenticated=true"
    }

    if "themarker.com" in url:
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 6_0 like Mac OS X) AppleWebKit/536.26 (KHTML, like Gecko) Version/6.0 Mobile/10A5376e Safari/8536.25',
            'Accept-Language': 'he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.google.com/'
        }

    with requests.Session() as session:
        # Attempt 1: Without headers
        logger.info(f"Attempting to fetch and extract from {url} without headers.")
        md_no_headers, response_no_headers = _fetch_and_extract(url, session)

        # Attempt 2: With headers
        logger.info(f"Attempting to fetch and extract from {url} with headers.")
        md_with_headers, response_with_headers = _fetch_and_extract(url, session, headers=headers)

        # Compare the results from with/without headers
        len_no_headers = len(md_no_headers) if md_no_headers else 0
        len_with_headers = len(md_with_headers) if md_with_headers else 0

        if len_with_headers > len_no_headers:
            logger.info(f"Extraction with headers yielded the best result for {url}.")
            return md_with_headers, response_with_headers

        if len_no_headers > 0:
            logger.info(f"Extraction without headers yielded the best result for {url}.")
            return md_no_headers, response_no_headers

        logger.error(f"Two main extraction methods failed for {url}. Trying Playwright as a fallback.")
        md_playwright, response_playwright = playwright_extract_to_markdown(url)
        if md_playwright:
            logger.info(f"Playwright successfully extracted data from {url}.")
            return md_playwright, response_playwright

        return None, response_playwright

if __name__ == "__main__":
    md = get_markdown_from_url("https://dl.dropbox.com/scl/fi/rpe6ci69ciyqzoghtksx0/content_to_share.txt?rlkey=s57fvwzz18gs51k648coo5zfz&dl=1")
    #md = get_markdown_from_url("https://antemedian.substack.com/p/why-reading-business-books-is-a-waste")
    # md = get_markdown_from_url("https://text-incubation.com/AI+code+is+legacy+code+from+day+one")
    # md = get_markdown_from_url("https://sampatt.com/blog/2025-04-28-can-o3-beat-a-geoguessr-master?utm_source=hackernewsletter&utm_medium=email&utm_term=fav")
    # md = get_markdown_from_url("https://news.ycombinator.com/item?id=44095189")
    # md = get_markdown_from_url("https://substack.com/inbox/post/164096497")
    # md = get_markdown_from_url("https://sketch.dev/blog/agent-loop")
    # md = get_markdown_from_url("https://arstechnica.com/gadgets/2025/06/apples-craig-federighi-on-the-long-road-to-the-ipads-mac-like-multitasking/")
    # md = get_markdown_from_url("https://phys.org/news/2025-06-quantum-mechanics-random-demand.html")
    # md = get_markdown_from_url("https://www.calcalist.co.il/calcalistech/article/skjsrj8xee")
    # md = get_markdown_from_url("https://www.fastcompany.com/91331507/leap-71-ai-printing-rocket-engine-faster-cheaper")
    # md, code = get_markdown_from_url("https://theorthagonist.substack.com/p/why-reading-business-books-is-a-waste")
    # md, code = get_markdown_from_url("https://www.cleverthinkingsoftware.com/programmers-will-be-replaced-by-people-with-ideas")
    #logger.info(md)

    # New functionality: process URLs from urls.txt files in specified directory
    base_dir = r"c:\Users\meir\Dropbox\tech_podcast_hebrew"

    # Find all urls.txt files in all subdirectories
    url_files = glob.glob(os.path.join(base_dir, "**", "urls.txt"), recursive=True)
    for url_file in url_files:
        # Only print the path of the current file on screen
        logger.info(f"Processing URLs from: {url_file}")

        output_filename = f"url_processing_results_{get_deepest_folder(url_file)}.txt"
        with open(output_filename, 'w', encoding='utf-8') as results_file:
            results_file.write(f"Processing URLs from: {url_file}\n")
            results_file.write("-"*50 + "\n")

            try:
                with open(url_file, 'r', encoding='utf-8') as f:
                    urls = [line.strip() for line in f if line.strip()]

                for url in urls:
                    md_content, response = get_markdown_from_url(url)
                    status_code = response.status_code if response else 500
                    if md_content:
                        # Write results to file
                        results_file.write(f"URL: {url}\n")
                        results_file.write(f"Status Code: {status_code}\n")
                        results_file.write(f"Content:\n{md_content}\n")
                        results_file.write("-" * 50 + "\n")
                    else:
                        # Write failure information to file
                        results_file.write(f"Failed to process URL: {url}\n")
                        results_file.write("-" * 50 + "\n")

                # Write summary at the end
                results_file.write(f"\nProcessing completed at {datetime.datetime.now()}\n")
                results_file.write(f"Results saved to {os.path.abspath(output_filename)}\n")

            except Exception as e:
                error_msg = f"Error processing file {url_file}: {e}"
                results_file.write(error_msg + "\n")
                results_file.write("-" * 50 + "\n")
                logger.error(error_msg)


    logger.info(f"All results written to {os.path.abspath(output_filename)}")
