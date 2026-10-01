import sqlite3
import time


class Database:

    def __init__(self, path: str):
        self.path = path
        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False
        )

        self.connection.row_factory = sqlite3.Row

        self._setup()

    def _setup(self):
        cursor = self.connection.cursor()

        cursor.execute("""
            PRAGMA journal_mode=WAL;
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS units (
                discord_id INTEGER PRIMARY KEY,
                discord_name TEXT NOT NULL,
                roblox_id TEXT,
                roblox_name TEXT,
                department TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
        """)

        self.connection.commit()

    def set_unit_status(
        self,
        discord_id: int,
        discord_name: str,
        status: str,
        department: str,
        roblox_id: str | None = None,
        roblox_name: str | None = None
    ):
        now = int(time.time())

        cursor = self.connection.cursor()

        cursor.execute("""
            INSERT INTO units (
                discord_id,
                discord_name,
                roblox_id,
                roblox_name,
                department,
                status,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(discord_id)
            DO UPDATE SET
                discord_name = excluded.discord_name,
                roblox_id = COALESCE(
                    excluded.roblox_id,
                    units.roblox_id
                ),
                roblox_name = COALESCE(
                    excluded.roblox_name,
                    units.roblox_name
                ),
                department = excluded.department,
                status = excluded.status,
                updated_at = excluded.updated_at
        """, (
            discord_id,
            discord_name,
            roblox_id,
            roblox_name,
            department,
            status,
            now
        ))

        self.connection.commit()

    def get_active_units(self):
        cursor = self.connection.cursor()

        cursor.execute("""
            SELECT *
            FROM units
            WHERE department = ?
            AND status != '10-7'
            ORDER BY updated_at DESC
        """, (
            "State Patrol",
        ))

        return [
            dict(row)
            for row in cursor.fetchall()
        ]

    def save_webhook_event(self, payload: str):
        cursor = self.connection.cursor()

        cursor.execute("""
            INSERT INTO webhook_events (
                received_at,
                payload
            )
            VALUES (?, ?)
        """, (
            int(time.time()),
            payload
        ))

        self.connection.commit()
