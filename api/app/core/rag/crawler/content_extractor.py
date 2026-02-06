"""Content extractor for web crawler."""

from bs4 import BeautifulSoup
import re
import logging

from app.core.rag.crawler.models import ExtractedContent

logger = logging.getLogger(__name__)


class ContentExtractor:
    """Extract clean, readable text from HTML pages."""
    
    # Tags to remove completely
    REMOVE_TAGS = ['script', 'style', 'nav', 'header', 'footer', 'aside']
    
    # Tags that typically contain main content
    MAIN_CONTENT_TAGS = ['article', 'main']
    
    # Content extraction tags
    CONTENT_TAGS = ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'section']
    
    def is_static_content(self, html: str) -> bool:
        """
        Determine if the HTML represents static content.
        
        Detects JavaScript-rendered content by checking for minimal body
        with heavy script tag presence.
        
        Args:
            html: Raw HTML string
        
        Returns:
            bool: True if static, False if JavaScript-rendered
        """
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Count script tags
            script_tags = soup.find_all('script')
            script_count = len(script_tags)
            
            # Get body content (excluding scripts and styles)
            body = soup.find('body')
            if not body:
                return False
            
            # Remove scripts and styles temporarily for text check
            for tag in body.find_all(['script', 'style']):
                tag.decompose()
            
            # Get text content
            text = body.get_text(strip=True)
            text_length = len(text)
            
            # If there's very little text but many scripts, likely JS-rendered
            if script_count > 5 and text_length < 200:
                logger.warning("Detected JavaScript-rendered content (many scripts, little text)")
                return False
            
            # If there's no meaningful text, likely JS-rendered
            if text_length < 50:
                logger.warning("Detected JavaScript-rendered content (minimal text)")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking if content is static: {e}")
            return True  # Assume static on error
    
    def extract(self, html: str, url: str) -> ExtractedContent:
        """
        Extract clean text content from HTML.
        
        Args:
            html: Raw HTML string
            url: Source URL (for context)
        
        Returns:
            ExtractedContent: Contains title, text, metadata
        """
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Check if content is static
            is_static = self.is_static_content(html)
            
            # Extract title
            title = self._extract_title(soup)
            
            # Remove unwanted tags
            for tag_name in self.REMOVE_TAGS:
                for tag in soup.find_all(tag_name):
                    tag.decompose()
            
            # Extract main content
            text = self._extract_main_content(soup)
            
            # Normalize whitespace
            text = self._normalize_whitespace(text)
            
            # Count words
            word_count = len(text.split())
            
            logger.info(f"Extracted {word_count} words from {url}")
            
            return ExtractedContent(
                title=title,
                text=text,
                is_static=is_static,
                word_count=word_count,
                metadata={'url': url}
            )
            
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {e}")
            return ExtractedContent(
                title=url,
                text="",
                is_static=False,
                word_count=0,
                metadata={'url': url, 'error': str(e)}
            )
    
    def _extract_title(self, soup: BeautifulSoup) -> str:
        """
        Extract title from HTML.
        
        Tries <title> tag first, then first <h1>.
        
        Args:
            soup: BeautifulSoup object
        
        Returns:
            str: Page title
        """
        # Try <title> tag
        title_tag = soup.find('title')
        if title_tag and title_tag.string:
            return title_tag.string.strip()
        
        # Try first <h1>
        h1_tag = soup.find('h1')
        if h1_tag:
            return h1_tag.get_text(strip=True)
        
        # Default to empty string
        return ""
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """
        Extract main content from HTML.
        
        Prioritizes semantic HTML5 elements like <article> and <main>.
        
        Args:
            soup: BeautifulSoup object
        
        Returns:
            str: Extracted text content
        """
        # Try to find main content area
        main_content = None
        
        # Priority 1: <article> or <main> tags
        for tag_name in self.MAIN_CONTENT_TAGS:
            main_content = soup.find(tag_name)
            if main_content:
                logger.debug(f"Found main content in <{tag_name}> tag")
                break
        
        # Priority 2: div with role="main"
        if not main_content:
            main_content = soup.find('div', role='main')
            if main_content:
                logger.debug("Found main content in div[role='main']")
        
        # Priority 3: Common class/id patterns
        if not main_content:
            for pattern in ['content', 'main', 'article', 'post']:
                main_content = soup.find(['div', 'section'], class_=re.compile(pattern, re.I))
                if main_content:
                    logger.debug(f"Found main content with class pattern '{pattern}'")
                    break
                
                main_content = soup.find(['div', 'section'], id=re.compile(pattern, re.I))
                if main_content:
                    logger.debug(f"Found main content with id pattern '{pattern}'")
                    break
        
        # Fallback: use body
        if not main_content:
            main_content = soup.find('body')
            logger.debug("Using <body> as main content (no specific content area found)")
        
        # Extract text from content tags
        if main_content:
            text_parts = []
            for tag in main_content.find_all(self.CONTENT_TAGS):
                text = tag.get_text(strip=True)
                if text:
                    text_parts.append(text)
            
            return '\n'.join(text_parts)
        
        return ""
    
    def _normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace in text.
        
        - Collapse multiple spaces to single space
        - Reduce excessive newlines to maximum 2
        - Strip leading/trailing whitespace
        
        Args:
            text: Text to normalize
        
        Returns:
            str: Normalized text
        """
        # Collapse multiple spaces to single space
        text = re.sub(r' +', ' ', text)
        
        # Reduce excessive newlines to maximum 2
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Strip leading/trailing whitespace
        text = text.strip()
        
        return text
