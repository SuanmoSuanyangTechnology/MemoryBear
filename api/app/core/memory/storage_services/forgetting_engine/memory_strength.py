"""
Memory Strength Calculator based on ACT-R Theory

This module implements the Base-Level Activation equation from ACT-R
(Adaptive Control of Thought-Rational) cognitive architecture.

Formula: B(i) = ln(Σ(t_k^(-d)))

Where:
- B(i): Base-level activation score
- t_k: Time since the k-th access
- d: Decay parameter (typically 0.5)
- n: Number of accesses

Reference: Anderson, J. R. (2007). How Can the Human Mind Occur in the Physical Universe?
"""

import math
from typing import List, Optional
from datetime import datetime, timedelta


class MemoryStrengthCalculator:
    """
    Calculate memory strength using ACT-R base-level activation formula.
    """

    def __init__(self, decay_parameter: float = 0.5, time_unit: str = "seconds"):
        """
        Initialize the memory strength calculator.

        Args:
            decay_parameter: The decay rate (d). Typically 0.5 for human memory.
                           Higher values = faster forgetting.
            time_unit: Unit for time calculations. Options: 'seconds', 'minutes', 
                      'hours', 'days'. Default is 'seconds'.
        """
        self.decay_parameter = decay_parameter
        self.time_unit = time_unit
        self._time_multipliers = {
            "seconds": 1,
            "minutes": 60,
            "hours": 3600,
            "days": 86400,
        }

    def calculate_activation(
        self, access_times: List[datetime], current_time: Optional[datetime] = None
    ) -> float:
        """
        Calculate the base-level activation B(i) for a memory item.

        Args:
            access_times: List of datetime objects representing when the memory
                         was accessed (most recent first or in any order).
            current_time: The current time for calculation. If None, uses datetime.now().

        Returns:
            float: The base-level activation score B(i).
                  Higher values indicate stronger, more retrievable memories.

        Raises:
            ValueError: If access_times is empty or contains invalid data.
        """
        if not access_times:
            raise ValueError("access_times cannot be empty")

        if current_time is None:
            current_time = datetime.now()

        # Calculate time differences in specified units
        time_diffs = []
        for access_time in access_times:
            diff_seconds = (current_time - access_time).total_seconds()
            if diff_seconds < 0:
                raise ValueError(f"Access time {access_time} is in the future")

            # Convert to specified time unit
            diff = diff_seconds / self._time_multipliers[self.time_unit]

            # Avoid division by zero for very recent accesses
            # Use a small epsilon (0.01 time units)
            diff = max(diff, 0.01)
            time_diffs.append(diff)

        # Calculate B(i) = ln(Σ(t_k^(-d)))
        sum_power_law = sum(t ** (-self.decay_parameter) for t in time_diffs)
        activation = math.log(sum_power_law)

        return activation

    def calculate_activation_from_intervals(
        self, time_intervals: List[float]
    ) -> float:
        """
        Calculate activation directly from time intervals (in the configured time unit).

        Args:
            time_intervals: List of time intervals since each access.
                          E.g., [1.0, 3.5, 7.2] means accessed 1, 3.5, and 7.2 time units ago.

        Returns:
            float: The base-level activation score B(i).
        """
        if not time_intervals:
            raise ValueError("time_intervals cannot be empty")

        # Ensure no zero or negative intervals
        safe_intervals = [max(t, 0.01) for t in time_intervals]

        sum_power_law = sum(t ** (-self.decay_parameter) for t in safe_intervals)
        activation = math.log(sum_power_law)

        return activation

    def calculate_memory_strength(self, activation: float) -> float:
        """
        Convert activation score to memory strength S(i) = e^(B(i)).

        This converts the log-space activation to linear space,
        suitable for use in the Ebbinghaus forgetting curve.

        Args:
            activation: The base-level activation B(i).

        Returns:
            float: Memory strength S(i) in linear space.
        """
        return math.exp(activation)

    def calculate_retention_probability(
        self,
        activation: float,
        time_since_last_access: float,
        decay_rate: float = 0.01,
        offset: float = 0.1,
    ) -> float:
        """
        Calculate retention probability using the unified Ebbinghaus-ACT-R formula.

        Formula: R(i) = offset + (1-offset) * exp(-λ * t / Σ(t_k^(-d)))

        Args:
            activation: The base-level activation B(i).
            time_since_last_access: Time since last access (in configured time units).
            decay_rate: Lambda (λ) parameter controlling forgetting speed.
            offset: Baseline retention rate (minimum memory strength).

        Returns:
            float: Retention probability between 0 and 1.
        """
        memory_strength = self.calculate_memory_strength(activation)

        # Unified formula: R(i) = offset + (1-offset) * exp(-λ * t / S(i))
        retention = offset + (1 - offset) * math.exp(
            -decay_rate * time_since_last_access / memory_strength
        )

        return retention

    def should_retain(
        self,
        access_times: List[datetime],
        threshold: float = 0.5,
        current_time: Optional[datetime] = None,
        decay_rate: float = 0.01,
        offset: float = 0.1,
    ) -> tuple[bool, float, float]:
        """
        Determine if a memory should be retained based on its strength.

        Args:
            access_times: List of access timestamps.
            threshold: Retention probability threshold (default 0.5 = 50%).
            current_time: Current time for calculation.
            decay_rate: Lambda parameter for forgetting curve.
            offset: Baseline retention rate.

        Returns:
            tuple: (should_retain: bool, retention_probability: float, activation: float)
        """
        if current_time is None:
            current_time = datetime.now()

        activation = self.calculate_activation(access_times, current_time)

        # Time since last access
        last_access = max(access_times)
        time_since_last = (current_time - last_access).total_seconds() / self._time_multipliers[self.time_unit]
        time_since_last = max(time_since_last, 0.01)

        retention_prob = self.calculate_retention_probability(
            activation, time_since_last, decay_rate, offset
        )

        return (retention_prob >= threshold, retention_prob, activation)


# Convenience functions for quick calculations
def calculate_activation(
    access_times: List[datetime],
    decay_parameter: float = 0.5,
    current_time: Optional[datetime] = None,
) -> float:
    """
    Quick function to calculate activation without creating a calculator instance.

    Args:
        access_times: List of access timestamps.
        decay_parameter: Decay rate (default 0.5).
        current_time: Current time (default now).

    Returns:
        float: Base-level activation B(i).
    """
    calculator = MemoryStrengthCalculator(decay_parameter=decay_parameter)
    return calculator.calculate_activation(access_times, current_time)


def calculate_retention(
    access_times: List[datetime],
    decay_parameter: float = 0.5,
    decay_rate: float = 0.01,
    offset: float = 0.1,
    current_time: Optional[datetime] = None,
) -> float:
    """
    Quick function to calculate retention probability.

    Args:
        access_times: List of access timestamps.
        decay_parameter: ACT-R decay parameter (default 0.5).
        decay_rate: Ebbinghaus decay rate lambda (default 0.01).
        offset: Baseline retention (default 0.1).
        current_time: Current time (default now).

    Returns:
        float: Retention probability between 0 and 1.
    """
    calculator = MemoryStrengthCalculator(decay_parameter=decay_parameter)
    activation = calculator.calculate_activation(access_times, current_time)

    if current_time is None:
        current_time = datetime.now()

    last_access = max(access_times)
    time_since_last = (current_time - last_access).total_seconds()

    return calculator.calculate_retention_probability(
        activation, time_since_last, decay_rate, offset
    )
