"""HTTP fetcher for web crawler."""

import requests
import time
import logging
import re
from typing import Optional, Dict


from app.core.rag.crawler.models import FetchResult

logger = logging.getLogger(__name__)


class HTTPFetcher:
    """Handle HTTP requests with retries, error handling, and response validation."""
    
    def __init__(
        self,
        timeout: int = 10,
        max_retries: int = 3,
        user_agent: str = "KnowledgeBaseCrawler/1.0"
    ):
        """
        Initialize HTTP fetcher.
        
        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            user_agent: User-Agent header value
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        
        # Create session for connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': user_agent
        })
    
    def fetch(self, url: str) -> FetchResult:
        """
        Fetch a URL with retry logic and error handling.
        
        Args:
            url: URL to fetch
        
        Returns:
            FetchResult: Contains status_code, content, headers, error info
        """
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                # Calculate backoff delay for retries
                if attempt > 0:
                    backoff_delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
                    logger.info(f"Retry attempt {attempt + 1}/{self.max_retries} for {url} after {backoff_delay}s")
                    time.sleep(backoff_delay)
                
                # Make HTTP request
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True
                )
                
                # Handle different status codes
                if response.status_code == 429:
                    # Too Many Requests - backoff and retry
                    logger.warning(f"429 Too Many Requests for {url}, backing off")
                    if attempt < self.max_retries - 1:
                        continue
                
                if response.status_code == 503:
                    # Service Unavailable - pause and retry
                    logger.warning(f"503 Service Unavailable for {url}")
                    if attempt < self.max_retries - 1:
                        time.sleep(5)  # Longer pause for 503
                        continue
                
                # Success or client error (don't retry 4xx except 429)
                if 200 <= response.status_code < 300:
                    logger.info(f"Successfully fetched {url} (status: {response.status_code})")

                    # Get correctly encoded content
                    content = self._get_decoded_content(response)

                    return FetchResult(
                        url=url,
                        final_url=response.url,
                        status_code=response.status_code,
                        content=content,
                        headers=dict(response.headers),
                        error=None,
                        success=True
                    )
                elif response.status_code == 404:
                    logger.info(f"404 Not Found: {url}")
                    return FetchResult(
                        url=url,
                        final_url=response.url,
                        status_code=response.status_code,
                        content=None,
                        headers=dict(response.headers),
                        error="Not Found",
                        success=False
                    )
                elif 400 <= response.status_code < 500:
                    logger.warning(f"Client error {response.status_code} for {url}")
                    return FetchResult(
                        url=url,
                        final_url=response.url,
                        status_code=response.status_code,
                        content=None,
                        headers=dict(response.headers),
                        error=f"Client error: {response.status_code}",
                        success=False
                    )
                elif 500 <= response.status_code < 600:
                    logger.error(f"Server error {response.status_code} for {url}")
                    last_error = f"Server error: {response.status_code}"
                    if attempt < self.max_retries - 1:
                        continue
                    return FetchResult(
                        url=url,
                        final_url=url,
                        status_code=response.status_code,
                        content=None,
                        headers={},
                        error=last_error,
                        success=False
                    )
                
            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                logger.warning(f"Timeout fetching {url} (attempt {attempt + 1}/{self.max_retries})")
                if attempt >= self.max_retries - 1:
                    break
                continue
            
            except requests.exceptions.SSLError as e:
                last_error = f"SSL/TLS error: {str(e)}"
                logger.error(f"SSL/TLS error for {url}: {e}")
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=0,
                    content=None,
                    headers={},
                    error=last_error,
                    success=False
                )
            
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {str(e)}"
                logger.warning(f"Connection error for {url} (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt >= self.max_retries - 1:
                    break
                continue
            
            except requests.exceptions.RequestException as e:
                last_error = f"Request error: {str(e)}"
                logger.error(f"Request error for {url}: {e}")
                if attempt >= self.max_retries - 1:
                    break
                continue
        
        # All retries exhausted
        logger.error(f"Failed to fetch {url} after {self.max_retries} attempts: {last_error}")
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content=None,
            headers={},
            error=last_error or "Unknown error",
            success=False
        )

    def _get_decoded_content(self, response) -> str:
        """
        Get correctly decoded content from response.

        Handles encoding detection and fallback strategies:
        1. Try encoding from HTML meta tags
        2. Try response.encoding (from Content-Type header or detected)
        3. Try UTF-8
        4. Try common encodings (GB2312, GBK for Chinese, etc.)
        5. Fall back to latin-1 with error replacement

        Args:
            response: requests.Response object

        Returns:
            str: Decoded content
        """
        # Try to detect encoding from HTML meta tags
        meta_encoding = self._detect_encoding_from_meta(response.content)
        if meta_encoding:
            try:
                content = response.content.decode(meta_encoding)
                logger.info(f"Successfully decoded with meta tag encoding: {meta_encoding}")
                return content
            except (UnicodeDecodeError, LookupError) as e:
                logger.warning(f"Failed to decode with meta encoding {meta_encoding}: {e}")

        # Try response.encoding (from Content-Type header or detected by requests)
        if response.encoding and response.encoding.lower() != 'iso-8859-1':
            # Note: requests defaults to ISO-8859-1 if no charset in Content-Type,
            # so we skip it here and try UTF-8 first
            try:
                return response.text
            except (UnicodeDecodeError, LookupError) as e:
                logger.warning(f"Failed to decode with detected encoding {response.encoding}: {e}")

        # Try UTF-8 first (most common)
        try:
            return response.content.decode('utf-8')
        except UnicodeDecodeError:
            logger.debug("UTF-8 decoding failed, trying other encodings")

        # Try common encodings for different languages
        encodings_to_try = [
            'gbk',  # Chinese (Simplified)
            'gb2312',  # Chinese (Simplified, older)
            'gb18030',  # Chinese (Simplified, extended)
            'big5',  # Chinese (Traditional)
            'shift_jis',  # Japanese
            'euc-jp',  # Japanese
            'euc-kr',  # Korean
            'iso-8859-1',  # Western European
            'windows-1252',  # Windows Western European
            'windows-1251',  # Cyrillic
        ]

        for encoding in encodings_to_try:
            try:
                content = response.content.decode(encoding)
                logger.info(f"Successfully decoded with {encoding}")
                return content
            except (UnicodeDecodeError, LookupError):
                continue

        # Last resort: use latin-1 with error replacement
        logger.warning("All encoding attempts failed, using latin-1 with error replacement")
        return response.content.decode('latin-1', errors='replace')

    def _detect_encoding_from_meta(self, content: bytes) -> Optional[str]:
        """
        Detect encoding from HTML meta tags.

        Looks for:
        - <meta charset="...">
        - <meta http-equiv="Content-Type" content="...; charset=...">

        Args:
            content: Raw response content (bytes)

        Returns:
            Optional[str]: Detected encoding or None
        """
        try:
            # Only check first 2KB for performance
            head = content[:2048]

            # Try to decode as ASCII/Latin-1 to search for meta tags
            try:
                head_str = head.decode('ascii', errors='ignore')
            except:
                head_str = head.decode('latin-1', errors='ignore')

            # Look for <meta charset="...">
            charset_match = re.search(
                r'<meta[^>]+charset=["\']?([a-zA-Z0-9_-]+)',
                head_str,
                re.IGNORECASE
            )
            if charset_match:
                encoding = charset_match.group(1).lower()
                logger.debug(f"Found charset in meta tag: {encoding}")
                return encoding

            # Look for <meta http-equiv="Content-Type" content="...; charset=...">
            content_type_match = re.search(
                r'<meta[^>]+http-equiv=["\']?content-type["\']?[^>]+content=["\']([^"\']+)',
                head_str,
                re.IGNORECASE
            )
            if content_type_match:
                content_value = content_type_match.group(1)
                charset_match = re.search(r'charset=([a-zA-Z0-9_-]+)', content_value, re.IGNORECASE)
                if charset_match:
                    encoding = charset_match.group(1).lower()
                    logger.debug(f"Found charset in Content-Type meta: {encoding}")
                    return encoding

        except Exception as e:
            logger.debug(f"Error detecting encoding from meta tags: {e}")

        return None
