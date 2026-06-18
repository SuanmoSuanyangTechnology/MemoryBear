import logging
from io import BytesIO

from markdown import markdown
from PIL import Image

from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import MarkdownElementExtractor
from app.core.rag.deepdoc.parser import MarkdownParser as RAGMarkdownParser
from app.core.rag.nlp import find_codec


class MarkdownParser(RAGMarkdownParser, DocumentParser):
    def __init__(self, chunk_token_num=128):
        super().__init__(chunk_token_num)

    def parse(self, ctx):
        return self(
            ctx.filename,
            ctx.binary,
            separate_tables=False,
            delimiter=None,
        )

    def md_to_html(self, sections):
        if not sections:
            return []
        if isinstance(sections, type("")):
            text = sections
        elif isinstance(sections[0], type("")):
            text = sections[0]
        else:
            return []

        from bs4 import BeautifulSoup

        html_content = markdown(text)
        soup = BeautifulSoup(html_content, "html.parser")
        return soup

    def get_picture_urls(self, soup):
        if soup:
            return [img.get("src") for img in soup.find_all("img") if img.get("src")]
        return []

    def get_hyperlink_urls(self, soup):
        if soup:
            return set([a.get("href") for a in soup.find_all("a") if a.get("href")])
        return []

    def get_pictures(self, text):
        import requests

        soup = self.md_to_html(text)
        image_urls = self.get_picture_urls(soup)
        images = []
        for url in image_urls:
            if not url:
                continue
            try:
                if url.startswith(("http://", "https://")):
                    response = requests.get(url, stream=True, timeout=30)
                    if response.status_code == 200 and response.headers["Content-Type"] and response.headers["Content-Type"].startswith("image/"):
                        img = Image.open(BytesIO(response.content)).convert("RGB")
                        images.append(img)
                else:
                    from pathlib import Path

                    local_path = Path(url)
                    if not local_path.exists():
                        logging.warning(f"Local image file not found: {url}")
                        continue
                    img = Image.open(url).convert("RGB")
                    images.append(img)
            except Exception as exc:
                logging.error(f"Failed to download/open image from {url}: {exc}")
                continue

        return images if images else None

    def __call__(self, filename, binary=None, separate_tables=True, delimiter=None):
        if binary:
            encoding = find_codec(binary)
            text = binary.decode(encoding, errors="ignore")
        else:
            with open(filename, "r") as file:
                text = file.read()

        remainder, tables = self.extract_tables_and_remainder(f"{text}\n", separate_tables=separate_tables)
        extractor = MarkdownElementExtractor(text)
        element_sections = extractor.extract_elements(delimiter)
        sections = [(element, "") for element in element_sections]
        table_results = []
        for table in tables:
            table_results.append(((None, markdown(table, extensions=["markdown.extensions.tables"])), ""))
        return sections, table_results
