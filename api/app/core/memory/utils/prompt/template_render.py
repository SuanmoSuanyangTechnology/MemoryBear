import os
from jinja2 import Environment, FileSystemLoader
from typing import List, Dict, Any


# Setup Jinja2 environment
prompt_dir = os.path.join(os.path.dirname(__file__), "prompts")
prompt_env = Environment(loader=FileSystemLoader(prompt_dir))

async def render_evaluate_prompt(evaluate_data: List[Any], schema: Dict[str, Any],
                                 baseline: str = "TIME",
                                 memory_verify: bool = False,quality_assessment:bool = False,
                                 statement_databasets: List[str] = [],language_type:str = "zh") -> str:
    """
    Renders the evaluate prompt using the evaluate_optimized.jinja2 template.

    Args:
        evaluate_data: The data to evaluate
        schema: The JSON schema to use for the output.
        baseline: The baseline type for conflict detection (TIME/FACT/TIME-FACT)
        memory_verify: Whether to enable memory verification for privacy detection

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("evaluate.jinja2")

    rendered_prompt = template.render(
        evaluate_data=evaluate_data,
        json_schema=schema,
        baseline=baseline,
        memory_verify=memory_verify,
        quality_assessment=quality_assessment,
        statement_databasets=statement_databasets,
        language_type=language_type
    )
    return rendered_prompt

async def render_reflexion_prompt(data: Dict[str, Any], schema: Dict[str, Any], baseline: str, memory_verify: bool = False,
                                  statement_databasets: List[str] = [],language_type:str = "zh") -> str:
    """
    Renders the reflexion prompt using the reflexion_optimized.jinja2 template.

    Args:
        data: The data to reflex on.
        schema: The JSON schema to use for the output.
        baseline: The baseline type for conflict resolution.

    Returns:
        Rendered prompt content as a string.
    """
    template = prompt_env.get_template("reflexion.jinja2")

    rendered_prompt = template.render(data=data, json_schema=schema,
                                      baseline=baseline,memory_verify=memory_verify,
                                      statement_databasets=statement_databasets,language_type=language_type)

    return rendered_prompt
