"""
SQLite database for session history storage.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)


class SessionDatabase:
    """SQLite database for storing session history."""

    def __init__(self, db_path: str = "data/sessions.db"):
        self.db_path = db_path
        # Ensure data directory exists
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_count INTEGER DEFAULT 0
                )
            """)

            # Messages table (stores both user and assistant messages)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    request_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT,
                    content_type TEXT DEFAULT 'text',
                    model TEXT,
                    tokens_used INTEGER,
                    latency_ms INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)

            # Create indexes for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session_id
                ON messages(session_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_created_at
                ON messages(created_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                ON sessions(updated_at)
            """)

            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    @contextmanager
    def _get_connection(self):
        """Get database connection with context manager."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def save_message(
        self,
        session_id: str,
        user_id: str,
        app_name: str,
        role: str,
        content: str,
        content_type: str = "text",
        request_id: str = None,
        model: str = None,
        tokens_used: int = None,
        latency_ms: int = None
    ) -> int:
        """
        Save a message to the database.
        Returns the message ID.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Upsert session
            cursor.execute("""
                INSERT INTO sessions (session_id, user_id, app_name, updated_at, message_count)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP,
                    message_count = message_count + 1
            """, (session_id, user_id, app_name))

            # Insert message
            cursor.execute("""
                INSERT INTO messages
                (session_id, request_id, role, content, content_type, model, tokens_used, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, request_id, role, content, content_type, model, tokens_used, latency_ms))

            conn.commit()
            message_id = cursor.lastrowid
            logger.debug(f"Saved message {message_id} for session {session_id}")
            return message_id

    def get_session_history(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get message history for a session."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, request_id, role, content, content_type, model,
                       tokens_used, latency_ms, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ? OFFSET ?
            """, (session_id, limit, offset))

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT session_id, user_id, app_name, created_at, updated_at, message_count
                FROM sessions
                WHERE session_id = ?
            """, (session_id,))

            row = cursor.fetchone()
            return dict(row) if row else None

    def list_sessions(
        self,
        user_id: str = None,
        app_name: str = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List sessions with optional filtering."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM sessions WHERE 1=1"
            params = []

            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            if app_name:
                query += " AND app_name = ?"
                params.append(app_name)

            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def delete_session_history(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count of deleted messages."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Count messages to delete
            cursor.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,))
            count = cursor.fetchone()[0]

            # Delete messages
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

            # Delete session record
            cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

            conn.commit()
            logger.info(f"Deleted {count} messages for session {session_id}")
            return count

    def search_messages(
        self,
        query: str,
        session_id: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Search messages by content."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            sql = """
                SELECT m.*, s.user_id, s.app_name
                FROM messages m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE m.content LIKE ?
            """
            params = [f"%{query}%"]

            if session_id:
                sql += " AND m.session_id = ?"
                params.append(session_id)

            sql += " ORDER BY m.created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Total sessions
            cursor.execute("SELECT COUNT(*) FROM sessions")
            total_sessions = cursor.fetchone()[0]

            # Total messages
            cursor.execute("SELECT COUNT(*) FROM messages")
            total_messages = cursor.fetchone()[0]

            # Messages by role
            cursor.execute("""
                SELECT role, COUNT(*) as count
                FROM messages
                GROUP BY role
            """)
            by_role = {row['role']: row['count'] for row in cursor.fetchall()}

            # Recent activity (last 24 hours)
            cursor.execute("""
                SELECT COUNT(*) FROM messages
                WHERE created_at >= datetime('now', '-1 day')
            """)
            recent_messages = cursor.fetchone()[0]

            return {
                "total_sessions": total_sessions,
                "total_messages": total_messages,
                "messages_by_role": by_role,
                "recent_messages_24h": recent_messages
            }

    def cleanup_old_sessions(self, days: int = 30) -> int:
        """Delete sessions older than specified days. Returns count of deleted sessions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get old session IDs
            cursor.execute("""
                SELECT session_id FROM sessions
                WHERE updated_at < datetime('now', ?)
            """, (f'-{days} days',))

            old_sessions = [row['session_id'] for row in cursor.fetchall()]

            if not old_sessions:
                return 0

            # Delete messages for old sessions
            placeholders = ','.join('?' * len(old_sessions))
            cursor.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", old_sessions)

            # Delete sessions
            cursor.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholders})", old_sessions)

            conn.commit()
            logger.info(f"Cleaned up {len(old_sessions)} old sessions (older than {days} days)")
            return len(old_sessions)


# Global database instance
_db: Optional[SessionDatabase] = None


def get_database() -> SessionDatabase:
    """Get the global database instance."""
    global _db
    if _db is None:
        from app.config import settings
        db_path = getattr(settings, 'database_path', 'data/sessions.db')
        _db = SessionDatabase(db_path)
    return _db


def init_database(db_path: str = "data/sessions.db"):
    """Initialize the global database instance."""
    global _db
    _db = SessionDatabase(db_path)
    return _db
