"""Emotion read calculations and daily display formatting; no model execution."""

from __future__ import annotations

import logging
import statistics
from typing import Any

from src.common.vocabulary import (
    EMOTION_CODES,
    EMOTION_NAMES_EN,
    EMOTION_NAMES_ZH,
    NEGATIVE_EMOTIONS,
    POSITIVE_EMOTIONS,
    emotion_valence,
    normalize_emotion_type,
)

logger = logging.getLogger(__name__)


class EmotionCalculations:
    TOO_FEW_THRESHOLD = 5
    EMOTION_DISPLAY_NAMES = {"zh": dict(EMOTION_NAMES_ZH), "en": dict(EMOTION_NAMES_EN)}
    POSITIVE_EMOTIONS = POSITIVE_EMOTIONS
    NEGATIVE_EMOTIONS = NEGATIVE_EMOTIONS

    def __init__(self, repository) -> None:
        self.emotion_repo = repository

    async def get_emotion_tags(
        self,
        end_user_id: str,
        emotion_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        language: str = "zh",
    ) -> dict[str, Any]:
        """获取情绪标签统计"""
        try:
            logger.info(
                "获取情绪标签统计 user=%s type=%s start=%s end=%s limit=%s language=%s",
                end_user_id,
                emotion_type,
                start_date,
                end_date,
                limit,
                language,
            )
            rows = await self.emotion_repo.get_emotion_tags(
                end_user_id=end_user_id,
                emotion_type=emotion_type,
                start_date=start_date,
                end_date=end_date,
            )
            buckets: dict[str, dict[str, float]] = {
                code: {"count": 0, "intensity_sum": 0.0, "intensity_count": 0}
                for code in EMOTION_CODES
            }
            for row in rows:
                code = normalize_emotion_type(row.get("emotion_type"))
                if code is None:
                    continue
                bucket = buckets[code]
                bucket["count"] += row.get("count") or 0
                bucket["intensity_sum"] += row.get("intensity_sum") or 0.0
                bucket["intensity_count"] += row.get("intensity_count") or 0
            total_count = sum(bucket["count"] for bucket in buckets.values())
            selected_codes = set(
                sorted(EMOTION_CODES, key=lambda c: buckets[c]["count"], reverse=True)[
                    : max(limit, 0)
                ]
            )
            complete_tags = []
            for code in (c for c in EMOTION_CODES if c in selected_codes):
                bucket = buckets[code]
                count = bucket["count"]
                intensity_count = bucket["intensity_count"]
                complete_tags.append(
                    {
                        "emotion_type": code,
                        "count": count,
                        "percentage": round(count / total_count * 100, 2)
                        if total_count > 0
                        else 0.0,
                        "avg_intensity": round(bucket["intensity_sum"] / intensity_count, 3)
                        if intensity_count > 0
                        else 0.0,
                    }
                )
            time_range = {}
            if start_date:
                time_range["start_date"] = start_date
            if end_date:
                time_range["end_date"] = end_date
            response = {
                "tags": complete_tags,
                "total_count": total_count,
                "time_range": time_range if time_range else None,
            }
            logger.info(
                f"情绪标签统计完成: total_count={total_count}, tags_count={len(complete_tags)}"
            )
            return response
        except Exception as e:
            logger.error(f"获取情绪标签统计失败: {str(e)}", exc_info=True)
            raise

    async def get_emotion_wordcloud(
        self, end_user_id: str, emotion_type: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """获取情绪词云数据"""
        try:
            logger.info(f"获取情绪词云数据: user={end_user_id}, type={emotion_type}, limit={limit}")
            keywords = await self.emotion_repo.get_emotion_wordcloud(
                end_user_id=end_user_id, emotion_type=emotion_type, limit=limit
            )
            total_keywords = len(keywords)
            response = {"keywords": keywords, "total_keywords": total_keywords}
            logger.info(f"情绪词云数据获取完成: total_keywords={total_keywords}")
            return response
        except Exception as e:
            logger.error(f"获取情绪词云数据失败: {str(e)}", exc_info=True)
            raise

    def _calculate_positivity_rate(self, emotions: list[dict[str, Any]]) -> dict[str, Any]:
        """计算积极率"""
        positive_count = sum(1 for e in emotions if e.get("emotion_type") in POSITIVE_EMOTIONS)
        negative_count = sum(1 for e in emotions if e.get("emotion_type") in NEGATIVE_EMOTIONS)
        neutral_count = sum(
            1 for e in emotions if emotion_valence(e.get("emotion_type")) == "neutral"
        )
        total_non_neutral = positive_count + negative_count
        if total_non_neutral > 0:
            score = positive_count / total_non_neutral * 100
        elif neutral_count > 0:
            score = 50.0
        else:
            score = None
        score_display = f"{score:.2f}" if score is not None else "N/A"
        logger.debug(
            "积极率计算 positive=%s negative=%s neutral=%s score=%s",
            positive_count,
            negative_count,
            neutral_count,
            score_display,
        )
        return {
            "score": round(score, 2) if score is not None else None,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": neutral_count,
        }

    def _calculate_stability(self, emotions: list[dict[str, Any]]) -> dict[str, Any]:
        """计算稳定性"""
        intensities = [
            e.get("emotion_intensity", 0.0)
            for e in emotions
            if e.get("emotion_intensity") is not None
        ]
        if len(intensities) >= 2:
            std_deviation = statistics.stdev(intensities)
        elif len(intensities) == 1:
            std_deviation = 0.0
        else:
            std_deviation = 0.0
        score = (1 - min(std_deviation, 1.0)) * 100
        logger.debug(
            "稳定性计算 intensities_count=%s std_deviation=%.3f score=%.2f",
            len(intensities),
            std_deviation,
            score,
        )
        return {"score": round(score, 2), "std_deviation": round(std_deviation, 3)}

    def _calculate_resilience(self, emotions: list[dict[str, Any]]) -> dict[str, Any]:
        """计算恢复力"""
        recovery_count = 0
        negative_count = 0
        for i in range(len(emotions)):
            current_emotion = emotions[i].get("emotion_type")
            if current_emotion in NEGATIVE_EMOTIONS:
                negative_count += 1
                if i + 1 < len(emotions):
                    next_emotion = emotions[i + 1].get("emotion_type")
                    if next_emotion in POSITIVE_EMOTIONS:
                        recovery_count += 1
        if negative_count > 0:
            recovery_rate = recovery_count / negative_count
            score = recovery_rate * 100
        else:
            recovery_rate = 1.0
            score = 100.0
        logger.debug(
            "恢复力计算 negative_count=%s recovery_count=%s score=%.2f",
            negative_count,
            recovery_count,
            score,
        )
        return {"score": round(score, 2), "recovery_rate": round(recovery_rate, 3)}

    async def calculate_emotion_health_index(
        self, end_user_id: str, time_range: str = "30d"
    ) -> dict[str, Any]:
        """计算情绪健康指数"""
        try:
            logger.info(f"计算情绪健康指数: user={end_user_id}, time_range={time_range}")
            emotions = await self.emotion_repo.get_emotions_in_range(
                end_user_id=end_user_id, time_range=time_range
            )
            if not emotions and time_range != "90d":
                logger.info(f"用户 {end_user_id} 在 {time_range} 内无数据，尝试90天范围")
                emotions = await self.emotion_repo.get_emotions_in_range(
                    end_user_id=end_user_id, time_range="90d"
                )
                if emotions:
                    time_range = "90d"
            if not emotions:
                logger.warning(f"用户 {end_user_id} 在时间范围 {time_range} 内没有情绪数据")
                return {
                    "health_score": None,
                    "level": "无数据",
                    "dimensions": {
                        "positivity_rate": {
                            "score": None,
                            "positive_count": 0,
                            "negative_count": 0,
                            "neutral_count": 0,
                        },
                        "stability": {"score": None, "std_deviation": 0.0},
                        "resilience": {"score": None, "recovery_rate": 0.0},
                    },
                    "emotion_distribution": {},
                    "time_range": time_range,
                }
            positivity_rate = self._calculate_positivity_rate(emotions)
            stability = self._calculate_stability(emotions)
            resilience = self._calculate_resilience(emotions)
            positivity_score = (
                positivity_rate["score"] if positivity_rate["score"] is not None else 0.0
            )
            health_score = (
                positivity_score * 0.4 + stability["score"] * 0.3 + resilience["score"] * 0.3
            )
            if health_score >= 80:
                level = "优秀"
            elif health_score >= 60:
                level = "良好"
            elif health_score >= 40:
                level = "一般"
            else:
                level = "较差"
            emotion_distribution = {}
            for emotion_type in EMOTION_CODES:
                count = sum(1 for e in emotions if e.get("emotion_type") == emotion_type)
                emotion_distribution[emotion_type] = count
            response = {
                "health_score": round(health_score, 2),
                "level": level,
                "dimensions": {
                    "positivity_rate": positivity_rate,
                    "stability": stability,
                    "resilience": resilience,
                },
                "emotion_distribution": emotion_distribution,
                "time_range": time_range,
            }
            logger.info(f"情绪健康指数计算完成: score={health_score:.2f}, level={level}")
            return response
        except Exception as e:
            logger.error(f"计算情绪健康指数失败: {str(e)}", exc_info=True)
            raise

    def _format_daily_row(
        self, row: dict[str, Any], language: str, include_summary: bool = False
    ) -> dict[str, Any]:
        """将聚合行 {stat_date, dialogue_count, emotions} 格式化为接口 item"""
        emotions = []
        for item in row.get("emotions") or []:
            count = int(item.get("count", 0))
            percentage = (
                round(count / row["dialogue_count"] * 100, 2) if row["dialogue_count"] > 0 else 0.0
            )
            emotions.append(
                {
                    "type": item.get("type"),
                    "display_name": self.get_display_name(item.get("type"), language),
                    "count": count,
                    "percentage": percentage,
                }
            )
        result = {
            "date": row["stat_date"].isoformat(),
            "dialogue_count": row["dialogue_count"],
            "emotions": emotions,
        }
        if include_summary:
            result["summary"] = self._build_daily_summary(row)
        return result

    def _build_daily_summary(self, row: dict[str, Any]) -> str:
        """日级结论文案（时间轴每个活跃日一条）"""
        if row["dialogue_count"] < self.TOO_FEW_THRESHOLD:
            return f"样本仅 {row['dialogue_count']} 条，暂不足以判断稳定的情绪结构"
        names = [
            self.get_display_name(item.get("type"), "zh")
            for item in row.get("emotions") or []
            if item.get("type")
        ]
        if not names:
            return "当日情绪数据缺失，暂无法判断情绪结构"
        if len(names) == 1:
            return f"情绪类型较为单一，以{names[0]}为主"
        if len(names) == 2:
            return f"以{names[0]}和{names[1]}为主"
        if len(names) == 3:
            return f"以{names[0]}、{names[1]}和{names[2]}为主"
        return f"情绪类型较分散，以{names[0]}、{names[1]}和{names[2]}为主"

    def _build_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """头部统计：最近 N 个活跃日口径（dialogue_count 总和、情绪种类并集）"""
        dialogue_count = sum(r["dialogue_count"] for r in rows)
        emotion_types = set()
        for r in rows:
            for item in r.get("emotions") or []:
                if item.get("type"):
                    emotion_types.add(item["type"])
        return {"dialogue_count": dialogue_count, "emotion_type_count": len(emotion_types)}

    def _build_conclusion(
        self, prev_day: dict[str, Any], latest_day: dict[str, Any], language: str
    ) -> dict[str, Any]:
        """核心结论（仅 normal 模式调用：两天均 ≥ 阈值）"""
        prev_top = self._dominant_emotion(prev_day)
        latest_top = self._dominant_emotion(latest_day)
        if prev_top and latest_top and (prev_top != latest_top):
            from_name = self.get_display_name(prev_top, "zh")
            to_name = self.get_display_name(latest_top, "zh")
            suffix = self._shift_valence_suffix(prev_top, latest_top)
            return {
                "type": "dominant_shift",
                "title": "主导情绪迁移",
                "message": f"从{from_name}主导转向{to_name}主导{suffix}",
                "from_emotion": prev_top,
                "to_emotion": latest_top,
            }
        top3_ratio = self._combined_top3_ratio(prev_day, latest_day)
        if top3_ratio is not None:
            if top3_ratio >= 0.7:
                return {
                    "type": "concentrated",
                    "title": "情绪集中",
                    "message": (
                        f"最近两个活跃日 TOP3 情绪占比达 {round(top3_ratio * 100, 2)}%，"
                        "情绪结构较为集中"
                    ),
                }
            if top3_ratio < 0.4:
                return {
                    "type": "scattered",
                    "title": "情绪分散",
                    "message": (
                        f"最近两个活跃日 TOP3 情绪占比仅 {round(top3_ratio * 100, 2)}%，"
                        "情绪结构较为分散"
                    ),
                }
        dominant_name = self.get_display_name(latest_top, "zh") if latest_top else ""
        return {
            "type": "stable",
            "title": "情绪稳定",
            "message": f"连续两个活跃日均以{dominant_name}为主导，情绪状态保持稳定",
        }

    @staticmethod
    def _dominant_emotion(row: dict[str, Any]) -> str | None:
        """取主导情绪 type（emotions 已按 count 降序，并列取首位）"""
        emotions = row.get("emotions") or []
        if not emotions:
            return None
        return emotions[0].get("type")

    @staticmethod
    def _combined_top3_ratio(prev_day: dict[str, Any], latest_day: dict[str, Any]) -> float | None:
        """两天合并后的 TOP3 情绪占比（0~1），无数据返回 None"""
        merged: dict[str, int] = {}
        for row in (prev_day, latest_day):
            for item in row.get("emotions") or []:
                etype = item.get("type")
                if etype:
                    merged[etype] = merged.get(etype, 0) + int(item.get("count", 0))
        total = sum(merged.values())
        if total <= 0:
            return None
        top3 = sorted(merged.values(), reverse=True)[:3]
        return sum(top3) / total

    @classmethod
    def _shift_valence_suffix(cls, from_emotion: str, to_emotion: str) -> str:
        """迁移文案的情绪效价后缀"""
        if to_emotion in cls.POSITIVE_EMOTIONS and from_emotion not in cls.POSITIVE_EMOTIONS:
            return "，情绪状态明显改善"
        if to_emotion in cls.NEGATIVE_EMOTIONS and from_emotion not in cls.NEGATIVE_EMOTIONS:
            return "，情绪状态有所回落"
        return ""

    @classmethod
    def get_display_name(cls, emotion_type: str | None, language: str) -> str:
        """情绪英文枚举 → 指定语言的展示名（未知类型回退英文枚举本身）"""
        if not emotion_type:
            return ""
        names = cls.EMOTION_DISPLAY_NAMES.get(language) or cls.EMOTION_DISPLAY_NAMES["zh"]
        return names.get(emotion_type, emotion_type)
