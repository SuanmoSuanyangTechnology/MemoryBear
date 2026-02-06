"""Rate limiter for web crawler."""

import time
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """Enforce delays between requests to be polite to servers."""
    
    def __init__(self, delay_seconds: float = 1.0):
        """
        Initialize rate limiter.
        
        Args:
            delay_seconds: Minimum delay between requests
        """
        self.delay_seconds = delay_seconds
        self.last_request_time = 0.0
        self.max_delay = 60.0  # Cap maximum delay at 60 seconds
    
    def wait(self):
        """
        Block until enough time has passed since last request.
        Respects the configured delay.
        """
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        
        if elapsed < self.delay_seconds:
            sleep_time = self.delay_seconds - elapsed
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def set_delay(self, delay_seconds: float):
        """
        Update the delay (useful for respecting Crawl-delay from robots.txt).
        
        Args:
            delay_seconds: New delay in seconds
        """
        self.delay_seconds = min(delay_seconds, self.max_delay)
        logger.info(f"Rate limiter delay updated to {self.delay_seconds} seconds")
    
    def backoff(self, multiplier: float = 2.0):
        """
        Increase delay exponentially for backoff scenarios (429, 503 responses).
        
        Args:
            multiplier: Factor to multiply current delay by
        """
        old_delay = self.delay_seconds
        self.delay_seconds = min(self.delay_seconds * multiplier, self.max_delay)
        logger.warning(f"Rate limiter backing off: {old_delay:.2f}s -> {self.delay_seconds:.2f}s")
