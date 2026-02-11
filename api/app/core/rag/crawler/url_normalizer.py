"""URL normalization and validation for web crawler."""

from typing import Optional, List
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, urljoin
from bs4 import BeautifulSoup


class URLNormalizer:
    """Normalize and validate URLs for deduplication and domain checking."""
    
    # Common tracking parameters to remove
    TRACKING_PARAMS = {
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'fbclid', 'gclid', 'msclkid', '_ga', 'mc_cid', 'mc_eid'
    }
    
    def __init__(self, base_domain: str):
        """
        Initialize URL normalizer with base domain.
        
        Args:
            base_domain: The domain to use for same-domain checks
        """
        parsed = urlparse(base_domain)
        self.base_domain = parsed.netloc.lower() # example.com:8000
        self.base_scheme = parsed.scheme or 'https' # https
    
    def normalize(self, url: str) -> Optional[str]:
        """
        Normalize a URL for deduplication.
        
        Normalization rules:
        1. Convert domain to lowercase
        2. Remove fragments (#section)
        3. Remove default ports (80 for http, 443 for https)
        4. Remove trailing slashes (except for root)
        5. Sort query parameters alphabetically
        6. Remove common tracking parameters
        
        Args:
            url: URL to normalize
        
        Returns:
            Optional[str]: Normalized URL, or None if invalid
        """
        try:
            parsed = urlparse(url)
            
            # Validate scheme
            if parsed.scheme not in ('http', 'https'):
                return None
            
            # Normalize domain to lowercase
            netloc = parsed.netloc.lower()
            
            # Remove default ports
            if ':' in netloc:
                host, port = netloc.rsplit(':', 1)
                if (parsed.scheme == 'http' and port == '80') or \
                   (parsed.scheme == 'https' and port == '443'):
                    netloc = host
            
            # Normalize path
            path = parsed.path
            # Remove trailing slash except for root
            if path != '/' and path.endswith('/'):
                path = path.rstrip('/')
            # Ensure path starts with /
            if not path:
                path = '/'
            
            # Process query parameters
            query = ''
            if parsed.query:
                # Parse query parameters
                params = parse_qs(parsed.query, keep_blank_values=True)
                # Remove tracking parameters
                filtered_params = {
                    k: v for k, v in params.items() 
                    if k not in self.TRACKING_PARAMS
                }
                # Sort parameters alphabetically
                if filtered_params:
                    sorted_params = sorted(filtered_params.items())
                    query = urlencode(sorted_params, doseq=True)
            
            # Reconstruct URL without fragment
            normalized = urlunparse((
                parsed.scheme,
                netloc,
                path,
                parsed.params,
                query,
                ''  # Remove fragment
            ))
            
            return normalized
            
        except Exception:
            return None
    
    def is_same_domain(self, url: str) -> bool:
        """
        Check if URL belongs to the same domain as base_domain.
        
        Args:
            url: URL to check
        
        Returns:
            bool: True if same domain, False otherwise
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove port if present
            if ':' in domain:
                domain = domain.split(':')[0]
            
            # Check if domains match
            return domain == self.base_domain or domain == self.base_domain.split(':')[0]
            
        except Exception:
            return False
    
    def extract_links(self, html: str, base_url: str) -> List[str]:
        """
        Extract and normalize all links from HTML.
        
        Args:
            html: HTML content
            base_url: Base URL for resolving relative links
        
        Returns:
            List[str]: List of normalized absolute URLs
        """
        links = []
        
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Find all anchor tags
            for anchor in soup.find_all('a', href=True):
                href = anchor['href']
                
                # Skip empty hrefs
                if not href or href.strip() == '':
                    continue
                
                # Skip javascript: and mailto: links
                if href.startswith(('javascript:', 'mailto:', 'tel:')):
                    continue

                normalized_url = None
                # Check if href starts with http/https (absolute URL)
                if href.startswith(('http://', 'https://')):
                    if self.is_same_domain(href):
                        normalized_url = self.normalize(href)
                else:
                    # Convert relative URL to absolute
                    absolute_url = urljoin(base_url, href)
                    # Normalize the URL
                    normalized_url = self.normalize(absolute_url)
                
                if normalized_url:
                    links.append(normalized_url)

        except Exception:
            pass
        
        return links
