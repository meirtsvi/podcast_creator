import json
import os
import re
import glob
import datetime

import requests
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlparse
import dotenv

from playwright.sync_api import sync_playwright
import html2text
from lxml import html
from trafilatura import extract
from markdownify import markdownify

from podcast_creator.logger import logger
from podcast_creator.youtube_content_extractor import extract_content_from_youtube
from podcast_creator.reddit_scraper import extract_content_from_reddit

dotenv.load_dotenv(Path(__file__).resolve().parent / ".env")

def url_to_filename(url: str, max_length: int = 200) -> str:
    # Parse URL and construct a base name
    parsed = urlparse(url)
    base = f"{parsed.netloc}_{parsed.path}_{parsed.query}"
    if not base.strip():
        base = url  # fallback

    # Replace invalid characters with "_"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', base)

    # Collapse repeated underscores
    base = re.sub(r'_+', '_', base).strip('_')

    # Limit filename length and add extension if desired
    base = base[:max_length]

    # Optional: ensure not empty
    if not base:
        base = "file"

    return base

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
            '//div[@class="toptext"] | //div[contains(@class, "commtext")]')

    elements = tree.xpath(xpath_expr)
    if not elements:
        return None

    html_fragment = "<div>" + "".join(
        html.tostring(el, encoding="unicode") for el in elements) + "</div>"

    # Convert to Markdown with markdownify
    markdown_text = markdownify(html_fragment, heading_style="ATX")  # ATX means using `#` headers

    return f"# {title}\n\n{markdown_text.strip()}" if title else markdown_text.strip()


_THEMARKER_COOKIE_CACHE = Path(__file__).resolve().parent / ".themarker_cookies.txt"


def _themarker_login() -> str:
    """Log in to themarker.com with THEMARKER_USERNAME/THEMARKER_PASSWORD and
    return a Cookie header string. The session is cached on disk so login only
    runs when the cached session has expired."""
    username = os.getenv("THEMARKER_USERNAME")
    password = os.getenv("THEMARKER_PASSWORD")
    if not username or not password:
        logger.error("THEMARKER_USERNAME/THEMARKER_PASSWORD are not set in .env")
        return ""
    logger.info(f"Logging in to themarker.com as {username}")
    cookies = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto("https://login.themarker.com/",
                      wait_until="networkidle", timeout=60000)
            page.fill('[data-testid="email-field"]', username)
            page.click('[data-testid="submit-btn"]')
            page.wait_for_selector('[data-testid="password-field"]', timeout=30000)
            page.fill('[data-testid="password-field"]', password)
            page.click('[data-testid="submit-btn"]')
            # The post-login redirect never reaches networkidle (ads), so poll
            # for the sso_token cookie instead.
            for _ in range(60):
                page.wait_for_timeout(500)
                cookies = context.cookies()
                if any(c["name"] == "sso_token" for c in cookies):
                    break
            else:
                logger.error(
                    "TheMarker login did not produce an sso_token cookie "
                    "(wrong credentials?)")
                return ""
        except Exception as e:
            logger.error(f"TheMarker login failed: {e}")
            return ""
        finally:
            browser.close()
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                       if "themarker.com" in c.get("domain", ""))
    _THEMARKER_COOKIE_CACHE.write_text(cookie, encoding="utf-8")
    return cookie


def _themarker_cookie(force_login: bool = False) -> str:
    if not force_login and _THEMARKER_COOKIE_CACHE.exists():
        cached = _THEMARKER_COOKIE_CACHE.read_text(encoding="utf-8").strip()
        if cached:
            return cached
    return _themarker_login()


def _themarker_rich_text_count(raw_html: str) -> int:
    return raw_html.count('data-testid="rich-text"')


def _is_complete_themarker_extract(raw_html: str, md: str | None, url: str) -> bool:
    """Premium articles ship only a teaser (2 rich-text blocks) without a valid cookie."""
    if not md:
        return False
    if ".premium" in url or ".highlight" in url:
        if _themarker_rich_text_count(raw_html) < 3:
            return False
    body_paragraphs = [
        p for p in md.split("\n\n")
        if p.strip() and not p.strip().startswith("#")
    ]
    return len(body_paragraphs) >= 3


def _extract_themarker_markdown(raw_html: str):
    tree = html.fromstring(raw_html)
    title_el = tree.xpath('//*[@data-testid="article-title"] | //h1')
    title = title_el[0].text_content().strip() if title_el else None
    subtitle_el = tree.xpath('//*[@data-testid="article-subtitle"]')
    subtitle = subtitle_el[0].text_content().strip() if subtitle_el else None
    paragraphs = [
        el.text_content().strip()
        for el in tree.xpath(
            '//*[@data-testid="article-body-wrapper"]//*[@data-testid="rich-text"]')
        if el.text_content().strip() and 'חדשות, ידיעות' not in el.text_content()
    ]
    if not paragraphs and not subtitle:
        return None
    parts = [f"# {title}"] if title else []
    if subtitle:
        parts.append(subtitle)
    parts.extend(paragraphs)
    return "\n\n".join(parts)


def _themarker_request_headers(cookie: str) -> dict:
    return {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9,he;q=0.8',
        'cache-control': 'no-cache',
        'dnt': '1',
        'pragma': 'no-cache',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'none',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        'Cookie': cookie,
    }


def _canonical_url(raw_html: str):
    match = re.search(r'<link rel="canonical" href="([^"]+)"', raw_html)
    return match.group(1) if match else None


def _get_markdown_from_themarker(url):
    """Fetch a TheMarker article as a logged-in subscriber.

    The full body is only server-rendered at the canonical URL (which carries
    the .premium/.highlight marker), so after fetching the given URL we follow
    the canonical link from its HTML. If the result is still a teaser the
    cached session has expired — log in again and retry once.
    """
    md, response = None, None
    with requests.Session() as session:
        for force_login in (False, True):
            cookie = _themarker_cookie(force_login=force_login)
            if not cookie:
                break
            headers = _themarker_request_headers(cookie)
            md, response = _fetch_and_extract(url, session, headers=headers)
            checked_url = url
            canonical = _canonical_url(response.text) if response is not None else None
            if canonical and canonical.split("?")[0] != url.split("?")[0]:
                md_canonical, response_canonical = _fetch_and_extract(
                    canonical, session, headers=headers)
                if response_canonical is not None:
                    md, response, checked_url = md_canonical, response_canonical, canonical
            if md and response is not None and _is_complete_themarker_extract(
                    response.text, md, checked_url):
                return md, response
            if not force_login:
                logger.warning(
                    f"TheMarker fetch incomplete for {url}, "
                    "re-logging in and retrying.")
    logger.error(f"TheMarker extraction failed for {url}.")
    return md, response


def playwright_extract_to_markdown(url: str):
    """
    Extracts markdown from a URL using Playwright and returns the markdown
    and a mock response object with the status code.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
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
            elif response and response.status == 400:
                logger.error(f"Playwright received status 400 for {url}")
                html_content = ""
            elif response:
                html_content = page.content()
        except Exception as e:
            logger.error(f"Playwright failed to navigate to {url}: {e}")
        finally:
            browser.close()

    markdown = html2text.html2text(html_content) if html_content else None
    status = response.status if response else None
    mock_response = SimpleNamespace(status_code=status,
                                    url=final_url,
                                    text=html_content)

    return markdown, mock_response

def _found_text(article: str):
    n_text_lines = 0
    if article.strip() == "You need to enable JavaScript to run this app.":
        return False
    for line in article.splitlines():
        if line.strip() == "":
            continue
        if not line.startswith("#"):
            n_text_lines += 1
    return n_text_lines > 2

def _extract_from_trafilatura(html: str) -> str:
    result = extract(html, output_format="markdown", favor_precision=True, with_metadata=True)
    if result:
        # Split YAML front matter from markdown body
        if result.startswith("---\n"):
            parts = result.split("---\n", 2)
            yaml_part = parts[1]  # between first and second ---
            md_body = parts[2] if len(parts) > 2 else ""

            # Check if body already starts with a markdown heading
            if not md_body.lstrip().startswith("# "):
                # Extract title from YAML
                for line in yaml_part.splitlines():
                    if line.startswith("title: "):
                        title = line[len("title: "):]
                        md_body = f"# {title}\n\n{md_body}"
                        break

            return md_body
    return result

def _fetch_and_extract(url, session, headers=None):
    """
    Fetches content from a URL and extracts markdown using two methods,
    returning the longest result.
    """
    try:
        response = session.get(url,
                               headers=headers,
                               verify=False,
                               allow_redirects=True,
                               timeout=30)
        response.raise_for_status()
        html_string = response.text

        if '<html' not in html_string.lower():
            html_string = f'<html><body>{html_string}</body></html>'

        if "themarker.com" in url:
            md_themarker = _extract_themarker_markdown(html_string)
            if md_themarker and _found_text(md_themarker) and _is_complete_themarker_extract(
                    html_string, md_themarker, url):
                return md_themarker, response
            if md_themarker and not _is_complete_themarker_extract(html_string, md_themarker, url):
                logger.warning(
                    f"TheMarker returned paywalled teaser for {url} "
                    f"({_themarker_rich_text_count(html_string)} rich-text blocks).")

        # Method 1: trafilatura.extract
        md_extract = _extract_from_trafilatura(html_string)
        if md_extract and not _found_text(md_extract):
            md_extract = ""

        # Method 2: html_to_markdown_fallback (manual extraction)
        md_fallback = html_to_markdown_fallback(html_string)
        if md_fallback and not _found_text(md_fallback):
            md_fallback = ""

        candidates = [md for md in (md_extract, md_fallback) if md and _found_text(md)]
        if candidates:
            return max(candidates, key=len), response
        return None, response

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch {url} with headers={headers is not None}. Error: {e}")
        return None, None

def get_markdown_from_url(url):
    cache_folder = os.getenv("CACHE_FOLDER")
    url_as_filename = url_to_filename(url)
    cache_path = os.path.join(cache_folder, url_as_filename).replace("\\", "/")
    if cache_folder and os.path.exists(cache_path):
        logger.info(f"Loading cached content for {url} from {cache_path}")
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached_md = f.read()
        return cached_md,  SimpleNamespace(status_code=200, url=url, text=cached_md)
    md_content, response = get_markdown_from_url_inner(url)
    if md_content and cache_folder and not (
            "themarker.com" in url
            and response
            and not _is_complete_themarker_extract(response.text, md_content, url)):
        os.makedirs(cache_folder, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
    return md_content, response


def get_markdown_from_url_inner(url):
    if "youtube.com" in url or "youtu.be" in url:
        _, _, content = extract_content_from_youtube(url, lang='en')
        return content, SimpleNamespace(status_code=200, url=url, text=content)

    if url.startswith("https://arxiv.org/") and not "/pdf/" in url:
        from common import extract_content_from_arxiv
        content = extract_content_from_arxiv(url)
        return content, SimpleNamespace(status_code=200, url=url, text=content)

    if "reddit.com" in url:
        content = extract_content_from_reddit(url)
        return json.dumps(content), SimpleNamespace(status_code=200, url=url, text=content)

    if "themarker.com" in url:
        return _get_markdown_from_themarker(url)

    """
    Tries to get markdown from a URL by fetching with and without headers,
    and using two different extraction methods. Returns the best result.
    """
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9,he;q=0.8',
        'cache-control': 'no-cache',
        'dnt': '1',
        'pragma': 'no-cache',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
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
    md, _ = get_markdown_from_url("https://www.themarker.com/markets/2026-05-25/ty-article/.premium/0000019e-5e7b-d9a9-abde-dfff4fbe0000?utm_source=App_Share&utm_medium=iOS_Native")
    md, _ = get_markdown_from_url("https://www.themarker.com/technation/2025-12-11/ty-article/.highlight/0000019b-0cd0-d868-affb-5cf9d2a10000?utm_source=App_Share&utm_medium=iOS_Native")
    prompt = f"""The following text is markdown formatted text of a web page. Do the following:
    1. Convert md to regular, plain text
    2. Remove all meta characters
    3. Remove all links, keep only links titles
    4. Remove ads text
    5. Remove all references to other articles
    Here is the article md text to work on:
    {md}
    """
    from src.podcast_creator.common import call_genai_api
    content = call_genai_api(prompt)
    from podcast_creator.common import translate_text
    content = translate_text(content, "hebrew")

    md = get_markdown_from_url("https://www.squid-club.com/blog/the-reality-of-ai-first-coding-that-nobodys-telling-you-about")
    md = get_markdown_from_url("https://www.reddit.com/r/algotrading/comments/1kgqcs7/using_machine_learning_for_trading_in_2025/")
    md = get_markdown_from_url("https://arxiv.org/abs/2503.09655")

    md = get_markdown_from_url("https://www.squid-club.com/blog/the-reality-of-ai-first-coding-that-nobodys-telling-you-about")
    md = get_markdown_from_url("https://www.themarker.com/technation/2025-10-30/ty-article/.premium/0000019a-3597-ddf1-a1db-fdffa8830000?utm_source=App_Share&utm_medium=iOS_Native")
    md = get_markdown_from_url("https://www.themarker.com/wallstreet/2025-10-15/ty-article/.premium/00000199-e779-d54a-abfb-f7f939420000")
    md = get_markdown_from_url("https://www.themarker.com/weekend/2025-10-17/ty-article-magazine/.highlight/00000199-edde-dde4-a7bd-fdfe20a00000")
    md = get_markdown_from_url("https://www.theverge.com/news/712638/alphabet-google-earnings-q2-2025-ceo-sundar-pichai-ai")
    md = get_markdown_from_url("https://www.themarker.com/wallstreet/2025-10-15/ty-article/.premium/00000199-e779-d54a-abfb-f7f939420000")
    md = get_markdown_from_url("https://www.themarker.com/weekend/2025-10-17/ty-article-magazine/.highlight/00000199-edde-dde4-a7bd-fdfe20a00000")
    md = get_markdown_from_url("https://www.theverge.com/news/712638/alphabet-google-earnings-q2-2025-ceo-sundar-pichai-ai")
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
            results_file.write("-" * 50 + "\n")

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
