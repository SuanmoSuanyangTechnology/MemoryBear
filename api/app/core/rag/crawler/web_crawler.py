"""Main web crawler orchestrator."""

from collections import deque
from datetime import datetime
from typing import Iterator, Optional, List, Set
from urllib.parse import urlparse
import logging

from app.core.rag.crawler.url_normalizer import URLNormalizer
from app.core.rag.crawler.robots_parser import RobotsParser
from app.core.rag.crawler.rate_limiter import RateLimiter
from app.core.rag.crawler.http_fetcher import HTTPFetcher
from app.core.rag.crawler.content_extractor import ContentExtractor
from app.core.rag.crawler.models import CrawledDocument, CrawlSummary

logger = logging.getLogger(__name__)


class WebCrawler:
    """Main orchestrator for web crawling."""
    
    def __init__(
        self,
        entry_url: str,
        max_pages: int = 200,
        delay_seconds: float = 1.0,
        timeout_seconds: int = 10,
        user_agent: str = "KnowledgeBaseCrawler/1.0",
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        content_extractor: Optional[ContentExtractor] = None
    ):
        """
        Initialize the web crawler.
        
        Args:
            entry_url: Starting URL for the crawl
            max_pages: Maximum number of pages to crawl (default: 200)
            delay_seconds: Delay between requests in seconds (default: 1.0)
            timeout_seconds: HTTP request timeout (default: 10)
            user_agent: User-Agent header string
            include_patterns: List of regex patterns for URLs to include
            exclude_patterns: List of regex patterns for URLs to exclude
            content_extractor: Custom content extractor (optional)
        """
        # Validate entry URL
        parsed = urlparse(entry_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid entry URL: {entry_url}")
        
        self.entry_url = entry_url
        self.max_pages = max_pages
        self.user_agent = user_agent
        
        # Extract domain from entry URL
        self.domain = parsed.netloc
        
        # Initialize components
        self.url_normalizer = URLNormalizer(entry_url)
        self.robots_parser = RobotsParser(user_agent, timeout_seconds)
        self.rate_limiter = RateLimiter(delay_seconds)
        self.http_fetcher = HTTPFetcher(timeout_seconds, max_retries=3, user_agent=user_agent)
        self.content_extractor = content_extractor or ContentExtractor()
        
        # State management
        self.url_queue: deque = deque()
        self.visited_urls: Set[str] = set()
        self.pages_processed = 0
        
        # Statistics
        self.stats = {
            'success': 0,
            'errors': 0,
            'skipped': 0,
            'urls_discovered': 0,
            'error_breakdown': {}
        }
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
    
    def crawl(self) -> Iterator[CrawledDocument]:
        """
        Execute the crawl and yield documents as they are processed.
        
        Yields:
            CrawledDocument: Structured document with extracted content
        """
        logger.info(f"Starting crawl from {self.entry_url} (max_pages: {self.max_pages})")
        self.start_time = datetime.now()
        
        # Add entry URL to queue
        normalized_entry = self.url_normalizer.normalize(self.entry_url)
        if normalized_entry:
            self.url_queue.append(normalized_entry)
            self.stats['urls_discovered'] += 1
        
        # Check robots.txt and update rate limiter if needed
        crawl_delay = self.robots_parser.get_crawl_delay(self.entry_url)
        if crawl_delay:
            self.rate_limiter.set_delay(crawl_delay)
        
        # Main crawl loop
        while self.url_queue and self.pages_processed < self.max_pages:
            url = self.url_queue.popleft()
            
            # Skip if already visited
            if url in self.visited_urls:
                continue
            
            # Mark as visited
            self.visited_urls.add(url)
            
            # Check robots.txt permission
            if not self.robots_parser.can_fetch(url):
                logger.info(f"Skipping {url} (disallowed by robots.txt)")
                self.stats['skipped'] += 1
                continue
            
            # Apply rate limiting
            self.rate_limiter.wait()
            
            # Fetch URL
            logger.info(f"Fetching {url} ({self.pages_processed + 1}/{self.max_pages})")
            fetch_result = self.http_fetcher.fetch(url)
            
            # Handle fetch errors
            if not fetch_result.success:
                self._record_error(fetch_result.error or "Unknown error")
                continue
            
            # Check Content-Type
            content_type = fetch_result.headers.get('Content-Type', '').lower()
            if not any(substring in content_type for substring in ['text/html', 'application/xhtml+xml']):
                logger.warning(f"Skipping {url} (Content-Type: {content_type})")
                self.stats['skipped'] += 1
                continue

            # Extract content
            try:
                extracted = self.content_extractor.extract(fetch_result.content, url)
                
                # Check if static content
                if not extracted.is_static:
                    logger.warning(f"Skipping {url} (JavaScript-rendered content)")
                    self.stats['skipped'] += 1
                    continue
                
                # Create document
                document = CrawledDocument(
                    url=url,
                    title=extracted.title,
                    content=extracted.text,
                    content_length=len(extracted.text),
                    crawl_timestamp=datetime.now(),
                    http_status=fetch_result.status_code,
                    metadata={
                        'word_count': extracted.word_count,
                        'final_url': fetch_result.final_url
                    }
                )
                
                # Update statistics
                self.pages_processed += 1
                self.stats['success'] += 1
                
                # Extract and queue links
                links = self.url_normalizer.extract_links(fetch_result.content, url)
                for link in links:
                    if link not in self.visited_urls and self.url_normalizer.is_same_domain(link):
                        if link not in self.url_queue:
                            self.url_queue.append(link)
                            self.stats['urls_discovered'] += 1
                
                # Yield document
                yield document
                
            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                self._record_error(f"Processing error: {str(e)}")
                continue
        
        self.end_time = datetime.now()
        logger.info(f"Crawl completed. Processed {self.pages_processed} pages.")
    
    def get_summary(self) -> CrawlSummary:
        """
        Get summary statistics after crawl completion.
        
        Returns:
            CrawlSummary: Statistics including success/error/skip counts
        """
        if not self.start_time:
            self.start_time = datetime.now()
        if not self.end_time:
            self.end_time = datetime.now()
        
        duration = (self.end_time - self.start_time).total_seconds()
        
        return CrawlSummary(
            total_pages_processed=self.stats['success'],
            total_errors=self.stats['errors'],
            total_skipped=self.stats['skipped'],
            total_urls_discovered=self.stats['urls_discovered'],
            start_time=self.start_time,
            end_time=self.end_time,
            duration_seconds=duration,
            error_breakdown=self.stats['error_breakdown']
        )
    
    def _record_error(self, error: str):
        """Record an error in statistics."""
        self.stats['errors'] += 1
        error_type = error.split(':')[0] if ':' in error else error
        self.stats['error_breakdown'][error_type] = \
            self.stats['error_breakdown'].get(error_type, 0) + 1
