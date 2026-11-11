"""Identify knowledge routes for their scoped validation envelope."""


def is_knowledge_request_validation_error(path: str) -> bool:
    parts = path.strip("/").split("/")
    return (
        len(parts) >= 2
        and parts[0] in {"api", "v1"}
        and parts[1]
        in {
            "knowledges",
            "documents",
            "files",
            "chunks",
            "knowledgeshares",
        }
    )
