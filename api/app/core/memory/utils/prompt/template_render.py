import os
from jinja2 import Environment, FileSystemLoader
from typing import List, Dict, Any


# Setup Jinja2 environment
prompt_dir = os.path.join(os.path.dirname(__file__), "prompts")
prompt_env = Environment(loader=FileSystemLoader(prompt_dir))

async def render_evaluate_prompt(evaluate_data: List[Any], schema: Dict[str, Any]) -> str:
    """
    Renders the evaluate prompt using the evaluate.jinja2 template.

    Args:
        evaluate_data: The data to evaluate
        schema: The JSON schema to use for the output.

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("evaluate.jinja2")

    rendered_prompt = template.render(evaluate_data=evaluate_data, json_schema=schema)

    return rendered_prompt

async def render_reflexion_prompt(data: Dict[str, Any], schema: Dict[str, Any]) -> str:
    """
    Renders the reflexion prompt using the extract_temporal.jinja2 template.

    Args:
        data: The data to reflex on.
        schema: The JSON schema to use for the output.

    Returns:
        Rendered prompt content as a string.
    """
    template = prompt_env.get_template("reflexion.jinja2")

    rendered_prompt = template.render(data=data, json_schema=schema)

    return rendered_prompt
