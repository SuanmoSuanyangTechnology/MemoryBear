from pydantic import Field
from app.core.workflow.nodes.base_config import BaseNodeConfig


class DocExtractorNodeConfig(BaseNodeConfig):
    file_selector: str = Field(
        ...,
        description="File variable selector, e.g. {{ sys.files }} or {{ node_id.file }}"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "file_selector": "{{ sys.files }}"
                }
            ]
        }
