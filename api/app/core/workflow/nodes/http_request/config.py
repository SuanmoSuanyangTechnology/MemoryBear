from typing import Literal

from pydantic import Field, BaseModel, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import HttpRequestMethod, HttpAuthType, HttpContentType, HttpErrorHandle


class HttpAuthConfig(BaseModel):
    auth_type: HttpAuthType = Field(
        default=HttpAuthType.NONE,
        description="Type of HTTP authentication to use",
    )

    header: str = Field(
        default="",
        description="Custom HTTP Authorization header (used if auth_type is CUSTOM)",
    )

    api_key: str = Field(
        default="",
        description="API key for authentication (used if auth_type is not NONE)",
    )

    @field_validator("header")
    @classmethod
    def validate_header(cls, v, info):
        auth_type = info.data.get("auth_type")
        if auth_type == HttpAuthType.CUSTOM and not v:
            raise ValueError("Custom auth header not specified")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v, info):
        auth_type = info.data.get("auth_type")
        if auth_type != HttpAuthType.NONE and not v:
            raise ValueError("API key for authentication not specified")
        return v


class HttpFormData(BaseModel):
    key: str = Field(
        ...,
        description="Form-data field name",
    )

    type: Literal["text", "file"] = Field(
        ...,
        description="Form-data type: 'text' or 'file'"
    )

    value: str = Field(
        ...,
        description="Form-data field value",
    )


class HttpContentTypeConfig(BaseModel):
    content_type: HttpContentType = Field(
        ...,
        description="HTTP content type of the request body",
    )

    data: list[HttpFormData] | dict | str = Field(
        ...,
        description="Data of the HTTP request body; type depends on content_type",
    )

    @field_validator("data")
    @classmethod
    def validate_data(cls, v, info):
        content_type = info.data.get("content_type")
        if content_type == HttpContentType.FROM_DATA and not isinstance(v, HttpFormData):
            raise ValueError("When content_type is 'form-data', data must be of type HttpFormData")
        elif content_type in [HttpContentType.JSON, HttpContentType.WWW_FORM] and not isinstance(v, dict):
            raise ValueError("When content_type is JSON or x-www-form-urlencoded, data must be a object")
        elif content_type in [HttpContentType.RAW, HttpContentType.BINARY] and not isinstance(v, str):
            raise ValueError("When content_type is raw/binary, data must be a string (File descriptor)")
        return v


class HttpTimeOutConfig(BaseModel):
    connect_timeout: int = Field(
        default=5,
        description="Connection timeout in seconds",
    )

    read_timeout: int = Field(
        default=5,
        description="Read timeout in seconds",
    )

    write_timeout: int = Field(
        default=5,
        description="Write timeout in seconds",
    )


class HttpRetryConfig(BaseModel):
    max_attempts: int = Field(
        default=1,
        description="Maximum number of retry attempts for failed requests",
    )
    retry_interval: int = Field(
        default=100,
        description="Interval between retries in milliseconds",
    )


class HttpErrorDefaultTamplete(BaseModel):
    body: str = Field(
        default="",
        description="Default body returned on HTTP error",
    )

    status_code: int = Field(
        default=400,
        description="Default HTTP status code returned on error",
    )

    headers: dict = Field(
        default_factory=dict,
        description="Default HTTP headers returned on error",
    )


class HttpErrorHandleConfig(BaseModel):
    method: HttpErrorHandle = Field(
        default=HttpErrorHandle.NONE,
        description="Error handling strategy: 'none', 'default', or 'branch'",
    )

    default: HttpErrorDefaultTamplete = Field(
        ...,
        description="Default response template for error handling",
    )


class HttpRequestNodeConfig(BaseNodeConfig):
    method: HttpRequestMethod = Field(
        ...,
        description="HTTP method for the request (GET, POST, etc.)",
    )

    url: str = Field(
        ...,
        description="URL of the HTTP request",
    )

    auth: HttpAuthConfig = Field(
        ...,
        description="HTTP authentication configuration",
    )

    headers: dict = Field(
        default_factory=dict,
        description="HTTP request headers",
    )

    params: dict = Field(
        default_factory=dict,
        description="Query parameters for the HTTP request",
    )

    body: HttpContentTypeConfig = Field(
        ...,
        description="HTTP request body configuration",
    )

    verify_ssl: bool = Field(
        ...,
        description="Whether to verify SSL certificates",
    )

    timeouts: HttpTimeOutConfig = Field(
        ...,
        description="Timeout settings for the request",
    )

    retry: HttpRetryConfig = Field(
        ...,
        description="Retry configuration for failed requests",
    )

    error_handle: HttpErrorHandleConfig = Field(
        ...,
        description="Configuration for handling HTTP request errors",
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "method": "GET",
                    "url": "{{sys.message}}",
                    "auth": {
                        "auth_type": "none",
                        "header": "",
                        "api_key": ""
                    },
                    "headers": {
                        # "Content-Type": "application/json",
                        # "User-Agent": "Workflow-HttpNode/1.0"
                    },
                    "params": {},
                    "body": {
                        "content_type": "none",
                        "data": ""
                    },
                    "verify_ssl": True,
                    "timeouts": {
                        "connect_timeout": 5,
                        "read_timeout": 30,
                        "write_timeout": 10
                    },
                    "retry": {
                        "max_attempts": 3,
                        "retry_interval": 500
                    },
                    "error_handle": {
                        "method": "default",
                        "default": {
                            "body": "Upstream service unavailable",
                            "status_code": 502,
                            "headers": {
                                "Content-Type": "text/plain"
                            }
                        }
                    }
                }
            ]
        }


class HttpRequestNodeOutput(BaseModel):
    body: str = Field(
        ...,
        description="Body of the HTTP response",
    )

    status_code: int = Field(
        ...,
        description="HTTP response status code",
    )

    headers: dict = Field(
        ...,
        description="Http response headers"
    )

    output: str = Field(
        default="SUCCESS",
        description="HTTP response body",
    )

    # files: list[File] = Field(
    #     ...
    # )
