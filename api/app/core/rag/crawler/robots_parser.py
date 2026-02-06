"""Robots.txt parser for web crawler."""

from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse, urljoin
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class RobotsParser:
    """Parse and check robots.txt compliance for URLs."""
    
    def __init__(self, user_agent: str, timeout: int = 10):
        """
        Initialize robots.txt parser.
        
        Args:
            user_agent: User agent string to check permissions for
            timeout: Timeout for fetching robots.txt
        """
        self.user_agent = user_agent
        self.timeout = timeout
        self._parsers = {}  # Cache parsers by domain
    
    def _get_robots_url(self, url: str) -> str:
        """
        Get the robots.txt URL for a given URL.
        
        Args:
            url: URL to get robots.txt for
        
        Returns:
            str: robots.txt URL
        """
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        return robots_url
    
    def _get_parser(self, url: str) -> RobotFileParser:
        """
        Get or create a RobotFileParser for the domain.
        
        Args:
            url: URL to get parser for
        
        Returns:
            RobotFileParser: Parser for the domain
        """
        robots_url = self._get_robots_url(url)
        
        # Return cached parser if available
        if robots_url in self._parsers:
            return self._parsers[robots_url]
        
        # Create new parser
        parser = RobotFileParser()
        parser.set_url(robots_url)
        
        try:
            # Fetch and parse robots.txt
            parser.read()
            logger.info(f"Successfully fetched robots.txt from {robots_url}")
        except Exception as e:
            # If robots.txt cannot be fetched, assume all URLs are allowed
            logger.warning(f"Could not fetch robots.txt from {robots_url}: {e}. Assuming all URLs allowed.")
            # Create a permissive parser
            parser = RobotFileParser()
            parser.parse([])  # Empty robots.txt allows everything
        
        # Cache the parser
        self._parsers[robots_url] = parser
        return parser
    
    def can_fetch(self, url: str) -> bool:
        """
        Check if the given URL can be fetched according to robots.txt.
        
        Args:
            url: URL to check
        
        Returns:
            bool: True if allowed, False if disallowed
        """
        try:
            parser = self._get_parser(url)
            allowed = parser.can_fetch(self.user_agent, url)
            
            if not allowed:
                logger.info(f"URL disallowed by robots.txt: {url}")
            
            return allowed
        except Exception as e:
            logger.error(f"Error checking robots.txt for {url}: {e}")
            # On error, assume allowed
            return True
    
    def get_crawl_delay(self, url: str) -> Optional[float]:
        """
        Get the Crawl-delay directive from robots.txt if present.
        
        Args:
            url: URL to get crawl delay for
        
        Returns:
            Optional[float]: Delay in seconds, or None if not specified
        """
        try:
            parser = self._get_parser(url)
            delay = parser.crawl_delay(self.user_agent)
            
            if delay is not None:
                logger.info(f"Crawl-delay from robots.txt: {delay} seconds")
            
            return delay
        except Exception as e:
            logger.error(f"Error getting crawl delay for {url}: {e}")
            return None
