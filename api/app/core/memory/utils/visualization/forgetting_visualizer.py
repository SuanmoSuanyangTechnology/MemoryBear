"""
Memory Visualization Utilities

This module provides visualization functions for the modified Ebbinghaus forgetting curve
and utilities to export memory curves as numpy arrays.
"""

import numpy as np
import matplotlib.pyplot as plt
from typing import Optional, Tuple, List, Dict, Any
import math


def export_memory_curve_numpy(forgetting_engine,
                             time_range: Tuple[float, float] = (0, 10),
                             memory_strength: float = 1.0,
                             num_points: int = 1000) -> Tuple[np.ndarray, np.ndarray]:
    """
    Export memory curve as numpy arrays for time and retention values.

    Args:
        forgetting_engine: Instance of ForgettingEngine
        time_range: Tuple of (start_time, end_time)
        memory_strength: Memory strength value to use
        num_points: Number of points to generate

    Returns:
        Tuple of (time_array, retention_array)
    """
    start_time, end_time = time_range
    time_array = np.linspace(start_time, end_time, num_points)
    retention_array = np.array([
        forgetting_engine.forgetting_curve(t, memory_strength)
        for t in time_array
    ])

    return time_array, retention_array


def export_memory_curves_multiple_strengths(forgetting_engine,
                                           time_range: Tuple[float, float] = (0, 10),
                                           memory_strengths: List[float] = None,
                                           num_points: int = 1000) -> Dict[str, np.ndarray]:
    """
    Export memory curves for multiple memory strengths as numpy arrays.

    Args:
        forgetting_engine: Instance of ForgettingEngine
        time_range: Tuple of (start_time, end_time)
        memory_strengths: List of memory strength values
        num_points: Number of points to generate

    Returns:
        Dictionary with 'time' and retention arrays for each strength
    """
    if memory_strengths is None:
        memory_strengths = [0.5, 1.0, 2.0, 5.0]

    start_time, end_time = time_range
    time_array = np.linspace(start_time, end_time, num_points)

    result = {'time': time_array}

    for strength in memory_strengths:
        retention_array = np.array([
            forgetting_engine.forgetting_curve(t, strength)
            for t in time_array
        ])
        result[f'strength_{strength}'] = retention_array

    return result


def export_parameter_sweep_numpy(base_engine,
                                parameter_name: str,
                                parameter_values: List[float],
                                time_range: Tuple[float, float] = (0, 10),
                                memory_strength: float = 1.0,
                                num_points: int = 1000) -> Dict[str, np.ndarray]:
    """
    Export memory curves for parameter sweep as numpy arrays.

    Args:
        base_engine: Base ForgettingEngine instance
        parameter_name: Name of parameter to sweep ('offset', 'lambda_time', 'lambda_mem')
        parameter_values: List of parameter values to test
        time_range: Tuple of (start_time, end_time)
        memory_strength: Memory strength value to use
        num_points: Number of points to generate

    Returns:
        Dictionary with 'time' and retention arrays for each parameter value
    """
    from app.core.memory.storage_services.forgetting_engine import ForgettingEngine
    from app.core.memory.models.variate_config import ForgettingEngineConfig

    start_time, end_time = time_range
    time_array = np.linspace(start_time, end_time, num_points)

    result = {'time': time_array}

    for param_value in parameter_values:
        # Create new engine with modified parameter
        if parameter_name == 'offset':
            config = ForgettingEngineConfig(offset=param_value, lambda_time=base_engine.lambda_time, lambda_mem=base_engine.lambda_mem)
        elif parameter_name == 'lambda_time':
            config = ForgettingEngineConfig(offset=base_engine.offset, lambda_time=param_value, lambda_mem=base_engine.lambda_mem)
        elif parameter_name == 'lambda_mem':
            config = ForgettingEngineConfig(offset=base_engine.offset, lambda_time=base_engine.lambda_time, lambda_mem=param_value)
        else:
            raise ValueError(f"Unknown parameter: {parameter_name}")

        engine = ForgettingEngine(config)

        retention_array = np.array([
            engine.forgetting_curve(t, memory_strength)
            for t in time_array
        ])
        result[f'{parameter_name}_{param_value}'] = retention_array

    return result


def visualize_forgetting_curve(forgetting_engine,
                              max_time: float = 10.0,
                              memory_strengths: Optional[List[float]] = None,
                              figsize: Tuple[int, int] = (12, 8)) -> None:
    """
    Visualize the modified Ebbinghaus forgetting curve.

    Args:
        forgetting_engine: Instance of ForgettingEngine
        max_time: Maximum time to plot
        memory_strengths: List of memory strength values to plot
        figsize: Figure size for the plot
    """
    if memory_strengths is None:
        memory_strengths = [0.5, 1.0, 2.0, 5.0]

    # Create time array
    t = np.linspace(0, max_time, 1000)

    # Create subplots
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=figsize)
    fig.suptitle('Modified Ebbinghaus Forgetting Curve Analysis', fontsize=16, fontweight='bold')

    # Plot 1: Different memory strengths
    ax1.set_title('Effect of Memory Strength (S)')
    for S in memory_strengths:
        retention = [forgetting_engine.forgetting_curve(time, S) for time in t]
        ax1.plot(t, retention, label=f'S = {S}', linewidth=2)
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Memory Retention')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, 1)

    # Plot 2: Different lambda_time values
    ax2.set_title('Effect of λ_time')
    lambda_times = [0.5, 1.0, 0.3]
    lambda_mem = [0.5,0.3,1.0]
    offset_mem = [0.1,0.05,0.2]
    for i in range(len(lambda_times)):
        lt = lambda_times[i]
        lm = lambda_mem[i]
        off = offset_mem[i]
        from app.core.memory.storage_services.forgetting_engine import ForgettingEngine
        from app.core.memory.models.variate_config import ForgettingEngineConfig
        config = ForgettingEngineConfig(offset=off, lambda_time=lt, lambda_mem=lm)
        temp_engine = ForgettingEngine(config)
        retention = [temp_engine.forgetting_curve(time, 1.0) for time in t]
        ax2.plot(t, retention, label=f'λ_time = {lt}', linewidth=2)
    ax2.set_xlabel('Time')
    ax2.set_ylabel('Memory Retention')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, 1)

    plt.tight_layout()
    plt.show()


def plot_3d_forgetting_surface(forgetting_engine,
                              max_time: float = 10.0,
                              max_strength: float = 5.0,
                              figsize: Tuple[int, int] = (12, 9)) -> None:
    """
    Create a 3D surface plot of the forgetting curve.

    Args:
        forgetting_engine: Instance of ForgettingEngine
        max_time: Maximum time to plot
        max_strength: Maximum memory strength to plot
        figsize: Figure size for the plot
    """
    # Create meshgrid
    t = np.linspace(0.1, max_time, 50)
    S = np.linspace(0.1, max_strength, 50)
    T, S_mesh = np.meshgrid(t, S)

    # Calculate retention for each point
    R = np.zeros_like(T)
    for i in range(T.shape[0]):
        for j in range(T.shape[1]):
            R[i, j] = forgetting_engine.forgetting_curve(T[i, j], S_mesh[i, j])

    # Create 3D plot
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection='3d')

    surface = ax.plot_surface(T, S_mesh, R, cmap='viridis', alpha=0.8)

    ax.set_xlabel('Time (t)')
    ax.set_ylabel('Memory Strength (S)')
    ax.set_zlabel('Memory Retention (R)')
    ax.set_title(f'3D Forgetting Curve Surface\n(offset={forgetting_engine.offset}, λ_time={forgetting_engine.lambda_time}, λ_mem={forgetting_engine.lambda_mem})')

    # Add colorbar
    fig.colorbar(surface, shrink=0.5, aspect=5)

    plt.show()


def create_comparison_visualization(forgetting_engine, figsize: Tuple[int, int] = (15, 10)) -> None:
    """
    Create a comparison visualization of different curve configurations.

    Args:
        forgetting_engine: Instance of ForgettingEngine
        figsize: Figure size for the plot
    """
    # Create figure with multiple subplots
    fig, axes = plt.subplots(2, 2, figsize=figsize)
    fig.suptitle('Modified Ebbinghaus Forgetting Curve - Parameter Comparison', fontsize=16, fontweight='bold')

    t = np.linspace(0, 10, 100)

    # Plot 1: Original vs Modified curve
    ax1 = axes[0, 0]
    ax1.set_title('Original vs Modified Ebbinghaus Curve')

    # Original Ebbinghaus: R = e^(-t/S)
    S = 2.0
    original = np.exp(-t / S)
    ax1.plot(t, original, 'r--', label='Original: R = e^(-t/S)', linewidth=2)

    # Modified with offset
    modified = [forgetting_engine.forgetting_curve(time, S) for time in t]
    ax1.plot(t, modified, 'b-', label='Modified: offset + (1-offset)*e^(-λ_time*t/λ_mem*S)', linewidth=2)

    ax1.set_xlabel('Time')
    ax1.set_ylabel('Memory Retention')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, 1)

    # Plot 2: Different offset values
    ax2 = axes[0, 1]
    ax2.set_title('Effect of Offset Parameter')

    for offset in [0.0, 0.1, 0.2, 0.3]:
        from forgetting.forgetting_engine import ForgettingEngine
        from app.core.memory.models.variate_config import ForgettingEngineConfig
        config = ForgettingEngineConfig(offset=offset, lambda_time=1.0, lambda_mem=1.0)
        engine = ForgettingEngine(config)
        retention = [engine.forgetting_curve(time, 1.0) for time in t]
        ax2.plot(t, retention, label=f'offset = {offset}', linewidth=2)

    ax2.set_xlabel('Time')
    ax2.set_ylabel('Memory Retention')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, 1)

    # Plot 3: Lambda time effect
    ax3 = axes[1, 0]
    ax3.set_title('Effect of λ_time (Time Sensitivity)')

    for lambda_time in [0.5, 1.0, 2.0, 3.0]:
        from forgetting.forgetting_engine import ForgettingEngine
        from app.core.memory.models.config_models import ForgettingEngineConfig
        config = ForgettingEngineConfig(offset=0.1, lambda_time=lambda_time, lambda_mem=1.0)
        engine = ForgettingEngine(config)
        retention = [engine.forgetting_curve(time, 1.0) for time in t]
        ax3.plot(t, retention, label=f'λ_time = {lambda_time}', linewidth=2)

    ax3.set_xlabel('Time')
    ax3.set_ylabel('Memory Retention')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.set_ylim(0, 1)

    # Plot 4: Memory strength effect
    ax4 = axes[1, 1]
    ax4.set_title('Effect of Memory Strength (S)')

    for strength in [0.5, 1.0, 2.0, 4.0]:
        retention = [forgetting_engine.forgetting_curve(time, strength) for time in t]
        ax4.plot(t, retention, label=f'S = {strength}', linewidth=2)

    ax4.set_xlabel('Time')
    ax4.set_ylabel('Memory Retention')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    ax4.set_ylim(0, 1)

    plt.tight_layout()
    plt.show()


def save_memory_curves_to_file(forgetting_engine,
                              filename: str,
                              time_range: Tuple[float, float] = (0, 10),
                              memory_strengths: List[float] = None,
                              num_points: int = 1000,
                              format: str = 'npz') -> None:
    """
    Save memory curves to file in various formats.

    Args:
        forgetting_engine: Instance of ForgettingEngine
        filename: Output filename (without extension)
        time_range: Tuple of (start_time, end_time)
        memory_strengths: List of memory strength values
        num_points: Number of points to generate
        format: Output format ('npz', 'csv', 'json')
    """
    if memory_strengths is None:
        memory_strengths = [0.5, 1.0, 2.0, 5.0]

    curves_data = export_memory_curves_multiple_strengths(
        forgetting_engine, time_range, memory_strengths, num_points
    )

    if format == 'npz':
        np.savez(f"{filename}.npz", **curves_data)
    elif format == 'csv':
        import pandas as pd
        df = pd.DataFrame(curves_data)
        df.to_csv(f"{filename}.csv", index=False)
    elif format == 'json':
        import json
        # Convert numpy arrays to lists for JSON serialization
        json_data = {k: v.tolist() if isinstance(v, np.ndarray) else v
                    for k, v in curves_data.items()}
        with open(f"{filename}.json", 'w') as f:
            json.dump(json_data, f, indent=2)
    else:
        raise ValueError(f"Unsupported format: {format}")


if __name__ == "__main__":
    # Example usage
    from app.core.memory.storage_services.forgetting_engine import ForgettingEngine

    print("Memory Visualization Utilities Demo")
    print("=" * 40)

    # Create engine
    from app.core.memory.models.variate_config import ForgettingEngineConfig
    config = ForgettingEngineConfig(offset=0.1, lambda_time=0.5, lambda_mem=0.5)
    engine = ForgettingEngine(config)

    # # Export single curve as numpy
    # time_arr, retention_arr = export_memory_curve_numpy(engine, (0, 10), 1.0, 100)
    # print(f"Exported single curve: {len(time_arr)} points")
    # print(f"Time range: {time_arr[0]:.2f} to {time_arr[-1]:.2f}")
    # print(f"Retention range: {retention_arr.min():.4f} to {retention_arr.max():.4f}")

    # # Export multiple curves
    # curves = export_memory_curves_multiple_strengths(engine, (0, 10), [0.5, 1.0, 2.0])
    # print(f"\nExported multiple curves: {list(curves.keys())}")

    # # Parameter sweep
    # param_sweep = export_parameter_sweep_numpy(engine, 'offset', [0.0, 0.1, 0.2, 0.3])
    # print(f"Parameter sweep results: {list(param_sweep.keys())}")

    # print("\nVisualization functions are ready to use!")
    visualize_forgetting_curve(engine)
    create_comparison_visualization(engine)






