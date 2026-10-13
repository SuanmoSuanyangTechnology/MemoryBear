"""Wiki models using the shared release schema; no execution logic."""

from mem_knowledge_models.wiki import (
    WikiDocumentPageTaskColumns,
    WikiDocumentStateColumns,
    WikiFolderColumns,
    WikiPageColumns,
    WikiPageLinkColumns,
    WikiPageSourceColumns,
)

from app.db import Base


class WikiFolder(WikiFolderColumns, Base):
    __tablename__ = "wiki_folders"


class WikiPage(WikiPageColumns, Base):
    __tablename__ = "wiki_pages"


class WikiPageSource(WikiPageSourceColumns, Base):
    __tablename__ = "wiki_page_sources"


class WikiPageLink(WikiPageLinkColumns, Base):
    __tablename__ = "wiki_page_links"


class WikiDocumentState(WikiDocumentStateColumns, Base):
    __tablename__ = "wiki_document_states"


class WikiDocumentPageTask(WikiDocumentPageTaskColumns, Base):
    __tablename__ = "wiki_document_page_tasks"
