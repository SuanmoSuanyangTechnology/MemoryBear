"""
LiteLLM Configuration for Enhanced Retry Logic and Usage Tracking with Native QPS Monitoring
"""

import litellm
from typing import Dict, Any, List
import json
from datetime import datetime, timedelta
import os
import time
from collections import defaultdict
import threading
from queue import Queue

class LiteLLMConfig:
    """Configuration class for LiteLLM with enhanced retry and tracking capabilities"""

    def __init__(self):
        self.usage_data = []
        self.error_data = []
        self.module_stats = defaultdict(lambda: {
            'requests': 0,
            'tokens_in': 0,
            'tokens_out': 0,
            'cost': 0.0,
            'errors': 0,
            'start_time': None,
            'last_request_time': None,
            'request_timestamps': [],  # Store precise timestamps
            'current_qps': 0.0,
            'max_qps': 0.0,
            'qps_history': []  # Store QPS measurements over time
        })
        self.start_time = datetime.now()
        self.global_request_timestamps = []
        self.global_max_qps = 0.0

        # Rate limiting for AWS Bedrock (conservative limits)
        self.rate_limits = {
            'bedrock': {
                'requests_per_minute': 2,  # AWS Bedrock default is very low
                'requests_per_second': 0.033,  # 2/60 = 0.033 RPS
                'last_request_time': 0,
                'request_queue': Queue(),
                'lock': threading.Lock()
            }
        }
        self.rate_limiting_enabled = True

    def setup_enhanced_config(self, max_retries: int = 3):
        """Configure LiteLLM with retry logic and instant QPS tracking"""

        litellm.num_retries = max_retries
        litellm.request_timeout = 300

        litellm.retry_policy = {
            "RateLimitError": {
                "max_retries": 5,
                "exponential_backoff": True,
                "initial_delay": 1,
                "max_delay": 60,
                "jitter": True
            },
            "APIConnectionError": {
                "max_retries": 3,
                "exponential_backoff": True,
                "initial_delay": 2,
                "max_delay": 30,
                "jitter": True
            },
            "InternalServerError": {
                "max_retries": 2,
                "exponential_backoff": True,
                "initial_delay": 5,
                "max_delay": 60,
                "jitter": True
            },
            "BadRequestError": {
                "max_retries": 1,
                "exponential_backoff": False,
                "initial_delay": 1,
                "max_delay": 5
            }
        }

        litellm.success_callback = [self._success_callback]
        litellm.failure_callback = [self._failure_callback]
        litellm.completion_cost_tracking = True
        litellm.set_verbose = False
        litellm.modify_params = True

        print("✅ LiteLLM configured with instant QPS tracking and rate limiting")

    def _success_callback(self, kwargs, completion_response, start_time, end_time):
        """Callback for successful requests with module-specific QPS tracking"""
        try:
            # Extract usage information
            usage = completion_response.get('usage', {})
            model = kwargs.get('model', 'unknown')

            # Extract module information from metadata or model name
            module = self._extract_module_name(kwargs, model)

            # Calculate cost
            cost = 0.0
            try:
                cost = litellm.completion_cost(completion_response)
            except:
                pass

            # Calculate duration
            duration_seconds = (end_time - start_time).total_seconds() if hasattr(end_time - start_time, 'total_seconds') else float(end_time - start_time)

            # Record usage data
            usage_record = {
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "module": module,
                "input_tokens": usage.get('prompt_tokens', 0),
                "output_tokens": usage.get('completion_tokens', 0),
                "total_tokens": usage.get('total_tokens', 0),
                "cost": cost,
                "duration_seconds": duration_seconds,
                "status": "success"
            }

            self.usage_data.append(usage_record)

            # Update module-specific stats for QPS tracking
            self._update_module_stats(module, usage_record, success=True)

            # Print real-time feedback
            print(f"✓ {model}: {usage_record['input_tokens']}→{usage_record['output_tokens']} tokens, ${cost:.4f}, {usage_record['duration_seconds']:.2f}s")

        except Exception as e:
            print(f"Warning: Success callback failed: {e}")

    def _failure_callback(self, kwargs, completion_response, start_time, end_time):
        """Callback for failed requests with module-specific error tracking"""
        try:
            model = kwargs.get('model', 'unknown')
            module = self._extract_module_name(kwargs, model)

            duration_seconds = (end_time - start_time).total_seconds() if hasattr(end_time - start_time, 'total_seconds') else float(end_time - start_time)

            # Handle different error response formats
            error_message = "Unknown error"
            error_type = "UnknownError"

            # According to LiteLLM docs, completion_response contains the exception for failures
            if completion_response is not None:
                error_message = str(completion_response)
                error_type = type(completion_response).__name__

            # Also check kwargs for exception (LiteLLM passes exception in kwargs for failure events)
            elif 'exception' in kwargs:
                exception = kwargs['exception']
                error_message = str(exception)
                error_type = type(exception).__name__

            # Check for other error formats in kwargs
            elif 'error' in kwargs:
                error = kwargs['error']
                error_message = str(error)
                error_type = type(error).__name__

            # Check log_event_type to confirm this is a failure event
            log_event_type = kwargs.get('log_event_type', '')
            if log_event_type == 'failed_api_call' and 'exception' in kwargs:
                exception = kwargs['exception']
                error_message = str(exception)
                error_type = type(exception).__name__

            error_record = {
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "module": module,
                "error": error_message,
                "error_type": error_type,
                "duration_seconds": duration_seconds,
                "status": "failed"
            }

            self.error_data.append(error_record)

            # Update module-specific stats for error tracking
            self._update_module_stats(module, error_record, success=False)

            # Print error feedback
            print(f"✗ {model}: {error_type} - {error_message[:100]}")

        except Exception as e:
            print(f"Warning: Failure callback failed: {e}")
            # Debug: print the actual parameters to understand the structure
            print(f"Debug - kwargs keys: {list(kwargs.keys()) if kwargs else 'None'}")
            print(f"Debug - completion_response type: {type(completion_response)}")
            print(f"Debug - completion_response: {completion_response}")

    def _should_rate_limit(self, model: str) -> bool:
        """Check if the model should be rate limited"""
        if not self.rate_limiting_enabled:
            return False
        return model.startswith('bedrock/') or 'bedrock' in model.lower()

    def _enforce_rate_limit(self, model: str):
        """Enforce rate limiting for AWS Bedrock models"""
        if not self._should_rate_limit(model):
            return

        provider = 'bedrock'
        if provider not in self.rate_limits:
            return

        rate_config = self.rate_limits[provider]

        with rate_config['lock']:
            current_time = time.time()
            time_since_last = current_time - rate_config['last_request_time']
            min_interval = 1.0 / rate_config['requests_per_second']

            if time_since_last < min_interval:
                sleep_time = min_interval - time_since_last
                print(f"⏳ Rate limiting: sleeping {sleep_time:.2f}s for {model}")
                time.sleep(sleep_time)

            rate_config['last_request_time'] = time.time()

    def _extract_module_name(self, kwargs: Dict[str, Any], model: str) -> str:
        """Extract module name from request context"""
        # Try to get module from metadata
        metadata = kwargs.get('metadata', {})
        if 'module' in metadata:
            return metadata['module']

        # Try to infer from model name or other context
        if 'claude' in model.lower():
            return 'bedrock_client'
        elif 'gpt' in model.lower() or 'openai' in model.lower():
            return 'openai_client'
        elif 'embed' in model.lower():
            return 'embedder'
        else:
            return 'unknown'

    def _update_module_stats(self, module: str, record: Dict[str, Any], success: bool):
        """Update module-specific statistics with instant QPS tracking"""
        current_timestamp = time.time()
        current_time = datetime.now()

        # Initialize module stats if first request
        if self.module_stats[module]['start_time'] is None:
            self.module_stats[module]['start_time'] = current_time

        # Update counters
        self.module_stats[module]['requests'] += 1
        self.module_stats[module]['last_request_time'] = current_time
        self.module_stats[module]['request_timestamps'].append(current_timestamp)
        self.global_request_timestamps.append(current_timestamp)

        # Calculate instant QPS for this module
        self._calculate_instant_qps(module, current_timestamp)

        # Calculate global instant QPS
        self._calculate_global_instant_qps(current_timestamp)

        if success:
            self.module_stats[module]['tokens_in'] += record.get('input_tokens', 0)
            self.module_stats[module]['tokens_out'] += record.get('output_tokens', 0)
            self.module_stats[module]['cost'] += record.get('cost', 0.0)
        else:
            self.module_stats[module]['errors'] += 1

    def _calculate_instant_qps(self, module: str, current_timestamp: float):
        """Calculate instant QPS for a specific module using sliding window"""
        # Keep only timestamps from last 1 second for instant QPS
        cutoff_time = current_timestamp - 1.0
        timestamps = self.module_stats[module]['request_timestamps']

        # Remove old timestamps
        self.module_stats[module]['request_timestamps'] = [
            ts for ts in timestamps if ts >= cutoff_time
        ]

        # Calculate current QPS (requests in last second)
        current_qps = len(self.module_stats[module]['request_timestamps'])
        self.module_stats[module]['current_qps'] = current_qps

        # Update max QPS if current is higher
        if current_qps > self.module_stats[module]['max_qps']:
            self.module_stats[module]['max_qps'] = current_qps

        # Store QPS history (keep last 60 measurements)
        self.module_stats[module]['qps_history'].append(current_qps)
        if len(self.module_stats[module]['qps_history']) > 60:
            self.module_stats[module]['qps_history'].pop(0)

    def _calculate_global_instant_qps(self, current_timestamp: float):
        """Calculate global instant QPS across all modules"""
        # Keep only timestamps from last 1 second
        cutoff_time = current_timestamp - 1.0
        self.global_request_timestamps = [
            ts for ts in self.global_request_timestamps if ts >= cutoff_time
        ]

        # Calculate current global QPS
        current_global_qps = len(self.global_request_timestamps)

        # Update max global QPS
        if current_global_qps > self.global_max_qps:
            self.global_max_qps = current_global_qps

    def get_instant_qps(self, module: str = None) -> Dict[str, Any]:
        """Get instant QPS data for modules"""
        if module:
            if module in self.module_stats:
                return {
                    'module': module,
                    'current_qps': self.module_stats[module]['current_qps'],
                    'max_qps': self.module_stats[module]['max_qps'],
                    'avg_qps_last_minute': sum(self.module_stats[module]['qps_history'][-60:]) / min(60, len(self.module_stats[module]['qps_history'])) if self.module_stats[module]['qps_history'] else 0
                }
            else:
                return {'module': module, 'current_qps': 0, 'max_qps': 0, 'avg_qps_last_minute': 0}
        else:
            # Return data for all modules plus global
            result = {
                'global': {
                    'current_qps': len([ts for ts in self.global_request_timestamps if ts >= time.time() - 1.0]),
                    'max_qps': self.global_max_qps
                },
                'modules': {}
            }

            for mod in self.module_stats:
                result['modules'][mod] = {
                    'current_qps': self.module_stats[mod]['current_qps'],
                    'max_qps': self.module_stats[mod]['max_qps'],
                    'avg_qps_last_minute': sum(self.module_stats[mod]['qps_history'][-60:]) / min(60, len(self.module_stats[mod]['qps_history'])) if self.module_stats[mod]['qps_history'] else 0
                }

            return result

    def get_usage_summary(self) -> Dict[str, Any]:
        """Get essential usage statistics"""
        if not self.usage_data:
            return {
                "total_requests": 0,
                "total_cost": 0.0,
                "error_rate": 0.0,
                "message": "No usage data available"
            }

        total_requests = len(self.usage_data)
        total_errors = len(self.error_data)
        total_cost = sum(record['cost'] for record in self.usage_data)
        total_input_tokens = sum(record['input_tokens'] for record in self.usage_data)
        total_output_tokens = sum(record['output_tokens'] for record in self.usage_data)

        # Calculate session duration
        duration_minutes = (datetime.now() - self.start_time).total_seconds() / 60

        # Build module statistics
        module_stats = {}
        for module, stats in self.module_stats.items():
            if stats['requests'] > 0:
                module_stats[module] = {
                    "requests": stats['requests'],
                    "errors": stats['errors'],
                    "success_rate": ((stats['requests'] - stats['errors']) / stats['requests'] * 100) if stats['requests'] > 0 else 0,
                    "tokens_in": stats['tokens_in'],
                    "tokens_out": stats['tokens_out'],
                    "cost": stats['cost'],
                    "current_qps": stats['current_qps'],
                    "max_qps": stats['max_qps']
                }

        return {
            "session_duration_minutes": duration_minutes,
            "total_requests": total_requests,
            "total_errors": total_errors,
            "error_rate": (total_errors / total_requests * 100) if total_requests > 0 else 0,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost": total_cost,
            "module_stats": module_stats,
            "global_max_qps": self.global_max_qps
        }

    def print_usage_summary(self):
        """Print essential usage summary"""
        stats = self.get_usage_summary()

        if stats.get('message'):
            print(f"📊 {stats['message']}")
            return

        print("\n📊 USAGE SUMMARY")
        print(f"{'='*50}")
        print(f"⏱️  Duration: {stats['session_duration_minutes']:.1f} min")
        print(f"📈 Requests: {stats['total_requests']}")
        print(f"❌ Errors: {stats['total_errors']}")
        print(f"💰 Cost: ${stats['total_cost']:.4f}")
        print(f"🏆 Global Max QPS: {stats['global_max_qps']}")

        # Module statistics
        if stats.get('module_stats'):
            print("\n📦 MODULES:")
            for module, mod_stats in stats['module_stats'].items():
                print(f"  {module}: {mod_stats['requests']} req, Max QPS: {mod_stats['max_qps']}, Current: {mod_stats['current_qps']}")

        print(f"{'='*50}")

    def save_usage_data(self, filename: str = "litellm_usage.json"):
        """Save usage data to JSON file"""
        data = {
            "summary": self.get_usage_summary(),
            "detailed_usage": self.usage_data,
            "errors": self.error_data,
            "export_timestamp": datetime.now().isoformat()
        }

        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"📁 Usage data saved to {filename}")

    def reset_tracking(self):
        """Reset all tracking data"""
        self.usage_data = []
        self.error_data = []
        self.module_stats = defaultdict(lambda: {
            'requests': 0,
            'tokens_in': 0,
            'tokens_out': 0,
            'cost': 0.0,
            'errors': 0,
            'start_time': None,
            'last_request_time': None,
            'request_timestamps': [],
            'current_qps': 0.0,
            'max_qps': 0.0,
            'qps_history': []
        })
        self.global_request_timestamps = []
        self.global_max_qps = 0.0
        self.start_time = datetime.now()
        print("🔄 All tracking data reset")

# Global instance for easy access
litellm_config = LiteLLMConfig()

def setup_litellm_enhanced(max_retries: int = 3):
    """
    Quick setup function for LiteLLM enhanced configuration

    Args:
        max_retries: Maximum number of retries for failed requests
    """
    litellm_config.setup_enhanced_config(max_retries)
    return litellm_config

def get_usage_summary():
    """Get current usage summary"""
    return litellm_config.get_usage_summary()

def print_usage_summary():
    """Print current usage summary"""
    litellm_config.print_usage_summary()

def save_usage_data(filename: str = "litellm_usage.json"):
    """Save usage data to file"""
    litellm_config.save_usage_data(filename)

def get_instant_qps(module: str = None) -> Dict[str, Any]:
    """Get instant QPS data for modules"""
    return litellm_config.get_instant_qps(module)

def print_instant_qps(module: str = None):
    """Print instant QPS information"""
    qps_data = get_instant_qps(module)

    print("\n⚡ INSTANT QPS MONITOR")
    print(f"{'='*60}")

    if module:
        print(f"Module: {qps_data['module']}")
        print(f"  Current QPS: {qps_data['current_qps']}")
        print(f"  Max QPS:     {qps_data['max_qps']}")
        print(f"  Avg (1min):  {qps_data['avg_qps_last_minute']:.2f}")
    else:
        # Global stats
        global_data = qps_data.get('global', {})
        print("🌍 GLOBAL:")
        print(f"  Current QPS: {global_data.get('current_qps', 0)}")
        print(f"  Max QPS:     {global_data.get('max_qps', 0)}")

        # Module stats
        modules = qps_data.get('modules', {})
        if modules:
            print("\n📦 MODULES:")
            for mod, data in modules.items():
                print(f"  {mod}:")
                print(f"    Current: {data['current_qps']} QPS")
                print(f"    Max:     {data['max_qps']} QPS")
                print(f"    Avg:     {data['avg_qps_last_minute']:.2f} QPS")

    print(f"{'='*60}")

def reset_tracking():
    """Reset all tracking data"""
    litellm_config.reset_tracking()

def get_module_stats() -> Dict[str, Dict[str, Any]]:
    """Get detailed module statistics"""
    summary = get_usage_summary()
    return summary.get('module_stats', {})
