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
    found_text = False
    if article.strip() == "You need to enable JavaScript to run this app.":
        return found_text
    for line in article.splitlines():
        if line.strip() == "":
            continue
        if not line.startswith("#"):
            found_text = True
            break
    return found_text

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

        # Method 1: trafilatura.extract
        md_extract = _extract_from_trafilatura(html_string)
        if md_extract and not _found_text(md_extract):
            md_extract = ""

        # Method 2: html_to_markdown_fallback (manual extraction)
        md_fallback = html_to_markdown_fallback(html_string)
        if md_fallback and not _found_text(md_fallback):
            md_fallback = ""

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
    cache_path = os.path.join(cache_folder, url_as_filename).replace("\\", "/")
    if cache_folder and os.path.exists(cache_path):
        logger.info(f"Loading cached content for {url} from {cache_path}")
        with open(cache_path, 'r', encoding='utf-8') as f:
            cached_md = f.read()
        return cached_md,  SimpleNamespace(status_code=200, url=url, text=cached_md)
    md_content, response = get_markdown_from_url_inner(url)
    if md_content and cache_folder:
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
            'Cookie': 'anonymousId=17831757416225; ab-test-group=B; _fbp=fb.1.1775399730957.552462482310956727; aat=WFVkZjB5djBNQVlOQzht; productsStatus=BOTHSuscribedPaying_; sso_token=eyJ1c2VySWQiOiI3NjUwNTQwNjU2IiwidXNlck1haWwiOiJ0dXRnaW5uYUBnbWFpbC5jb20iLCJ0aWNrZXRJZCI6IjM3MzczNTM3MzIzMDM0MzczNzMxMzczNTMzMzYzNDM3MzkzNzMwMzAiLCJmaXJzdE5hbWUiOiLXlNeS16giLCJsYXN0TmFtZSI6Item15HXmSIsImVtYWlsVmFsaWRpdHkiOiJ2YWxpZCIsInAiOiJkZGYxYzQwODM2ZDJiZjNmYzQ0N2JjOWNiZTNiOGY2ZCIsInVzZXJUeXBlIjoicGF5aW5nIiwiZCI6IjIwMjYtMDQtMDUgYWZhOGU1NDRjZDNmMjM1Yjk5YTY3Yjg4OWJlMzRjOWIifQ==; userProducts=%7B%22products%22%3A%5B%7B%22prodNum%22%3A274%2C%22trial%22%3Afalse%7D%5D%2C%22stopped%22%3A%5B%5D%2C%22tempSince%22%3A%22%22%2C%22temporary%22%3Afalse%7D; user_details=eyJ1c2VyTWFpbCI6InR1dGdpbm5hQGdtYWlsLmNvbSIsImZpcnN0TmFtZSI6IteU15LXqCIsImxhc3ROYW1lIjoi16bXkdeZIiwiZW1haWxWYWxpZGl0eSI6InZhbGlkIiwidXNlclR5cGUiOiJwYXlpbmciLCJwcm9kdWN0cyI6W3sicHJvZE51bSI6Mjc0LCJzdGF0dXMiOiJTVUJTQ1JJQkVEIiwiaXNUcmlhbCI6ZmFsc2UsImRlYnRBY3RpdmUiOmZhbHNlLCJzdGFydERhdGUiOjE1NjUzODQ0MDAsImNhcmRFeHBpcmF0aW9uIjpmYWxzZSwiY29ubmVjdGlvblR5cGUiOjcyMH1dLCJ1bml2ZXJzaXR5IjpmYWxzZSwiZXh0ZW5kZWRVc2VyVHlwZSI6IlBheWluZyIsInRlcm1zQ2hlY2siOnRydWV9; _htzwif=none; acl=acl; _ga=GA1.1.999789562.1775399766; _gcl_au=1.1.913945240.1775399766; _twpid=tw.1775399766476.15276858734047806; vad-loc-code=il; cebs=1; _sharedID=dc7f5129-75d7-458d-b2f1-46b6b19faaac; _sharedID_cst=znv0HA%3D%3D; OptanonConsent=isGpcEnabled=0&datestamp=Sun+Apr+05+2026+17%3A36%3A07+GMT%2B0300+(Israel+Daylight+Time)&version=202308.2.0&browserGpcFlag=0&isIABGlobal=false&hosts=&landingPath=https%3A%2F%2Fwww.themarker.com%2F%3FfromLogin%3DsuccessChangePassword&groups=C0001%3A1%2CC0002%3A0%2CC0003%3A0%2CC0004%3A0; dmp-FE-cookie-dmpid=9b7203b6-6959-4e86-8b3e-27e62e781df8; __gads=ID=e38805cfb21411b7:T=1775399779:RT=1775399779:S=ALNI_MYNtmQCJ6_sVpiBxvmASFs24XbfCw; __gpi=UID=000013bb41326c68:T=1775399779:RT=1775399779:S=ALNI_MZr84Vjq2YnWHuEoZ0eGoQMWdZoZw; __eoi=ID=df1f0089e7c7b54d:T=1775399779:RT=1775399779:S=AA-Afjal_MsklKQwFI3X8c2JlvS9; _ce.clock_data=-11879%2C93.172.169.223%2C1%2C91e1a2a41c0741f7f47615ab9de2fb8a%2CChrome%2CIL; _ce.s=v~d805005791ae47ccd872e704086038f8b09f5bb9~lcw~1775399767448~vir~new~lva~1775399766669~vpv~0~v11.cs~22588~v11.s~c83a45b0-30fc-11f1-a2ba-d73a69259c6b~v11.vs~d805005791ae47ccd872e704086038f8b09f5bb9~v11.fsvd~eyJub3RNb2RpZmllZFVybCI6Imh0dHBzOi8vd3d3LnRoZW1hcmtlci5jb20vP2Zyb21Mb2dpbj1zdWNjZXNzQ2hhbmdlUGFzc3dvcmQiLCJ1cmwiOiJ0aGVtYXJrZXIuY29tIiwicmVmIjoiaHR0cHM6Ly9sb2dpbi50aGVtYXJrZXIuY29tLyIsInV0bSI6W119~v11.sla~1775399767441~v11.wss~1775399767444~v11.ss~1775399767447~v11ls~c83a45b0-30fc-11f1-a2ba-d73a69259c6b~lcw~1775399767450; cto_bidid=885iuV9uJTJGJTJCcWRkYzJ3SmNoOUlYNlR4V1lmQTBiU1ZlaFRTc0hEVnZmMWk3U09aJTJGbXR3OEludGl0NFRIVmFxekdvMTVqaWwzMU5yWE9JdDdRZkNTNEFxRUNEVHVIeHZnMWFoQktyT29kbUJTVFNMcyUzRA; _k5a=75@{"u":[{"uid":"mPaVXiPDKSPFvJkt","c":"desktop","ts":1775399768},1775489768]}; _ga_8CR4051LQE=GS2.1.s1775399766$o1$g1$t1775399778$j48$l0$h0; ra=1; cebsp_=2; dmp-FE-cookie-ts=1775371044187; cto_bundle=rB5NM180aUU5RkZCSFRHbFlPcmZGZm9HaXl0TUJQUlN0NkhERjl3NlN4OVBtVjA3WU5HeVVyaENQMzlvUTRzMXIxTEVLajlFOFJrN003dVIlMkZ5WWw1UkpIWTdYJTJCNVlCYmM3bk1CVXZkeTJqUmFYVCUyQnY4ODJmZ2JQMVZ5MTNVWk1ocjZuUUllSndkaG4lMkJtZEZjUXBzVUQxUXpVdyUzRCUzRA'
            # 'Cookie': 'anonymousId=17685570035687; aat=T3FIYTRWOURkTnZGSmp3; productsStatus=BOTHSuscribedPaying_; userProducts=%7B%22products%22%3A%5B%7B%22prodNum%22%3A274%2C%22trial%22%3Afalse%7D%5D%2C%22stopped%22%3A%5B%5D%2C%22tempSince%22%3A%22%22%2C%22temporary%22%3Afalse%7D; _htzwif=none; _ga=GA1.1.722377055.1760781021; _fbp=fb.1.1760781021031.562396184486743961; ra=1; ab-test-group=B; acl=acl; _gcl_au=1.1.426889494.1772443740; _twpid=tw.1772443740497.760146166291941114; cebs=1; vad-loc-code=il; dmp-FE-cookie-dmpid=9b7203b6-6959-4e86-8b3e-27e62e781df8; _ce.clock_data=-738%2C85.64.148.88%2C1%2C7c73ef5b8d3235ae0606f2e84e457ff5%2CChrome%2CIL; _sharedID=7e82ffd6-edf6-450e-ae7d-bdd958f0a576; _sharedID_cst=znv0HA%3D%3D; dmp-FE-cookie-ts=1772444861542; _ce.s=v~99eaf59224c39ce9331ce338c8b6a63745b9c9f7~lcw~1772446634964~vir~new~lva~1772443740837~vpv~3~v11ls~fc1b05f0-1620-11f1-9c16-0fc296b9c546~v11.cs~22588~v11.s~fc1b05f0-1620-11f1-9c16-0fc296b9c546~v11.vs~99eaf59224c39ce9331ce338c8b6a63745b9c9f7~v11.fsvd~eyJ1cmwiOiJ0aGVtYXJrZXIuY29tL3RlY2huYXRpb24vKi90eS1hcnRpY2xlLy5oaWdobGlnaHQvKiIsInJlZiI6IiIsInV0bSI6WyJBcHBfU2hhcmUiLCJpT1NfTmF0aXZlIiwiIiwiIiwiIl19~v11.sla~1772446634961~v11.wss~1772446634961~v11.ss~1772446634963~lcw~1772447034726; __gads=ID=2b3f042d99767cee:T=1760781023:RT=1772447036:S=ALNI_MYBICg_QW_ZWPlv8zV3ccmWoBgE2A; __eoi=ID=97d93ac0b0e584f1:T=1760781023:RT=1772447036:S=AA-Afjb5erV-3YNLtWUmpUwulUyl; cebsp_=7; OptanonConsent=isGpcEnabled=0&datestamp=Mon+Mar+02+2026+12%3A25%3A06+GMT%2B0200+(Israel+Standard+Time)&version=202308.2.0&browserGpcFlag=0&isIABGlobal=false&hosts=&landingPath=NotLandingPage&groups=C0001%3A1%2CC0002%3A0%2CC0003%3A0%2CC0004%3A0&AwaitingReconsent=false; _k5a=75@{"u":[{"uid":"bbz0Aiw4AqrqhNtQ","c":"desktop","ts":1772447106},1772537106]}; cto_bidid=ktOyyV9uJTJGJTJCcWRkYzJ3SmNoOUlYNlR4V1lmQTBiU1ZlaFRTc0hEVnZmMWk3U09aJTJGbXR3OEludGl0NFRIVmFxekdvMTVqeGhrZmFxZEhYSCUyQk1qZUlTUDczYXpMJTJGTmduQzg5VTJEazFUYjRGek13ekklM0Q; cto_bundle=wOd-GV80aUU5RkZCSFRHbFlPcmZGZm9HaXlpYWElMkJ0TmlpbDFLTVhiYTU4U0VETkRRS09ubXI5ZDdKcVFCRlE3Y2RKclQ2amxlTmFqckNzakZ4T0VjUDhZckw5c0dpWVpOZXczdVhWR2UyblU1M0xyam9VemRNS0RSbnVUZTFuUkZ0b0tUT0xrNnZMUm9IQko4TkRMRmhFTFpUUSUzRCUzRA; _ga_8CR4051LQE=GS2.1.s1772446633$o7$g1$t1772447257$j60$l0$h0; sso_token=eyJ1c2VySWQiOiI3NjUwNTQwNjU2IiwidXNlck1haWwiOiJ0dXRnaW5uYUBnbWFpbC5jb20iLCJ0aWNrZXRJZCI6IjM3MzczNTM3MzIzMDM0MzczNzMxMzczNTMzMzYzNDM3MzkzNzMwMzAiLCJmaXJzdE5hbWUiOiLXlNeS16giLCJsYXN0TmFtZSI6Item15HXmSIsImVtYWlsVmFsaWRpdHkiOiJ2YWxpZCIsInAiOiJkZGYxYzQwODM2ZDJiZjNmYzQ0N2JjOWNiZTNiOGY2ZCIsInVzZXJUeXBlIjoicGF5aW5nIiwiZCI6IjIwMjYtMDMtMDIgOTAzNTU4YTA1NzQzZjdiZTE5YjM4OThiMGM5OGRlZDYifQ==; user_details=eyJ1c2VyTWFpbCI6InR1dGdpbm5hQGdtYWlsLmNvbSIsImZpcnN0TmFtZSI6IteU15LXqCIsImxhc3ROYW1lIjoi16bXkdeZIiwiZW1haWxWYWxpZGl0eSI6InZhbGlkIiwidXNlclR5cGUiOiJwYXlpbmciLCJwcm9kdWN0cyI6W3sicHJvZE51bSI6Mjc0LCJzdGF0dXMiOiJTVUJTQ1JJQkVEIiwiaXNUcmlhbCI6ZmFsc2UsImRlYnRBY3RpdmUiOmZhbHNlLCJzdGFydERhdGUiOjE1NjUzODQ0MDAsImNhcmRFeHBpcmF0aW9uIjpmYWxzZSwiY29ubmVjdGlvblR5cGUiOjcyMH1dLCJ1bml2ZXJzaXR5IjpmYWxzZSwiZXh0ZW5kZWRVc2VyVHlwZSI6IlBheWluZyIsInRlcm1zQ2hlY2siOnRydWV9'
        }


    with requests.Session() as session:
        # Attempt 1: Without headers
        logger.info(f"Attempting to fetch and extract from {url} without headers.")
        md_no_headers, response_no_headers = _fetch_and_extract(url, session)

        # Attempt 2: With headers
        logger.info(f"Attempting to fetch and extract from {url} with headers.")
        md_with_headers, response_with_headers = _fetch_and_extract(url, session, headers=headers)

        # Check if themarker cookies expired
        if "themarker.com" in url and md_with_headers and "טוען..." in md_with_headers:
            return ""

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
    md, _ = get_markdown_from_url("https://www.themarker.com/wallstreet/2026-04-04/ty-article/.premium/0000019d-54b0-d759-ab9d-7db90e5c0000?utm_source=App_Share&utm_medium=iOS_Native")

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
