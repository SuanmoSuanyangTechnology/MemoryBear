import logging
import threading
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent


class PromptRenderError(Exception):
    def __init__(self, template_name: str, error: Exception):
        self.template_name = template_name
        self.error = error
        super().__init__(f"Failed to render prompt '{template_name}': {error}")


class PromptManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_once()
        return cls._instance

    def _init_once(self):
        self.env = Environment(
            loader=FileSystemLoader(str(PROMPT_DIR)),
            autoescape=False,
            keep_trailing_newline=True,
        )
        logger.info(f"PromptManager initialized: template_dir={PROMPT_DIR}")

    def __repr__(self):
        templates = self.list_templates()
        return f"<PromptManager: {len(templates)} prompts: {templates}>"

    def list_templates(self) -> list[str]:
        return [
            Path(name).stem
            for name in self.env.loader.list_templates()
            if name.endswith('.jinja2')
        ]

    def get(self, name: str) -> str:
        template_name = self._resolve_name(name)
        try:
            source, _, _ = self.env.loader.get_source(self.env, template_name)
            return source
        except TemplateNotFound:
            raise FileNotFoundError(
                f"Prompt '{name}' not found. "
                f"Available: {self.list_templates()}"
            )

    def render(self, name: str, **kwargs) -> str:
        template_name = self._resolve_name(name)
        try:
            template = self.env.get_template(template_name)
            return template.render(**kwargs)
        except TemplateNotFound:
            raise FileNotFoundError(
                f"Prompt '{name}' not found. "
                f"Available: {self.list_templates()}"
            )
        except TemplateSyntaxError as e:
            logger.error(f"Prompt syntax error in '{name}': {e}", exc_info=True)
            raise PromptRenderError(name, e)
        except Exception as e:
            logger.error(f"Prompt render failed for '{name}': {e}", exc_info=True)
            raise PromptRenderError(name, e)

    @staticmethod
    def _resolve_name(name: str) -> str:
        if not name.endswith('.jinja2'):
            return f"{name}.jinja2"
        return name


prompt_manager = PromptManager()
