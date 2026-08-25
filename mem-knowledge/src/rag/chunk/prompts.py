"""Prompts used by the DeepDoc-free chunk pipelines."""

from __future__ import annotations


def vision_llm_figure_describe_prompt(lang: str = "Chinese") -> str:
    """Build the complete-image description prompt without a template runtime."""

    if lang.lower() == "chinese":
        return (
            "请准确描述图片中明确可见的内容。识别视觉类型、标题、坐标轴、图例、"
            "标签、数据点、趋势、注释和说明；只输出图片中实际存在的信息，不要猜测。"
        )
    return (
        "Describe only information explicitly visible in the image. Identify the visual type, "
        "title, axes, legends, labels, data points, trends, captions, and annotations without "
        "guessing."
    )


__all__ = ["vision_llm_figure_describe_prompt"]
