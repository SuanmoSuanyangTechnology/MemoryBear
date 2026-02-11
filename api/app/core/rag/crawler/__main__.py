"""Command-line interface for web crawler."""

import argparse
import logging
import sys
from app.core.rag.crawler.web_crawler import WebCrawler


def setup_logging(verbose: bool = False):
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def main(entry_url: str,
         max_pages: int = 200,
         delay_seconds: float = 1.0,
         timeout_seconds: int = 10,
         user_agent: str = "KnowledgeBaseCrawler/1.0"):
    """Main entry point for the crawler."""
    # Create crawler
    crawler = WebCrawler(
        entry_url=entry_url,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        timeout_seconds=timeout_seconds,
        user_agent=user_agent
    )

    # Crawl and collect documents
    documents = []
    try:
        for doc in crawler.crawl():
            print(f"\n{'=' * 80}")
            print(f"URL: {doc.url}")
            print(f"Title: {doc.title}")
            print(f"Content Length: {doc.content_length} characters")
            print(f"Word Count: {doc.metadata.get('word_count', 0)} words")
            print(f"{'=' * 80}\n")

            documents.append({
                'url': doc.url,
                'title': doc.title,
                'content': doc.content,
                'content_length': doc.content_length,
                'crawl_timestamp': doc.crawl_timestamp.isoformat(),
                'http_status': doc.http_status,
                'metadata': doc.metadata
            })

    except KeyboardInterrupt:
        print("\n\nCrawl interrupted by user.")

    except Exception as e:
        print(f"\n\nError during crawl: {e}")
        sys.exit(1)

    # Get summary
    summary = crawler.get_summary()
    print(f"\n{'=' * 80}")
    print("CRAWL SUMMARY")
    print(f"{'=' * 80}")
    print(f"Total Pages Processed: {summary.total_pages_processed}")
    print(f"Total Errors: {summary.total_errors}")
    print(f"Total Skipped: {summary.total_skipped}")
    print(f"Total URLs Discovered: {summary.total_urls_discovered}")
    print(f"Duration: {summary.duration_seconds:.2f} seconds")
    print(f"documents: {documents}")

    if summary.error_breakdown:
        print(f"\nError Breakdown:")
        for error_type, count in summary.error_breakdown.items():
            print(f"  {error_type}: {count}")


if __name__ == '__main__':
    entry_url = "https://www.xxx.com"
    max_pages = 20
    delay_seconds = 1.0
    timeout_seconds = 10
    user_agent = "KnowledgeBaseCrawler/1.0"

    main(entry_url, max_pages, delay_seconds, timeout_seconds, user_agent)
