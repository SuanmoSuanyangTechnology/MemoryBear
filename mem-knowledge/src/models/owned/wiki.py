"""Wiki models using the shared release schema; no execution logic."""

from mem_knowledge_models.wiki import (
    WikiDocumentPageTaskColumns,
    WikiDocumentStateColumns,
    WikiFolderColumns,
    WikiPageColumns,
    WikiPageLinkColumns,
    WikiPageSourceColumns,
)

from ...db import KnowledgeBase


class WikiFolder(WikiFolderColumns, KnowledgeBase):
    __tablename__ = "wiki_folders"


class WikiPage(WikiPageColumns, KnowledgeBase):
    __tablename__ = "wiki_pages"


class WikiPageSource(WikiPageSourceColumns, KnowledgeBase):
    __tablename__ = "wiki_page_sources"


class WikiPageLink(WikiPageLinkColumns, KnowledgeBase):
    __tablename__ = "wiki_page_links"


class WikiDocumentState(WikiDocumentStateColumns, KnowledgeBase):
    __tablename__ = "wiki_document_states"


class WikiDocumentPageTask(WikiDocumentPageTaskColumns, KnowledgeBase):
    __tablename__ = "wiki_document_page_tasks"
