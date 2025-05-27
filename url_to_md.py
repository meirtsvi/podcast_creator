import requests
import trafilatura

def create_markdown_from_url(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, verify=False)
        downloaded = response.text
        md_text = trafilatura.extract(downloaded, output_format="markdown")
        print(f"Markdown file created successfully.")
        return md_text
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    md = create_markdown_from_url("https://garymarcus.substack.com/p/the-ai-2027-scenario-how-realistic")
    print(md)
