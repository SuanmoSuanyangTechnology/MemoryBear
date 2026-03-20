# Internationalization (i18n) Module

This module provides internationalization support for the MemoryBear API.

## Components

- `service.py` - Translation service and core translation logic
- `middleware.py` - Language detection middleware
- `dependencies.py` - FastAPI dependency injection functions
- `exceptions.py` - Internationalized exception classes

## Usage

### Basic Translation

```python
from app.i18n import t

# Simple translation
message = t("common.success.created")

# Parameterized translation
message = t("common.validation.required", field="Name")
```

### Enum Translation

```python
from app.i18n import t_enum

# Translate enum value
role_display = t_enum("workspace_role", "manager")
```

### In FastAPI Endpoints

```python
from fastapi import Depends
from app.i18n.dependencies import get_translator

@router.post("/workspaces")
async def create_workspace(
    data: WorkspaceCreate,
    t: Callable = Depends(get_translator)
):
    workspace = await workspace_service.create(data)
    return {
        "success": True,
        "message": t("workspace.created_successfully"),
        "data": workspace
    }
```

## Configuration

See `app/core/config.py` for i18n configuration options:

- `I18N_DEFAULT_LANGUAGE` - Default language (default: "zh")
- `I18N_SUPPORTED_LANGUAGES` - Supported languages (default: "zh,en")
- `I18N_ENABLE_TRANSLATION_CACHE` - Enable caching (default: true)
- `I18N_LOG_MISSING_TRANSLATIONS` - Log missing translations (default: true)
