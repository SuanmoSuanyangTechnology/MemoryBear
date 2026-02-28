# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/24 15:58
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.workflow.adapters.errors import ExceptionDefineition
from app.schemas.workflow_schema import (
    EdgeDefinition,
    NodeDefinition,
    VariableDefinition,
    ExecutionConfig,
    TriggerConfig
)


class PlatformType(StrEnum):
    MEMORY_BEAR = "memory_bear"
    DIFY = "dify"
    COZE = "coze"


class PlatformMetadata(BaseModel):
    platform_name: str
    version: str
    support_node_types: list[str]


class WorkflowParserResult(BaseModel):
    success: bool
    platform: PlatformMetadata
    execution_config: ExecutionConfig
    origin_config: dict[str, Any]
    trigger: TriggerConfig | None
    edges: list[EdgeDefinition] = Field(default_factory=list)
    nodes: list[NodeDefinition] = Field(default_factory=list)
    variables: list[VariableDefinition] = Field(default_factory=list)
    warnings: list[ExceptionDefineition] = Field(default_factory=list)
    errors: list[ExceptionDefineition] = Field(default_factory=list)


class WorkflowImportResult(BaseModel):
    success: bool
    temp_id: str | None = Field(..., description="cache id")
    workflow_id: str | None = Field(..., description="workflow id")
    edges: list[EdgeDefinition] = Field(default_factory=list)
    nodes: list[NodeDefinition] = Field(default_factory=list)
    variables: list[VariableDefinition] = Field(default_factory=list)
    warnings: list[ExceptionDefineition] = Field(default_factory=list)
    errors: list[ExceptionDefineition] = Field(default_factory=list)


class BasePlatformAdapter(ABC):
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.nodes: list[NodeDefinition] = []
        self.edges: list[EdgeDefinition] = []
        self.conv_variables: list[VariableDefinition] = []

        self.errors = []
        self.warnings = []

        self.branch_node_cache = defaultdict(list)
        self.error_branch_node_cache = []

    @abstractmethod
    def get_metadata(self) -> PlatformMetadata:
        """get platform metadata"""
        pass

    @abstractmethod
    def validate_config(self) -> bool:
        """platform configuration validate"""
        pass

    @abstractmethod
    def parse_workflow(self) -> WorkflowParserResult:
        """parse platform configuration to local config"""
        pass

    @abstractmethod
    def map_node_type(self, platform_node_type: str) -> str:
        pass
