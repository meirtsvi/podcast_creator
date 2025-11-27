import requests
import json
from typing import Dict, List, Any
from datetime import datetime


class RedditScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def scrape_post(self, url: str) -> Dict[str, Any]:
        """
        Scrape a Reddit post and all its comments

        Args:
            url: Reddit post URL

        Returns:
            Dictionary containing post data and comments
        """
        # Convert URL to JSON endpoint
        json_url = url.rstrip('/') + '.json'

        try:
            response = requests.get(json_url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            # Extract post data (first element in response)
            post_data = data[0]['data']['children'][0]['data']

            # Extract comments data (second element in response)
            comments_data = data[1]['data']['children']

            # Parse post information
            post = {
                'title': post_data.get('title'),
                'author': post_data.get('author'),
                'score': post_data.get('score'),
                'upvote_ratio': post_data.get('upvote_ratio'),
                'num_comments': post_data.get('num_comments'),
                'created_utc': post_data.get('created_utc'),
                'created_date': datetime.fromtimestamp(post_data.get('created_utc', 0)).strftime('%Y-%m-%d %H:%M:%S'),
                'selftext': post_data.get('selftext'),
                'url': post_data.get('url'),
                'permalink': f"https://www.reddit.com{post_data.get('permalink')}",
                'subreddit': post_data.get('subreddit'),
            }

            # Parse comments
            comments = self._parse_comments(comments_data)

            return {
                'post': post,
                'comments': comments,
                'total_comments_scraped': len(comments)
            }

        except requests.exceptions.RequestException as e:
            print(f"Error fetching data: {e}")
            return None
        except (KeyError, IndexError) as e:
            print(f"Error parsing data: {e}")
            return None

    def _parse_comments(self, comments_data: List[Dict], level: int = 0) -> List[Dict]:
        """
        Recursively parse comments and their replies

        Args:
            comments_data: List of comment objects from Reddit API
            level: Current nesting level (for indentation)

        Returns:
            List of parsed comments
        """
        comments = []

        for item in comments_data:
            # Skip "more" comments placeholders
            if item['kind'] == 'more':
                continue

            comment_data = item['data']

            comment = {
                'author': comment_data.get('author'),
                'body': comment_data.get('body'),
                'score': comment_data.get('score'),
                'created_utc': comment_data.get('created_utc'),
                'created_date': datetime.fromtimestamp(comment_data.get('created_utc', 0)).strftime(
                    '%Y-%m-%d %H:%M:%S'),
                'level': level,
                'id': comment_data.get('id'),
                'permalink': f"https://www.reddit.com{comment_data.get('permalink')}",
            }

            comments.append(comment)

            # Recursively parse replies
            if 'replies' in comment_data and comment_data['replies']:
                if isinstance(comment_data['replies'], dict):
                    replies_data = comment_data['replies']['data']['children']
                    reply_comments = self._parse_comments(replies_data, level + 1)
                    comments.extend(reply_comments)

        return comments

    def print_results(self, result: Dict[str, Any]):
        """
        Pretty print the scraped results

        Args:
            result: Dictionary containing post and comments data
        """
        if not result:
            print("No data to display")
            return

        post = result['post']
        comments = result['comments']

        print("\n" + "=" * 80)
        print("POST INFORMATION")
        print("=" * 80)
        print(f"Title: {post['title']}")
        print(f"Author: u/{post['author']}")
        print(f"Subreddit: r/{post['subreddit']}")
        print(f"Score: {post['score']} (upvote ratio: {post['upvote_ratio']})")
        print(f"Created: {post['created_date']}")
        print(f"Number of comments: {post['num_comments']}")
        print(f"URL: {post['permalink']}")
        print(f"\nPost Content:\n{post['selftext'][:500]}..." if len(
            post['selftext']) > 500 else f"\nPost Content:\n{post['selftext']}")

        print("\n" + "=" * 80)
        print(f"COMMENTS ({result['total_comments_scraped']} scraped)")
        print("=" * 80 + "\n")

        for i, comment in enumerate(comments, 1):
            indent = "  " * comment['level']
            print(f"{indent}[{i}] u/{comment['author']} (score: {comment['score']}) - {comment['created_date']}")

            # Print comment body with word wrap
            body = comment['body'].replace('\n', f'\n{indent}    ')
            max_length = 300
            if len(body) > max_length:
                body = body[:max_length] + "..."
            print(f"{indent}    {body}")
            print()

    def save_to_json(self, result: Dict[str, Any], filename: str):
        """
        Save scraped data to JSON file

        Args:
            result: Dictionary containing post and comments data
            filename: Output filename
        """
        if not result:
            print("No data to save")
            return

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"\nData saved to {filename}")

def extract_content_from_reddit(url):
    scraper = RedditScraper()
    return scraper.scrape_post(url)

def main():
    scraper = RedditScraper()
    test_url = "https://www.reddit.com/r/algotrading/comments/1kgqcs7/using_machine_learning_for_trading_in_2025/"
    #test_url = "https://www.reddit.com/r/Rag/comments/1p42qik/docling_vs_chunkletpy_which_document_processing/"

    print("Reddit Scraper - Starting...")
    print(f"Scraping: {test_url}\n")
    result = extract_content_from_reddit(test_url)
    res = json.dumps(result)
    print(res)
    if result:
        scraper.print_results(result)
        scraper.save_to_json(result, 'reddit_scraped_data.json')
        print(f"\n✓ Successfully scraped {result['total_comments_scraped']} comments!")
    else:
        print("Failed to scrape the post")


if __name__ == "__main__":
    main()
