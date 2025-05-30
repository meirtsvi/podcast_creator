import os
import glob
import datetime
import random
import time

import requests
import trafilatura

def get_markdown_from_url(url):
    try:
        max_retries = 3  # Retries per approach (without/with headers)

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
        }

        # First attempt without headers (simpler approach)
        print(f"Trying {url} without headers...")

        # Try without headers
        for retry in range(max_retries):
            try:
                response = requests.get(url, verify=False, allow_redirects=True, timeout=30)
                status_code = response.status_code
                if status_code == 200:
                    downloaded = response.text
                    md_text = trafilatura.extract(downloaded, output_format="markdown")
                    if md_text:
                        print(f"Successfully extracted markdown without headers (attempt {retry+1}).")
                        return md_text, response
                    else:
                        print(f"Got response but couldn't extract markdown without headers (attempt {retry+1}).")
                        break

                if retry < max_retries - 1:
                    wait_time = random.uniform(10, 15)
                    print(f"Retry {retry+1}/{max_retries} without headers, waiting {wait_time:.2f} seconds...")
                    time.sleep(wait_time)
            except Exception as e:
                print(f"Error without headers (attempt {retry+1}): {e}")

        # If we get here, the without-headers approach failed
        print("Without-headers approach failed. Trying with headers...")

        # Try with headers
        for retry in range(max_retries):
            try:
                response = requests.get(url, headers=headers, verify=False, allow_redirects=True, timeout=30)
                status_code = response.status_code
                if status_code == 200:
                    downloaded = response.text
                    md_text = trafilatura.extract(downloaded, output_format="markdown")

                    if md_text:
                        print(f"Successfully extracted markdown with headers (attempt {retry+1}).")
                        return md_text, response
                    else:
                        print(f"Got response but couldn't extract markdown with headers (attempt {retry+1}).")
                        break

                if retry < max_retries - 1:
                    wait_time = random.uniform(10, 15)
                    print(f"Retry {retry+1}/{max_retries} with headers, waiting {wait_time:.2f} seconds...")
                    time.sleep(wait_time)
            except Exception as e:
                print(f"Error with headers (attempt {retry+1}): {e}")

        # If we get here, both approaches failed
        print(f"Failed to extract markdown from {url} after trying without and with headers")
        return None, None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None, None

if __name__ == "__main__":
    md = get_markdown_from_url("https://www.calcalist.co.il/calcalistech/article/hyttkmvwxx")
    #md = create_markdown_from_url("https://www.fastcompany.com/91331507/leap-71-ai-printing-rocket-engine-faster-cheaper")
    #md, code = create_markdown_from_url("https://theorthagonist.substack.com/p/why-reading-business-books-is-a-waste")
    #md, code = create_markdown_from_url("https://www.cleverthinkingsoftware.com/programmers-will-be-replaced-by-people-with-ideas")
    print(md)

    # New functionality: process URLs from urls.txt files in specified directory
    base_dir = r"c:\Users\meir\Dropbox\tech_podcast_hebrew"

    # Create output file with timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"url_processing_results_{timestamp}.txt"

    with open(output_filename, 'w', encoding='utf-8') as results_file:
        # Record start time and write header
        results_file.write(f"URL Processing Results - {datetime.datetime.now()}\n")
        results_file.write("="*50 + "\n\n")

        # Find all urls.txt files in all subdirectories
        url_files = glob.glob(os.path.join(base_dir, "**", "urls.txt"), recursive=True)

        for url_file in url_files:
            # Only print the path of the current file on screen
            print(f"Processing URLs from: {url_file}")

            # Write detailed information to the results file
            results_file.write(f"Processing URLs from: {url_file}\n")
            results_file.write("-"*50 + "\n")

            try:
                with open(url_file, 'r', encoding='utf-8') as f:
                    urls = [line.strip() for line in f if line.strip()]

                for url in urls:
                    md_content, response = get_markdown_from_url(url)
                    status_code = response.status_code if response else 500
                    if md_content:
                        # Get the first line from the markdown content
                        first_line = md_content.split('\n')[0] if md_content else "No content extracted"

                        # Write results to file
                        results_file.write(f"URL: {url}\n")
                        results_file.write(f"Status Code: {status_code}\n")
                        results_file.write(f"First line: {first_line}\n")
                        results_file.write("-" * 50 + "\n")
                    else:
                        # Write failure information to file
                        results_file.write(f"Failed to process URL: {url}\n")
                        results_file.write("-" * 50 + "\n")

            except Exception as e:
                error_msg = f"Error processing file {url_file}: {e}"
                results_file.write(error_msg + "\n")
                results_file.write("-" * 50 + "\n")

        # Write summary at the end
        results_file.write(f"\nProcessing completed at {datetime.datetime.now()}\n")
        results_file.write(f"Results saved to {os.path.abspath(output_filename)}\n")

    print(f"All results written to {os.path.abspath(output_filename)}")
