"""Extract the four legacy insight sections without transport dependencies."""

import re


def insight_sections(raw: str, language: str) -> dict[str, str]:
    titles = (
        ["Overview", "Behavior Pattern", "Key Findings", "Growth Trajectory"]
        if language == "en"
        else ["总体概述", "行为模式", "关键发现", "成长轨迹"]
    )
    result = {}
    for key, title in zip(
        ("memory_insight", "behavior_pattern", "key_findings", "growth_trajectory"),
        titles,
        strict=True,
    ):
        match = re.search("【" + title + "】(.*?)(?=【|$)", raw, re.DOTALL)
        result[key] = match.group(1).strip() if match else ""
    return result
