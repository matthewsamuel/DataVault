"""
DataVault Dashboard - Database Management
SQLite database for hash caching and undo tracking
"""
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from backend import migrations

class Database:
    """SQLite database manager"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self):
        """Get database connection"""
        return sqlite3.connect(str(self.db_path))
    
    def _init_db(self):
        """Create or upgrade the schema via numbered migrations (see migrations.py)"""
        migrations.migrate(self.db_path)

    # Rating methods
    def get_rating(self, path: str) -> int:
        """Get current rating for a file"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT rating FROM file_ratings WHERE path = ?", (path,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 1000

    def update_rating(self, path: str, new_rating: int):
        """Update rating for a file"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO file_ratings (path, rating, votes, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET
                rating = ?,
                votes = votes + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (path, new_rating, new_rating))
        conn.commit()
        conn.close()

    def get_all_ratings(self) -> Dict[str, int]:
        """Get all file ratings as a dictionary for bulk access"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT path, rating FROM file_ratings")
        results = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in results}

    # Settings methods
    def get_folder_status(self, path: str) -> bool:
        """Check if a folder is enabled for shows"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT enabled FROM show_settings WHERE path = ?", (path,))
        result = cursor.fetchone()
        conn.close()
        return bool(result[0]) if result else True

    def set_folder_status(self, path: str, enabled: bool):
        """Enable/disable a folder for shows"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO show_settings (path, enabled, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET
                enabled = ?,
                updated_at = CURRENT_TIMESTAMP
        """, (path, 1 if enabled else 0, 1 if enabled else 0))
        conn.commit()
        conn.close()
    
    # Hash caching methods
    
    def get_cached_hash(self, path: Path, size: int, mtime: float) -> Optional[str]:
        """
        Get cached hash if file unchanged.
        Returns None if cache miss or file modified.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT sha256, size, mtime FROM file_hashes WHERE path = ?",
            (str(path),)
        )
        result = cursor.fetchone()
        conn.close()
        
        if result:
            cached_hash, cached_size, cached_mtime = result
            # Validate cache is still valid
            if cached_size == size and cached_mtime == mtime:
                return cached_hash
        
        return None
    
    def store_hash(self, path: Path, size: int, mtime: float, sha256: str):
        """Store file hash in cache"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO file_hashes (path, size, mtime, sha256, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (str(path), size, mtime, sha256, datetime.now()))
        
        conn.commit()
        conn.close()
    
    def invalidate_hash(self, path: Path):
        """Remove hash from cache"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM file_hashes WHERE path = ?", (str(path),))
        
        conn.commit()
        conn.close()
    
    # Delete run tracking methods
    
    def create_delete_run(self) -> str:
        """Create new delete run, return run_id"""
        run_id = str(uuid.uuid4())
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO delete_runs (run_id, timestamp, total_files) VALUES (?, ?, 0)",
            (run_id, datetime.now())
        )
        
        conn.commit()
        conn.close()
        
        return run_id
    
    def record_delete_operation(self, run_id: str, src_path: Path, dst_path: Path):
        """Record individual file move in a delete run"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO delete_operations (run_id, src_path, dst_path, timestamp)
            VALUES (?, ?, ?, ?)
        """, (run_id, str(src_path), str(dst_path), datetime.now()))
        
        # Update total count
        cursor.execute("""
            UPDATE delete_runs
            SET total_files = (
                SELECT COUNT(*) FROM delete_operations WHERE run_id = ?
            )
            WHERE run_id = ?
        """, (run_id, run_id))
        
        conn.commit()
        conn.close()
    
    def get_last_delete_run(self) -> Optional[Dict[str, Any]]:
        """Get most recent delete run"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT run_id, timestamp, total_files
            FROM delete_runs
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                "run_id": result[0],
                "timestamp": result[1],
                "total_files": result[2]
            }
        return None
    
    def get_delete_operations(self, run_id: str) -> List[Dict[str, str]]:
        """Get all operations for a delete run"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT src_path, dst_path
            FROM delete_operations
            WHERE run_id = ?
            ORDER BY id
        """, (run_id,))
        results = cursor.fetchall()
        conn.close()
        
        return [
            {"src_path": row[0], "dst_path": row[1]}
            for row in results
        ]
    
    # Keurer methods

    def get_keurer_decided_paths(self, folder: str) -> List[str]:
        """
        Geeft alle al beoordeelde paden terug voor een specifieke map.
        Verplaatsingen tellen niet mee: komt zo'n bestand ooit terug, dan mag
        het opnieuw beoordeeld worden.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT path FROM keurer_decisions WHERE folder = ? AND decision != 'move'",
            (folder,),
        )
        results = cursor.fetchall()
        conn.close()
        return [row[0] for row in results]

    def record_keurer_decision(
        self,
        path: str,
        folder: str,
        decision: str,
        dest_path: Optional[str] = None,
        dest_ino: Optional[int] = None,
        dest_size: Optional[int] = None,
    ) -> int:
        """
        Sla een houd/verwijder/verplaats-beslissing op. Een houd/verwijder-beslissing
        vervangt een eerdere houd/verwijder-beslissing voor hetzelfde pad; verplaats-
        beslissingen blijven altijd staan (migratie 4), zodat hun undo niet verloren gaat.
        dest_path is waar het bestand nu staat (TRASH of doelmap), nodig voor undo;
        dest_ino en dest_size identificeren dat bestand (een rename houdt de inode).
        Geeft het id van de beslissing terug.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO keurer_decisions
                (path, folder, decision, dest_path, dest_ino, dest_size, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (path, folder, decision, dest_path, dest_ino, dest_size),
        )
        decision_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return decision_id

    _KEURER_COLUMNS = "id, path, folder, decision, dest_path, decided_at, dest_ino, dest_size"

    @staticmethod
    def _keurer_row(row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "path": row[1],
            "folder": row[2],
            "decision": row[3],
            "dest_path": row[4],
            "decided_at": row[5],
            "dest_ino": row[6],
            "dest_size": row[7],
        }

    def get_keurer_decision(self, decision_id: int) -> Optional[Dict[str, Any]]:
        """Een enkele beslissing op id, of None."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {self._KEURER_COLUMNS} FROM keurer_decisions WHERE id = ?",
            (decision_id,),
        )
        row = cursor.fetchone()
        conn.close()
        return self._keurer_row(row) if row else None

    def get_recent_keurer_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """De nieuwste beslissingen eerst (voor de undo-stapel)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {self._KEURER_COLUMNS} FROM keurer_decisions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._keurer_row(r) for r in rows]

    def delete_keurer_decision_by_id(self, decision_id: int):
        """Verwijder een beslissing (na een geslaagde undo)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM keurer_decisions WHERE id = ?", (decision_id,))
        conn.commit()
        conn.close()

    def get_keurer_stats(self) -> Dict[str, Any]:
        """Geeft streak, dagdoel-voortgang en totaal terug."""
        from datetime import date, timedelta

        conn = self._get_connection()
        cursor = conn.cursor()

        # Verplaatsingen tellen (zoals altijd) niet mee voor dagdoel en streak
        cursor.execute("SELECT COUNT(*) FROM keurer_decisions WHERE decision != 'move'")
        total = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM keurer_decisions
            WHERE DATE(decided_at, 'localtime') = DATE('now', 'localtime')
              AND decision != 'move'
            """
        )
        today = cursor.fetchone()[0]

        # Dagelijkse tellingen voor streakberekening
        cursor.execute(
            """
            SELECT DATE(decided_at, 'localtime') AS day, COUNT(*) AS cnt
            FROM keurer_decisions
            WHERE decision != 'move'
            GROUP BY day
            ORDER BY day DESC
            LIMIT 400
            """
        )
        daily_counts: Dict[str, int] = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        # Bereken huidige streak (opeenvolgende dagen met >= 20 beslissingen)
        streak = 0
        check_date = date.today()
        today_str = str(check_date)

        # Als dagdoel nog niet bereikt, begin bij gisteren
        if daily_counts.get(today_str, 0) < 20:
            check_date = check_date - timedelta(days=1)

        while True:
            day_str = str(check_date)
            if daily_counts.get(day_str, 0) >= 20:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break

        return {
            "total": total,
            "today": today,
            "goal": 20,
            "streak": streak,
            "goal_reached_today": today >= 20,
        }

    def delete_run(self, run_id: str):
        """Delete a run and its operations (after successful undo)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM delete_operations WHERE run_id = ?", (run_id,))
        cursor.execute("DELETE FROM delete_runs WHERE run_id = ?", (run_id,))
        
        conn.commit()
        conn.close()
