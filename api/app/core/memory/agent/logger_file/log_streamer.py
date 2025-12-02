"""
Log Streamer Module

Manages streaming of log file content with file watching and real-time transmission.
"""
import os
import re
import time
import asyncio
from typing import AsyncGenerator, Optional
from pathlib import Path

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class LogStreamer:
    """Manages log file streaming with file watching and content transmission"""
    
    def __init__(self, log_path: str, keepalive_interval: int = 300):
        """
        Initialize LogStreamer
        
        Args:
            log_path: Path to the log file to stream
            keepalive_interval: Interval in seconds for sending keepalive messages (default: 300)
        """
        self.log_path = log_path
        self.keepalive_interval = keepalive_interval
        self.last_position = 0
        
        # Pattern to match and remove timestamp and log level prefix
        # Matches: "YYYY-MM-DD HH:MM:SS,mmm - [LEVEL] - module_name - "
        # This pattern is comprehensive to handle various log formats
        self.pattern = re.compile(
            r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - \[(?:INFO|DEBUG|WARNING|ERROR|CRITICAL)\] - \S+ - '
        )
        
        logger.info(f"LogStreamer initialized for {log_path}")
    
    @staticmethod
    def clean_log_line(line: str) -> str:
        """
        Static method to clean log entry by removing timestamp and log level prefix.
        This is the canonical log cleaning method used by both file mode and transmission mode.
        
        Args:
            line: Raw log line
            
        Returns:
            Cleaned log line without timestamp and log level prefix
        """
        # Pattern to match and remove timestamp and log level prefix
        # Matches: "YYYY-MM-DD HH:MM:SS,mmm - [LEVEL] - module_name - "
        pattern = re.compile(
            r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - \[(?:INFO|DEBUG|WARNING|ERROR|CRITICAL)\] - \S+ - '
        )
        cleaned = re.sub(pattern, '', line)
        return cleaned
    
    def clean_log_entry(self, line: str) -> str:
        """
        Clean log entry by removing timestamp and log level prefix.
        This instance method delegates to the static method for consistency.
        
        Args:
            line: Raw log line
            
        Returns:
            Cleaned log line without timestamp and log level prefix
        """
        return LogStreamer.clean_log_line(line)
    
    async def send_keepalive(self) -> dict:
        """
        Generate keepalive message
        
        Returns:
            Keepalive message dict with timestamp
        """
        return {
            "event": "keepalive",
            "data": {
                "timestamp": int(time.time())
            }
        }
    
    async def read_existing_and_stream(self) -> AsyncGenerator[dict, None]:
        """
        Read existing log content first, then watch for new content
        
        This method reads all existing content in the file first,
        then continues to watch for new content as it's written.
        
        Yields:
            Dict messages with event type and data:
            - log events: {"event": "log", "data": {"content": "...", "timestamp": ...}}
            - keepalive events: {"event": "keepalive", "data": {"timestamp": ...}}
            - error events: {"event": "error", "data": {"code": ..., "message": "...", "error": "..."}}
            - done events: {"event": "done", "data": {"message": "..."}}
        """
        logger.info(f"Starting log stream (read existing) for {self.log_path}")
        
        # Check if file exists
        if not os.path.exists(self.log_path):
            logger.error(f"Log file not found: {self.log_path}")
            yield {
                "event": "error",
                "data": {
                    "code": 4006,
                    "message": "日志文件不存在",
                    "error": f"File not found: {self.log_path}"
                }
            }
            return
        
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                # First, read all existing content
                for line in f:
                    if line.strip():  # Skip empty lines
                        cleaned_line = self.clean_log_entry(line)
                        yield {
                            "event": "log",
                            "data": {
                                "content": cleaned_line.rstrip('\n'),
                                "timestamp": int(time.time())
                            }
                        }
                
                # Now watch for new content
                self.last_position = f.tell()
                last_keepalive = time.time()
                
                while True:
                    line = f.readline()
                    if line:
                        cleaned_line = self.clean_log_entry(line)
                        yield {
                            "event": "log",
                            "data": {
                                "content": cleaned_line.rstrip('\n'),
                                "timestamp": int(time.time())
                            }
                        }
                        last_keepalive = time.time()
                    else:
                        # No new content, check if we need to send keepalive
                        current_time = time.time()
                        if current_time - last_keepalive >= self.keepalive_interval:
                            keepalive_msg = await self.send_keepalive()
                            yield keepalive_msg
                            last_keepalive = current_time
                        
                        # Sleep briefly before checking again
                        await asyncio.sleep(0.1)
                        
        except FileNotFoundError:
            logger.error(f"Log file disappeared during streaming: {self.log_path}")
            yield {
                "event": "error",
                "data": {
                    "code": 4006,
                    "message": "日志文件在流式传输期间变得不可用",
                    "error": "File not found during streaming"
                }
            }
        except Exception as e:
            logger.error(f"Error during log streaming: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {
                    "code": 8001,
                    "message": "流式传输期间发生错误",
                    "error": str(e)
                }
            }
        finally:
            logger.info(f"Log stream ended for {self.log_path}")
            yield {
                "event": "done",
                "data": {
                    "message": "流式传输完成"
                }
            }
    
    async def watch_and_stream(self) -> AsyncGenerator[dict, None]:
        """
        Watch log file and stream only new content as it's written
        
        This method starts from the end of the file and only streams
        new content that is written after the stream starts.
        
        Yields:
            Dict messages with event type and data:
            - log events: {"event": "log", "data": {"content": "...", "timestamp": ...}}
            - keepalive events: {"event": "keepalive", "data": {"timestamp": ...}}
            - error events: {"event": "error", "data": {"code": ..., "message": "...", "error": "..."}}
            - done events: {"event": "done", "data": {"message": "..."}}
        """
        logger.info(f"Starting log stream (new content only) for {self.log_path}")
        
        # Check if file exists
        if not os.path.exists(self.log_path):
            logger.error(f"Log file not found: {self.log_path}")
            yield {
                "event": "error",
                "data": {
                    "code": 4006,
                    "message": "日志文件不存在",
                    "error": f"File not found: {self.log_path}"
                }
            }
            return
        
        try:
            # Open file and seek to end to start streaming new content
            with open(self.log_path, 'r', encoding='utf-8') as f:
                # Move to end of file
                f.seek(0, os.SEEK_END)
                self.last_position = f.tell()
                
                last_keepalive = time.time()
                
                while True:
                    # Check if file has new content
                    current_position = f.tell()
                    
                    # Read new lines if available
                    line = f.readline()
                    if line:
                        # Clean the log entry
                        cleaned_line = self.clean_log_entry(line)
                        
                        # Yield log event
                        yield {
                            "event": "log",
                            "data": {
                                "content": cleaned_line.rstrip('\n'),
                                "timestamp": int(time.time())
                            }
                        }
                        
                        # Update last keepalive time since we sent data
                        last_keepalive = time.time()
                    else:
                        # No new content, check if we need to send keepalive
                        current_time = time.time()
                        if current_time - last_keepalive >= self.keepalive_interval:
                            keepalive_msg = await self.send_keepalive()
                            yield keepalive_msg
                            last_keepalive = current_time
                        
                        # Sleep briefly before checking again
                        await asyncio.sleep(0.1)
                        
        except FileNotFoundError:
            logger.error(f"Log file disappeared during streaming: {self.log_path}")
            yield {
                "event": "error",
                "data": {
                    "code": 4006,
                    "message": "日志文件在流式传输期间变得不可用",
                    "error": "File not found during streaming"
                }
            }
        except Exception as e:
            logger.error(f"Error during log streaming: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {
                    "code": 8001,
                    "message": "流式传输期间发生错误",
                    "error": str(e)
                }
            }
        finally:
            logger.info(f"Log stream ended for {self.log_path}")
            yield {
                "event": "done",
                "data": {
                    "message": "流式传输完成"
                }
            }
