"""Legacy emotional health and fallback suggestions, without I/O dependencies."""

import logging
import statistics
from typing import Any

from src.domain.shared.emotion_vocabulary import (
    EMOTION_CODES,
    NEGATIVE_EMOTIONS,
    POSITIVE_EMOTIONS,
    emotion_valence,
)

logger = logging.getLogger(__name__)


class EmotionAnalysis:
    def _calculate_positivity_rate(self, emotions: list[dict[str, Any]]) -> dict[str, Any]:
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
            "positivity positive=%s negative=%s neutral=%s score=%s",
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
            "stability count=%s deviation=%.3f score=%.2f", len(intensities), std_deviation, score
        )
        return {"score": round(score, 2), "std_deviation": round(std_deviation, 3)}

    def _calculate_resilience(self, emotions: list[dict[str, Any]]) -> dict[str, Any]:
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
            "recovery negatives=%s recovered=%s score=%.2f", negative_count, recovery_count, score
        )
        return {"score": round(score, 2), "recovery_rate": round(recovery_rate, 3)}

    def analyze_patterns(self, emotions: list[dict[str, Any]]) -> dict[str, Any]:
        negative_emotion_counts = {}
        for emotion in emotions:
            emotion_type = emotion.get("emotion_type")
            if emotion_type in NEGATIVE_EMOTIONS:
                negative_emotion_counts[emotion_type] = (
                    negative_emotion_counts.get(emotion_type, 0) + 1
                )
        dominant_negative_emotion = None
        if negative_emotion_counts:
            dominant_negative_emotion = max(
                negative_emotion_counts, key=negative_emotion_counts.get
            )
        high_intensity_emotions = [
            {
                "type": e.get("emotion_type"),
                "intensity": e.get("emotion_intensity"),
                "created_at": e.get("created_at"),
            }
            for e in emotions
            if (e.get("emotion_intensity") or 0.0) >= 0.7
        ]
        intensities = [
            e.get("emotion_intensity", 0.0)
            for e in emotions
            if e.get("emotion_intensity") is not None
        ]
        if len(intensities) >= 2:
            std_dev = statistics.stdev(intensities)
            if std_dev > 0.3:
                volatility = "高"
            elif std_dev > 0.15:
                volatility = "中"
            else:
                volatility = "低"
        else:
            volatility = "未知"
        logger.debug(
            "patterns negative=%s intensity_count=%s volatility=%s",
            dominant_negative_emotion,
            len(high_intensity_emotions),
            volatility,
        )
        return {
            "dominant_negative_emotion": dominant_negative_emotion,
            "high_intensity_emotions": high_intensity_emotions[:5],
            "emotion_volatility": volatility,
        }

    def default_suggestions(
        self, health_data: dict[str, Any], language: str = "zh"
    ) -> dict[str, Any]:
        health_score = health_data.get("health_score") or 0
        if language == "en":
            if health_score >= 80:
                summary = "Your emotional health is excellent. Keep up the positive attitude."
            elif health_score >= 60:
                summary = "Your emotional health is good. Some adjustments can further improve it."
            elif health_score >= 40:
                summary = (
                    "Your emotional health needs attention. Consider taking improvement measures."
                )
            else:
                summary = (
                    "Your emotional health needs serious attention. "
                    "Consider seeking professional help."
                )
            suggestions = [
                dict(
                    type="Emotion Balance",
                    title="Maintain Emotional Balance",
                    content=(
                        "Through mindfulness meditation and deep breathing exercises, "
                        "help you better manage emotional fluctuations and improve "
                        "emotional stability."
                    ),
                    priority="High",
                    actionable_steps=[
                        "Practice 5-10 minutes of mindfulness meditation every morning",
                        "Take 3 deep breaths when feeling emotional fluctuations",
                        "Record daily emotional changes to identify triggers",
                    ],
                ),
                dict(
                    type="Activity Recommendation",
                    title="Increase Outdoor Activities",
                    content=(
                        "Moderate outdoor exercise can effectively improve mood and "
                        "enhance physical and mental health. Recommend 3-4 outdoor "
                        "activities per week."
                    ),
                    priority="Medium",
                    actionable_steps=[
                        "Schedule 2-3 30-minute walks per week",
                        "Try outdoor sports like cycling or hiking on weekends",
                        "Focus on surroundings and relax during outdoor activities",
                    ],
                ),
                dict(
                    type="Social Connection",
                    title="Strengthen Social Connections",
                    content=(
                        "Maintaining good social connections with friends and family can "
                        "provide emotional support and improve emotional health."
                    ),
                    priority="Medium",
                    actionable_steps=[
                        "Have a deep conversation with at least one friend or family member weekly",
                        "Join social activities or interest groups you enjoy",
                        "Actively share your feelings and thoughts",
                    ],
                ),
            ]
        else:
            if health_score >= 80:
                summary = "您的情绪健康状况优秀，请继续保持积极的生活态度。"
            elif health_score >= 60:
                summary = "您的情绪健康状况良好，可以通过一些调整进一步提升。"
            elif health_score >= 40:
                summary = "您的情绪健康需要关注，建议采取一些改善措施。"
            else:
                summary = "您的情绪健康需要重点关注，建议寻求专业帮助。"
            suggestions = [
                dict(
                    type="情绪平衡",
                    title="保持情绪平衡",
                    content="通过正念冥想和深呼吸练习，帮助您更好地管理情绪波动，提升情绪稳定性。",
                    priority="高",
                    actionable_steps=[
                        "每天早晨进行5-10分钟的正念冥想",
                        "感到情绪波动时，进行3次深呼吸",
                        "记录每天的情绪变化，识别触发因素",
                    ],
                ),
                dict(
                    type="活动建议",
                    title="增加户外活动",
                    content="适度的户外运动可以有效改善情绪，增强身心健康。建议每周进行3-4次户外活动。",
                    priority="中",
                    actionable_steps=[
                        "每周安排2-3次30分钟的散步",
                        "周末尝试户外运动如骑行或爬山",
                        "在户外活动时关注周围环境，放松心情",
                    ],
                ),
                dict(
                    type="社交联系",
                    title="加强社交联系",
                    content="与朋友和家人保持良好的社交联系，可以提供情感支持，改善情绪健康。",
                    priority="中",
                    actionable_steps=[
                        "每周至少与一位朋友或家人深入交流",
                        "参加感兴趣的社交活动或兴趣小组",
                        "主动分享自己的感受和想法",
                    ],
                ),
            ]
        return {"health_summary": summary, "suggestions": suggestions}

    def health(self, emotions, time_range):
        if not emotions:
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
        positive = self._calculate_positivity_rate(emotions)
        stable = self._calculate_stability(emotions)
        recovery = self._calculate_resilience(emotions)
        score = (positive["score"] or 0) * 0.4 + stable["score"] * 0.3 + recovery["score"] * 0.3
        return {
            "health_score": round(score, 2),
            "level": "优秀"
            if score >= 80
            else "良好"
            if score >= 60
            else "一般"
            if score >= 40
            else "较差",
            "dimensions": {
                "positivity_rate": positive,
                "stability": stable,
                "resilience": recovery,
            },
            "emotion_distribution": {
                code: sum(row["emotion_type"] == code for row in emotions) for code in EMOTION_CODES
            },
            "time_range": time_range,
        }
