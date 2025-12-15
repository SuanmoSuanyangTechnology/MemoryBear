"""
LoCoMo-specific metric calculations.

This module provides clean, simplified implementations of metrics used for
LoCoMo benchmark evaluation, including text normalization and F1 score variants.
"""

import re
from typing import Dict, Any


def normalize_text(text: str) -> str:
    """
    Normalize text for LoCoMo evaluation.
    
    Normalization steps:
    - Convert to lowercase
    - Remove commas
    - Remove stop words (a, an, the, and)
    - Remove punctuation
    - Normalize whitespace
    
    Args:
        text: Input text to normalize
        
    Returns:
        Normalized text string with consistent formatting
        
    Examples:
        >>> normalize_text("The cat, and the dog")
        'cat dog'
        >>> normalize_text("Hello, World!")
        'hello world'
    """
    # Ensure input is a string
    text = str(text) if text is not None else ""
    
    # Convert to lowercase
    text = text.lower()
    
    # Remove commas
    text = re.sub(r"[\,]", " ", text)
    
    # Remove stop words
    text = re.sub(r"\b(a|an|the|and)\b", " ", text)
    
    # Remove punctuation (keep only word characters and whitespace)
    text = re.sub(r"[^\w\s]", " ", text)
    
    # Normalize whitespace (collapse multiple spaces to single space)
    text = " ".join(text.split())
    
    return text


def locomo_f1_score(prediction: str, ground_truth: str) -> float:
    """
    Calculate LoCoMo F1 score for single-answer questions.
    
    Uses token-level precision and recall based on normalized text.
    Treats tokens as sets (no duplicate counting).
    
    Args:
        prediction: Model's predicted answer
        ground_truth: Correct answer
        
    Returns:
        F1 score between 0.0 and 1.0
        
    Examples:
        >>> locomo_f1_score("Paris", "Paris")
        1.0
        >>> locomo_f1_score("The cat", "cat")
        1.0
        >>> locomo_f1_score("dog", "cat")
        0.0
    """
    # Ensure inputs are strings
    pred_str = str(prediction) if prediction is not None else ""
    truth_str = str(ground_truth) if ground_truth is not None else ""
    
    # Normalize and tokenize
    pred_tokens = normalize_text(pred_str).split()
    truth_tokens = normalize_text(truth_str).split()
    
    # Handle empty cases
    if not pred_tokens or not truth_tokens:
        return 0.0
    
    # Convert to sets for comparison
    pred_set = set(pred_tokens)
    truth_set = set(truth_tokens)
    
    # Calculate true positives (intersection)
    true_positives = len(pred_set & truth_set)
    
    # Calculate precision and recall
    precision = true_positives / len(pred_set) if pred_set else 0.0
    recall = true_positives / len(truth_set) if truth_set else 0.0
    
    # Calculate F1 score
    if precision + recall == 0:
        return 0.0
    
    f1 = 2 * precision * recall / (precision + recall)
    return f1


def locomo_multi_f1(prediction: str, ground_truth: str) -> float:
    """
    Calculate LoCoMo F1 score for multi-answer questions.
    
    Handles comma-separated answers by:
    1. Splitting both prediction and ground truth by commas
    2. For each ground truth answer, finding the best matching prediction
    3. Averaging the F1 scores across all ground truth answers
    
    Args:
        prediction: Model's predicted answer (may contain multiple comma-separated answers)
        ground_truth: Correct answer (may contain multiple comma-separated answers)
        
    Returns:
        Average F1 score across all ground truth answers (0.0 to 1.0)
        
    Examples:
        >>> locomo_multi_f1("Paris, London", "Paris, London")
        1.0
        >>> locomo_multi_f1("Paris", "Paris, London")
        0.5
        >>> locomo_multi_f1("Paris, Berlin", "Paris, London")
        0.5
    """
    # Ensure inputs are strings
    pred_str = str(prediction) if prediction is not None else ""
    truth_str = str(ground_truth) if ground_truth is not None else ""
    
    # Split by commas and strip whitespace
    predictions = [p.strip() for p in pred_str.split(',') if p.strip()]
    ground_truths = [g.strip() for g in truth_str.split(',') if g.strip()]
    
    # Handle empty cases
    if not predictions or not ground_truths:
        return 0.0
    
    # For each ground truth, find the best matching prediction
    f1_scores = []
    for gt in ground_truths:
        # Calculate F1 with each prediction and take the maximum
        best_f1 = max(locomo_f1_score(pred, gt) for pred in predictions)
        f1_scores.append(best_f1)
    
    # Return average F1 across all ground truths
    return sum(f1_scores) / len(f1_scores)


def get_category_name(item: Dict[str, Any]) -> str:
    """
    Extract and normalize category name from QA item.
    
    Handles both numeric categories (1-4) and string categories with various formats.
    Supports multiple field names: "cat", "category", "type".
    
    Category mapping:
    - 1 or "multi-hop" -> "Multi-Hop"
    - 2 or "temporal" -> "Temporal"
    - 3 or "open domain" -> "Open Domain"
    - 4 or "single-hop" -> "Single-Hop"
    
    Args:
        item: QA item dictionary containing category information
        
    Returns:
        Standardized category name or "unknown" if not found
        
    Examples:
        >>> get_category_name({"category": 1})
        'Multi-Hop'
        >>> get_category_name({"cat": "temporal"})
        'Temporal'
        >>> get_category_name({"type": "Single-Hop"})
        'Single-Hop'
    """
    # Numeric category mapping
    CATEGORY_MAP = {
        1: "Multi-Hop",
        2: "Temporal",
        3: "Open Domain",
        4: "Single-Hop",
    }
    
    # String category aliases (case-insensitive)
    TYPE_ALIASES = {
        "single-hop": "Single-Hop",
        "singlehop": "Single-Hop",
        "single hop": "Single-Hop",
        "multi-hop": "Multi-Hop",
        "multihop": "Multi-Hop",
        "multi hop": "Multi-Hop",
        "open domain": "Open Domain",
        "opendomain": "Open Domain",
        "temporal": "Temporal",
    }
    
    # Try "cat" field first (string category)
    cat = item.get("cat")
    if isinstance(cat, str) and cat.strip():
        name = cat.strip()
        lower = name.lower()
        return TYPE_ALIASES.get(lower, name)
    
    # Try "category" field (can be int or string)
    cat_num = item.get("category")
    if isinstance(cat_num, int):
        return CATEGORY_MAP.get(cat_num, "unknown")
    elif isinstance(cat_num, str) and cat_num.strip():
        lower = cat_num.strip().lower()
        return TYPE_ALIASES.get(lower, cat_num.strip())
    
    # Try "type" field as fallback
    cat_type = item.get("type")
    if isinstance(cat_type, str) and cat_type.strip():
        lower = cat_type.strip().lower()
        return TYPE_ALIASES.get(lower, cat_type.strip())
    
    return "unknown"
