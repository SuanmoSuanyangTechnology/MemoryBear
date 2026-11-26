"""Shared processing responses for document upload and rechunk."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel


class ProcessingError(BaseModel):
    code: Literal["KB_TASK_DISPATCH_FAILED", "KB_TASK_DISPATCH_UNKNOWN"]
    message: str


class Submitted(BaseModel):
    status: Literal["submitted"]
    task_id: UUID


class AlreadyProcessing(BaseModel):
    status: Literal["already_processing"]
    task_id: UUID | None = None


class DispatchFailed(BaseModel):
    status: Literal["dispatch_failed"]
    error: ProcessingError


class DispatchUnknown(BaseModel):
    status: Literal["dispatch_unknown"]
    task_id: UUID | None = None
    error: ProcessingError


class ProcessingDispatchResult(
    RootModel[
        Annotated[
            Submitted | AlreadyProcessing | DispatchFailed | DispatchUnknown,
            Field(discriminator="status"),
        ]
    ]
):
    """One wire contract shared by upload and rechunk."""


class RechunkData(BaseModel):
    document_id: UUID
    processing: ProcessingDispatchResult


class UploadAndParseData(RechunkData):
    file_id: UUID
    file_saved: Literal[True]
