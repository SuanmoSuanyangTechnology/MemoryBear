"""Configuration rows share the owner primary key; no relationships cross share boundaries."""

from ...db import KnowledgeBase
from ...parser_configuration.fields import Scope
from ...parser_configuration.schema import configuration_table


class KnowledgeParserConfig(KnowledgeBase):
    __tablename__ = "knowledge_parser_configs"
    __table__ = configuration_table(__tablename__, KnowledgeBase.metadata, Scope.KNOWLEDGE)


class DocumentParserConfig(KnowledgeBase):
    __tablename__ = "document_parser_configs"
    __table__ = configuration_table(__tablename__, KnowledgeBase.metadata, Scope.DOCUMENT)
