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

dotenv.load_dotenv()

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

        # Method 1: trafilatura.extract
        md_extract = extract(html_string,
                             output_format="markdown",
                             favor_recall=True)

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
    cache_folder = os.getenv("CACHE_FOLDER")
    url_as_filename = url_to_filename(url)
    cache_path = os.path.join(cache_folder, url_as_filename)
    if cache_folder and os.path.exists(cache_path):
        logger.info(f"Loading cached content for {url} from {cache_path}")
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached_md = f.read()
        return cached_md, SimpleNamespace(status_code=200, url=url)
    md_content, response = get_markdown_from_url_inner(url)
    if md_content and cache_folder:
        os.makedirs(cache_folder, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
    return md_content, response

def get_markdown_from_url_inner(url):
    if "youtube.com" in url or "youtu.be" in url:
        _, _, content = extract_content_from_youtube(url, lang='en')
        return content, SimpleNamespace(status_code=200, url=url)
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
        'Cookie': 'vmidv1=5d96018b-fbaa-4f7a-abec-17575e728528; _vm_consent_type=opt-out; pbjs_sharedId=fb0236bc-b131-40e4-bd1b-cd61ae28bf4f; pbjs_sharedId_cst=zix7LPQsHA%3D%3D; _lr_retry_request=true; _lr_env_src_ats=false; _gcl_au=1.1.819203324.1753764721; pbjs_unifiedID=%7B%22TDID%22%3A%220b01272a-a279-4507-9423-a6fbdfc05f2b%22%2C%22TDID_LOOKUP%22%3A%22TRUE%22%2C%22TDID_CREATED_AT%22%3A%222025-06-29T04%3A52%3A01%22%7D; pbjs_unifiedID_cst=zix7LPQsHA%3D%3D; _parsely_session={%22sid%22:1%2C%22surl%22:%22https://www.theverge.com/news/712638/alphabet-google-earnings-q2-2025-ceo-sundar-pichai-ai%22%2C%22sref%22:%22%22%2C%22sts%22:1753764721584%2C%22slts%22:0}; blaize_session=4abee957-f3ad-49fc-a20b-2c58efd0af0d; blaize_tracking_id=4ca98a3e-f3f5-4e5e-a08e-88474b96eae1; _parsely_visitor={%22id%22:%22pid=a9e3d10b-ec71-484d-a1c9-dbb2c3e115d1%22%2C%22session_count%22:1%2C%22last_session_ts%22:1753764721584}; _ga=GA1.1.1327380282.1753764722; sailthru_visitor=88b9255e-77b6-41d6-987b-ddfc533c0800; permutive-id=593596f2-dd4e-43d7-a6bc-0da45358be90; _tt_enable_cookie=1; _ttp=01K1A8BXG7CFS8QC92XKKJD081_.tt.1; _fbp=fb.1.1753764722328.37428949349292645; __gads=ID=540cfabb4c464483:T=1753764722:RT=1753764722:S=ALNI_Ma8jY9tmDtrSCcgUyBeD7wqlc2iug; __eoi=ID=b4b1a8575c3ba339:T=1753764722:RT=1753764722:S=AA-AfjbH2c-XP5B2-4giiaqrYoEg; cnx_userId=3-92621218b5e54b5d88209decaf046c16; _cc_id=704c4f4c7900f8105911444f61f9d621; panoramaId_expiry=1754369524330; panoramaId=bab2d41d166db157744f9e7b5c5b16d539386d3a18f366a87bb928aac8e68e94; panoramaIdType=panoIndiv; _lr_sampling_rate=100; duet:identityAuthenticated=true; duet:identitySession=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzZXNzaW9uSUQiOiJhYmUxZTY1NS03ODEzLTQ4YTYtYTVmNy0zYzE0NDEwYWU5ZGUiLCJ1c2VySUQiOiJFMU44a3p0R0VnZ1hYa29yazZqS3kzeUlmcFYyIiwiZW50aXRsZW1lbnRzIjoidGhldmVyZ2Vfc3Vic2NyaXB0aW9uIiwiaWF0IjoxNzUzNzY0NzY4LCJleHAiOjE3NjE1NDA3Njh9.AD21358PrlTTMKAScuuPn2FBrwaU8Uhi69arAzj4_OPQIRQMpbYA3pESKbsnst5Zj7mRrEpPqOJzKyUStqHcYD0PXiIOp4FBH2e_rjJ-3RsmBiXDab59i0evTiwT9IgIFT3RgSdN9nHEve4TeBgwdVyyu6AYKhqzysqHZkZFz_zuz-Y3b9PmpWjR9YgXa7r4PgBxPXNqPAB3W7ESqLYLVAnOUMHtED_Nn0FTt6SCqaVAEvXK3FV1plLsMBCecOknQ0G08UoUXHV_s2SfF_FF1wmKOlhmc5SoUZ72LC46Jw0Xbw1lAhetSpKrTCHKSBhaMekWLCVc-1ls0ANK7Hzps0iLuItycuWBNpiMnNmdexihgXpZtOr4oNrpy4_tv_ab9_nqWojtz_1DZulKSGAsu89L8EUA1l8v9d44DsM76k5NcXGECkwWIJ-03WFDUYpoJcXnnJs3pqRxKuRyh6Ijjp8CdJ_5aFlnt16z19nO8dZs98cIItq6leVqdw7bDxcnHh5AN-ElUYCt-j-Krpt38K3RXrBpfCUYt6ntVEhssoESrjniaQuNhhi046TE8Y0oaNokhCE7rBIrBo6M07u4Pcy33u93JvLq4jQPgGg8o595_LNx2j550MtUvNI-grv_ggo_NWb3PCJlI2ko8kZsw08n-t2Auf1mWn_NkL57pYk; sailthru_pageviews=3; OptanonConsent=isGpcEnabled=0&datestamp=Tue+Jul+29+2025+07%3A52%3A48+GMT%2B0300+(Israel+Daylight+Time)&version=202504.1.0&browserGpcFlag=0&isIABGlobal=false&consentId=15b6c650-ee8b-4449-b7b1-7f099e264241&interactionCount=1&isAnonUser=1&landingPath=NotLandingPage&groups=C0001%3A1%2CBG136%3A1%2CC0002%3A1%2CC0003%3A1%2CC0004%3A1%2CC0005%3A1&hosts=H60%3A1%2CH369%3A1%2CH407%3A1%2CH236%3A1%2CH27%3A1%2CH42%3A1%2CH167%3A1%2CH486%3A1%2CH409%3A1%2CH410%3A1%2CH29%3A1%2CH62%3A1%2CH63%3A1%2CH4%3A1%2CH64%3A1%2CH231%3A1%2CH12%3A1%2CH251%3A1%2CH71%3A1%2CH74%3A1%2CH17%3A1%2CH488%3A1%2CH77%3A1%2CH275%3A1%2CH285%3A1%2CH82%3A1%2CH379%3A1%2CH381%3A1%2CH484%3A1%2CH89%3A1%2CH164%3A1%2CH90%3A1%2CH41%3A1%2CH46%3A1%2CH48%3A1%2CH244%3A1%2CH96%3A1%2CH290%3A1%2CH246%3A1%2CH489%3A1%2CH490%3A1%2CH304%3A1%2CH11%3A1%2CH487%3A1%2CH297%3A1&genVendors=&AwaitingReconsent=false; sailthru_content=0574b3fac79d43f63c4241c6dec7f3e8d75f5fb0badd7516e16e30197bdc0a61; ttcsid=1753764722191::LnxHA30JmrZRAXld8JCU.1.1753764769557; ttcsid_COPQD3JC77UADS7P6KBG=1753764722189::sxaax66AadqjqbEfssqQ.1.1753764769955; _awl=2.1753764771.5-65e5d1563da8ff1ce9e626d40d806889-6763652d6575726f70652d7765737431-0; AWSALB=+mrOul9DvCy6T20ceJk1UsbCX8uZ/UlCftMk9So0HQgc72mx3nEQcvSVPAEBC+DInrpO9acSQicePVV91yYt5BIt8DciSWEoIptOym4jgXus78RHliVnu+3CbzFK; AWSALBCORS=+mrOul9DvCy6T20ceJk1UsbCX8uZ/UlCftMk9So0HQgc72mx3nEQcvSVPAEBC+DInrpO9acSQicePVV91yYt5BIt8DciSWEoIptOym4jgXus78RHliVnu+3CbzFK; _ga_9GXHZT6RVE=GS2.1.s1753764721$o1$g1$t1753765283$j60$l0$h0$dPwf6YAwXLoHOO-ETuAl8t2lch5XEmM9g3A'
    }

    if "themarker.com" in url:
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9,he;q=0.8,ru;q=0.7',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Referer': 'https://login.themarker.com/',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-site',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'Cookie': 'ab-test-group=B; anonymousId=17623320712696; _htzwif=none; acl=acl; _k5a=75@{"u":[{"uid":"YzHlMYzX6jeuWXnt","c":"desktop","ts":1760781050},1760871050]}; OptanonConsent=isGpcEnabled=0&datestamp=Sat+Oct+18+2025+12%3A50%3A51+GMT%2B0300+(Israel+Daylight+Time)&version=202308.2.0&browserGpcFlag=0&isIABGlobal=false&hosts=&landingPath=NotLandingPage&groups=C0001%3A1%2CC0002%3A0%2CC0003%3A0%2CC0004%3A0&AwaitingReconsent=false; aat=T3FIYTRWOURkTnZGSmp3; productsStatus=BOTHSuscribedPaying_; sso_token=eyJ1c2VySWQiOiI3NjUwNTQwNjU2IiwidXNlck1haWwiOiJ0dXRnaW5uYUBnbWFpbC5jb20iLCJ0aWNrZXRJZCI6IjM3MzczNTM3MzIzMDM0MzczNzMxMzczNTMzMzYzNDM3MzkzNzMwMzAiLCJmaXJzdE5hbWUiOiLXlNeS16giLCJsYXN0TmFtZSI6Item15HXmSIsImVtYWlsVmFsaWRpdHkiOiJ2YWxpZCIsInAiOiJkZGYxYzQwODM2ZDJiZjNmYzQ0N2JjOWNiZTNiOGY2ZCIsInVzZXJUeXBlIjoicGF5aW5nIiwiZCI6IjIwMjUtMTAtMTggYjJlNjVlNmJlYzgxYTZhNzM3MzdjMDVhMmUyNjcxMjkifQ==; userProducts=%7B%22products%22%3A%5B%7B%22prodNum%22%3A274%2C%22trial%22%3Afalse%7D%5D%2C%22stopped%22%3A%5B%5D%2C%22tempSince%22%3A%22%22%2C%22temporary%22%3Afalse%7D; user_details=eyJ1c2VyTWFpbCI6InR1dGdpbm5hQGdtYWlsLmNvbSIsImZpcnN0TmFtZSI6IteU15LXqCIsImxhc3ROYW1lIjoi16bXkdeZIiwiZW1haWxWYWxpZGl0eSI6InZhbGlkIiwidXNlclR5cGUiOiJwYXlpbmciLCJwcm9kdWN0cyI6W3sicHJvZE51bSI6Mjc0LCJzdGF0dXMiOiJTVUJTQ1JJQkVEIiwiaXNUcmlhbCI6ZmFsc2UsImRlYnRBY3RpdmUiOmZhbHNlLCJzdGFydERhdGUiOjE1NjUzODQ0MDAsImNhcmRFeHBpcmF0aW9uIjpmYWxzZSwiY29ubmVjdGlvblR5cGUiOjcyMH1dLCJ1bml2ZXJzaXR5IjpmYWxzZSwiZXh0ZW5kZWRVc2VyVHlwZSI6IlBheWluZyIsInRlcm1zQ2hlY2siOnRydWV9'
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
    md = get_markdown_from_url("https://www.themarker.com/wallstreet/2025-10-15/ty-article/.premium/00000199-e779-d54a-abfb-f7f939420000")
    md = get_markdown_from_url("https://www.themarker.com/weekend/2025-10-17/ty-article-magazine/.highlight/00000199-edde-dde4-a7bd-fdfe20a00000")
    md = get_markdown_from_url("https://www.theverge.com/news/712638/alphabet-google-earnings-q2-2025-ceo-sundar-pichai-ai")
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
