"""
Cancellation Manager for Agent System

This module provides a thread-safe mechanism for tracking and managing
cancellation requests for streaming sessions.
"""

import threading
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass
import logging

from config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class CancellationEntry:
    """Represents a cancellation entry for a session."""
    session_id: str
    timestamp: datetime
    phase: Optional[str] = None  # 'thinking' | 'answer'
    content_length: int = 0


class CancellationManager:
    """
    Thread-safe manager for tracking cancelled sessions.
    
    Features:
    - Thread-safe operations using locks
    - Automatic cleanup of stale entries (configurable expiry)
    - Logging of cancellation events
    """
    
    def __init__(self, expiry_seconds: Optional[int] = None):
        """
        Initialize the CancellationManager.
        
        Args:
            expiry_seconds: Time in seconds after which entries are considered stale.
                           If None, uses value from settings.
        """
        self._cancelled_sessions: Dict[str, CancellationEntry] = {}
        self._lock = threading.Lock()
        self._expiry_seconds = expiry_seconds or get_settings().cancellation_expiry_seconds
    
    def cancel(
        self, 
        session_id: str, 
        phase: Optional[str] = None, 
        content_length: int = 0
    ) -> None:
        """
        Mark a session as cancelled.
        
        Args:
            session_id: The session ID to cancel
            phase: Current phase when cancelled ('thinking', 'answer')
            content_length: Length of content generated so far
        """
        with self._lock:
            entry = CancellationEntry(
                session_id=session_id,
                timestamp=datetime.now(),
                phase=phase,
                content_length=content_length
            )
            self._cancelled_sessions[session_id] = entry
            
            logger.info(
                f"Session cancelled: session_id={session_id}, "
                f"phase={phase}, content_length={content_length}"
            )
            
            # Cleanup stale entries
            self._cleanup_stale_entries()
    
    def is_cancelled(self, session_id: str) -> bool:
        """
        Check if a session has been cancelled.
        
        Args:
            session_id: The session ID to check
            
        Returns:
            True if the session is cancelled, False otherwise
        """
        with self._lock:
            return session_id in self._cancelled_sessions
    
    def clear(self, session_id: str) -> None:
        """
        Clear the cancellation status for a session.
        
        Args:
            session_id: The session ID to clear
        """
        with self._lock:
            if session_id in self._cancelled_sessions:
                del self._cancelled_sessions[session_id]
                logger.debug(f"Cancellation cleared for session: {session_id}")
    
    def get_entry(self, session_id: str) -> Optional[CancellationEntry]:
        """
        Get the cancellation entry for a session.
        
        Args:
            session_id: The session ID to get
            
        Returns:
            The CancellationEntry if found, None otherwise
        """
        with self._lock:
            return self._cancelled_sessions.get(session_id)
    
    def _cleanup_stale_entries(self) -> None:
        """
        Remove entries that have exceeded the expiry time.
        
        Note: This method assumes the lock is already held.
        """
        now = datetime.now()
        expiry_threshold = now - timedelta(seconds=self._expiry_seconds)
        
        stale_sessions = [
            session_id 
            for session_id, entry in self._cancelled_sessions.items()
            if entry.timestamp < expiry_threshold
        ]
        
        for session_id in stale_sessions:
            del self._cancelled_sessions[session_id]
            logger.debug(f"Removed stale cancellation entry: {session_id}")
        
        if stale_sessions:
            logger.info(f"Cleaned up {len(stale_sessions)} stale cancellation entries")
    
    def cleanup_all(self) -> int:
        """
        Force cleanup of all stale entries.
        
        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            initial_count = len(self._cancelled_sessions)
            self._cleanup_stale_entries()
            return initial_count - len(self._cancelled_sessions)
    
    @property
    def active_count(self) -> int:
        """Get the number of active cancellation entries."""
        with self._lock:
            return len(self._cancelled_sessions)


# Global singleton instance
_cancellation_manager: Optional[CancellationManager] = None
_manager_lock = threading.Lock()


def get_cancellation_manager() -> CancellationManager:
    """
    Get the global CancellationManager singleton instance.
    
    Returns:
        The global CancellationManager instance
    """
    global _cancellation_manager
    
    if _cancellation_manager is None:
        with _manager_lock:
            if _cancellation_manager is None:
                _cancellation_manager = CancellationManager()
    
    return _cancellation_manager
