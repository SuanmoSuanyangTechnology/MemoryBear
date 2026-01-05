"""
ACT-R Memory Activation Calculator

This module implements the unified Memory Activation model based on ACT-R
(Adaptive Control of Thought-Rational) cognitive architecture theory.

The calculator integrates BLA (Base-Level Activation) computation into the
Memory Activation formula, providing a single coherent model for memory strength
calculation that reflects both recency and frequency of access.

Formula: R(i) = offset + (1-offset) * exp(-λ*t / Σ(I·t_k^(-d)))

Where:
- R(i): Memory activation value (0 to 1)
- offset: Minimum retention rate (prevents complete forgetting)
- λ: Forgetting rate (lambda_time / lambda_mem)
- t: Time since last access
- I: Importance score (0 to 1)
- t_k: Time since k-th access
- d: Decay constant (typically 0.5)

Reference: Anderson, J. R. (2007). How Can the Human Mind Occur in the Physical Universe?
"""

import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


class ACTRCalculator:
    """
    Unified ACT-R Memory Activation Calculator.
    
    This calculator implements the Memory Activation model that combines
    recency and frequency effects into a single activation value computation.
    It replaces the separate BLA calculation with an integrated approach.
    
    Attributes:
        decay_constant: Decay parameter d (typically 0.5)
        forgetting_rate: Lambda parameter λ controlling forgetting speed
        offset: Minimum retention rate (baseline memory strength)
        max_history_length: Maximum number of access records to keep
    """

    def __init__(
        self,
        decay_constant: float = 0.5,
        forgetting_rate: float = 0.3,
        offset: float = 0.1,
        max_history_length: int = 100
    ):
        """
        Initialize the ACT-R calculator.
        
        Args:
            decay_constant: Decay parameter d (default 0.5)
            forgetting_rate: Forgetting rate λ (default 0.3)
            offset: Minimum retention rate (default 0.1)
            max_history_length: Maximum access history length (default 100)
        """
        self.decay_constant = decay_constant
        self.forgetting_rate = forgetting_rate
        self.offset = offset
        self.max_history_length = max_history_length

    def calculate_memory_activation(
        self,
        access_history: List[datetime],
        current_time: datetime,
        last_access_time: datetime,
        importance_score: float = 0.5
    ) -> float:
        """
        Calculate memory activation value using the unified Memory Activation formula.
        
        This method computes R(i) = offset + (1-offset) * exp(-λ*t / Σ(I·t_k^(-d)))
        
        The formula integrates:
        - Recency effect: Recent accesses contribute more (via t)
        - Frequency effect: Multiple accesses strengthen memory (via Σ)
        - Importance weighting: Important memories decay slower (via I)
        
        Args:
            access_history: List of access timestamps (ISO format or datetime objects)
            current_time: Current time for calculation
            last_access_time: Time of most recent access
            importance_score: Importance weight (0 to 1, default 0.5)
        
        Returns:
            float: Memory activation value between offset and 1.0
        
        Raises:
            ValueError: If access_history is empty or contains invalid data
        """
        if not access_history:
            raise ValueError("access_history cannot be empty")
        
        if not (0.0 <= importance_score <= 1.0):
            raise ValueError(f"importance_score must be between 0 and 1, got {importance_score}")
        
        # Calculate time since last access (in days)
        time_since_last = (current_time - last_access_time).total_seconds() / 86400.0
        time_since_last = max(time_since_last, 0.0001)  # Avoid division by zero
        
        # Calculate BLA component: Σ(I·t_k^(-d))
        bla_sum = 0.0
        for access_time in access_history:
            # Calculate time since this access (in days)
            time_diff = (current_time - access_time).total_seconds() / 86400.0
            time_diff = max(time_diff, 0.0001)  # Avoid division by zero
            
            # Add weighted power-law term: I * t_k^(-d)
            bla_sum += importance_score * (time_diff ** (-self.decay_constant))
        
        # Avoid division by zero in case of numerical issues
        if bla_sum <= 0:
            bla_sum = 0.0001
        
        # Calculate Memory Activation: R(i) = offset + (1-offset) * exp(-λ*t / BLA)
        exponent = -self.forgetting_rate * time_since_last / bla_sum
        
        # Clamp exponent to avoid numerical overflow/underflow
        exponent = max(min(exponent, 100), -100)
        
        activation = self.offset + (1 - self.offset) * math.exp(exponent)
        
        # Ensure activation is within valid range [offset, 1.0]
        return max(self.offset, min(1.0, activation))

    def trim_access_history(
        self,
        access_history: List[datetime],
        current_time: datetime
    ) -> List[datetime]:
        """
        Intelligently trim access history to prevent unbounded growth.
        
        Strategy:
        - Keep all records if under max_history_length
        - If over limit, keep most recent 50% and sample from older records
        - Preserves both recent accesses (high importance) and historical pattern
        
        Args:
            access_history: List of access timestamps (sorted or unsorted)
            current_time: Current time for calculation
        
        Returns:
            List[datetime]: Trimmed access history
        """
        if len(access_history) <= self.max_history_length:
            return access_history
        
        # Sort by time (most recent first)
        sorted_history = sorted(access_history, reverse=True)
        
        # Calculate split point (keep most recent 50%)
        keep_recent_count = self.max_history_length // 2
        
        # Keep most recent 50%
        recent_records = sorted_history[:keep_recent_count]
        
        # Sample from older records
        older_records = sorted_history[keep_recent_count:]
        sample_count = self.max_history_length - keep_recent_count
        
        if len(older_records) <= sample_count:
            # If older records fit, keep them all
            sampled_older = older_records
        else:
            # Sample evenly from older records
            step = len(older_records) / sample_count
            sampled_older = [
                older_records[int(i * step)]
                for i in range(sample_count)
            ]
        
        # Combine and return
        trimmed_history = recent_records + sampled_older
        return sorted(trimmed_history, reverse=True)

    def get_forgetting_curve(   # 预测激活值，决定复习；测试不同配置效果，选择合适的d
        self,
        initial_time: datetime,
        importance_score: float = 0.5,
        days: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Generate forgetting curve data for visualization.
        
        This method simulates how memory activation decays over time
        for a single initial access, useful for understanding and
        visualizing the forgetting behavior.
        
        Args:
            initial_time: Time of initial memory creation/access
            importance_score: Importance weight (0 to 1, default 0.5)
            days: Number of days to simulate (default 60)
        
        Returns:
            List of dictionaries with keys:
            - 'day': Day number (0 to days)
            - 'activation': Memory activation value
            - 'retention_rate': Same as activation (for compatibility)
        """
        curve_data = []
        access_history = [initial_time]
        
        for day in range(days + 1):
            current_time = initial_time + timedelta(days=day)
            
            try:
                activation = self.calculate_memory_activation(
                    access_history=access_history,
                    current_time=current_time,
                    last_access_time=initial_time,
                    importance_score=importance_score
                )
            except ValueError:
                # Handle edge cases
                activation = self.offset
            
            curve_data.append({
                'day': day,
                'activation': activation,
                'retention_rate': activation  # Alias for compatibility
            })
        
        return curve_data

    def calculate_forgetting_score(
        self,
        access_history: List[datetime],
        current_time: datetime,
        last_access_time: datetime,
        importance_score: float = 0.5
    ) -> float:
        """
        Calculate forgetting score (inverse of activation).
        
        Forgetting score = 1 - activation value
        Higher score means more likely to be forgotten.
        
        Args:
            access_history: List of access timestamps
            current_time: Current time for calculation
            last_access_time: Time of most recent access
            importance_score: Importance weight (0 to 1, default 0.5)
        
        Returns:
            float: Forgetting score between 0 and (1 - offset)
        """
        activation = self.calculate_memory_activation(
            access_history=access_history,
            current_time=current_time,
            last_access_time=last_access_time,
            importance_score=importance_score
        )
        return 1.0 - activation

    def should_forget(
        self,
        access_history: List[datetime],
        current_time: datetime,
        last_access_time: datetime,
        importance_score: float = 0.5,
        threshold: float = 0.3
    ) -> bool:
        """
        Determine if a memory should be forgotten based on activation threshold.
        
        Args:
            access_history: List of access timestamps
            current_time: Current time for calculation
            last_access_time: Time of most recent access
            importance_score: Importance weight (0 to 1, default 0.5)
            threshold: Activation threshold below which memory should be forgotten
        
        Returns:
            bool: True if activation < threshold (should forget), False otherwise
        """
        activation = self.calculate_memory_activation(
            access_history=access_history,
            current_time=current_time,
            last_access_time=last_access_time,
            importance_score=importance_score
        )
        return activation < threshold


# Convenience functions for quick calculations
def calculate_activation(
    access_history: List[datetime],
    current_time: datetime,
    last_access_time: datetime,
    importance_score: float = 0.5,
    decay_constant: float = 0.5,
    forgetting_rate: float = 0.3,
    offset: float = 0.1
) -> float:
    """
    Quick function to calculate activation without creating a calculator instance.
    
    Args:
        access_history: List of access timestamps
        current_time: Current time for calculation
        last_access_time: Time of most recent access
        importance_score: Importance weight (0 to 1, default 0.5)
        decay_constant: Decay parameter d (default 0.5)
        forgetting_rate: Forgetting rate λ (default 0.3)
        offset: Minimum retention rate (default 0.1)
    
    Returns:
        float: Memory activation value between offset and 1.0
    """
    calculator = ACTRCalculator(
        decay_constant=decay_constant,
        forgetting_rate=forgetting_rate,
        offset=offset
    )
    return calculator.calculate_memory_activation(
        access_history=access_history,
        current_time=current_time,
        last_access_time=last_access_time,
        importance_score=importance_score
    )


def generate_forgetting_curve(
    initial_time: datetime,
    importance_score: float = 0.5,
    days: int = 60,
    decay_constant: float = 0.5,
    forgetting_rate: float = 0.3,
    offset: float = 0.1
) -> List[Dict[str, Any]]:
    """
    Quick function to generate forgetting curve data.
    
    Args:
        initial_time: Time of initial memory creation/access
        importance_score: Importance weight (0 to 1, default 0.5)
        days: Number of days to simulate (default 60)
        decay_constant: Decay parameter d (default 0.5)
        forgetting_rate: Forgetting rate λ (default 0.3)
        offset: Minimum retention rate (default 0.1)
    
    Returns:
        List of dictionaries with forgetting curve data
    """
    calculator = ACTRCalculator(
        decay_constant=decay_constant,
        forgetting_rate=forgetting_rate,
        offset=offset
    )
    return calculator.get_forgetting_curve(
        initial_time=initial_time,
        importance_score=importance_score,
        days=days
    )
