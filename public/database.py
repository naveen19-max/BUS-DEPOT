from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Dict


def _clean_text(value: Any) -> str:
    return (str(value or "")).strip()


def _clean_id(value: Any) -> str:
    raw = _clean_text(value).upper()
    return "".join(ch for ch in raw if ch.isalnum())


def _clean_phone(value: Any) -> str:
    raw = _clean_text(value)
    if raw.startswith("+"):
        return "+" + "".join(ch for ch in raw[1:] if ch.isdigit())
    return "".join(ch for ch in raw if ch.isdigit())


class MySQLDepotRepository:
    def __init__(self):
        self.host = os.getenv("MYSQL_HOST", "127.0.0.1")
        self.port = int(os.getenv("MYSQL_PORT", "3306"))
        self.user = os.getenv("MYSQL_USER", "root")
        self.password = os.getenv("MYSQL_PASSWORD", "")
        self.database = os.getenv("MYSQL_DATABASE", "bus_depot")
        self.pool_name = os.getenv("MYSQL_POOL_NAME", "bus_depot_pool")
        self.pool_size = int(os.getenv("MYSQL_POOL_SIZE", "5"))

        self.default_admin_id = _clean_id(os.getenv("DEFAULT_ADMIN_ID", "ADMIN001"))
        self.default_admin_name = _clean_text(os.getenv("DEFAULT_ADMIN_NAME", "Depot Admin"))
        self.default_admin_qr = _clean_text(os.getenv("DEFAULT_ADMIN_QR", "ADMIN_MASTER_001"))
        self.default_admin_username = _clean_text(os.getenv("DEFAULT_ADMIN_USERNAME", "admin")).lower()
        self.default_admin_password = _clean_text(os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123"))

        self.mysql = None
        self.pool = None
        self.last_error = ""
        self._connect()

    @property
    def is_connected(self) -> bool:
        return self.pool is not None

    def status(self) -> Dict[str, Any]:
        return {
            "connected": self.is_connected,
            "database": self.database,
            "host": self.host,
            "port": self.port,
            "error": self.last_error
        }

    def reconnect(self) -> Dict[str, Any]:
        self.pool = None
        self.last_error = ""
        self._connect()
        return self.status()

    def _connect(self) -> None:
        try:
            import mysql.connector
            from mysql.connector import pooling
            self.mysql = mysql.connector
        except Exception:
            self.last_error = "mysql-connector-python is not installed. Run: python -m pip install mysql-connector-python"
            return

        try:
            bootstrap = self.mysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password
            )
            bootstrap_cursor = bootstrap.cursor()
            bootstrap_cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
            bootstrap_cursor.close()
            bootstrap.close()
        except Exception as error:
            self.last_error = f"MySQL bootstrap failed: {error}"
            return

        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name=self.pool_name,
                pool_size=self.pool_size,
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=False
            )
            self._init_schema()
            self.last_error = ""
        except Exception as error:
            self.pool = None
            self.last_error = f"MySQL pool init failed: {error}"

    def _get_connection(self):
        if not self.pool:
            raise RuntimeError(self.last_error or "MySQL is not connected.")
        return self.pool.get_connection()

    def _init_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS drivers (
              driver_id VARCHAR(64) PRIMARY KEY,
              qr_code VARCHAR(255) NOT NULL UNIQUE,
              driver_name VARCHAR(120) NOT NULL,
              bus_number VARCHAR(40) NOT NULL,
              phone_number VARCHAR(32) NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              INDEX idx_driver_qr (qr_code)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS admins (
              admin_id VARCHAR(64) PRIMARY KEY,
              admin_name VARCHAR(120) NOT NULL,
              username VARCHAR(80) NOT NULL,
              password_hash VARCHAR(255) NOT NULL,
              qr_code VARCHAR(255) NOT NULL UNIQUE,
              is_active TINYINT(1) NOT NULL DEFAULT 1,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              INDEX idx_admin_username (username),
              INDEX idx_admin_qr (qr_code)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS active_entries (
              driver_id VARCHAR(64) PRIMARY KEY,
              entry_time DATETIME NOT NULL,
              note VARCHAR(255) DEFAULT '',
              INDEX idx_entry_time (entry_time)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS movement_logs (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              driver_id VARCHAR(64) NOT NULL,
              driver_name VARCHAR(120) NOT NULL,
              bus_number VARCHAR(40) NOT NULL,
              phone_number VARCHAR(32) NOT NULL,
              qr_code VARCHAR(255) NOT NULL,
              movement ENUM('entry', 'exit') NOT NULL,
              event_time DATETIME NOT NULL,
              note VARCHAR(255) DEFAULT '',
              INDEX idx_event_time (event_time),
              INDEX idx_driver_id (driver_id),
              INDEX idx_movement (movement)
            )
            """
        ]

        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            for statement in statements:
                cursor.execute(statement)

            # Lightweight migrations for older tables.
            self._ensure_column(cursor, "drivers", "qr_code", "VARCHAR(255) NOT NULL DEFAULT ''")
            self._ensure_column(cursor, "movement_logs", "qr_code", "VARCHAR(255) NOT NULL DEFAULT ''")
            self._ensure_column(cursor, "active_entries", "note", "VARCHAR(255) DEFAULT ''")
            self._ensure_column(cursor, "admins", "username", "VARCHAR(80) NOT NULL DEFAULT ''")
            self._ensure_column(cursor, "admins", "password_hash", "VARCHAR(255) NOT NULL DEFAULT ''")

            connection.commit()
            self._seed_default_admin(connection)
        finally:
            cursor.close()
            connection.close()

    def _ensure_column(self, cursor, table_name: str, column_name: str, definition: str) -> None:
        cursor.execute(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
        if cursor.fetchone():
            return
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _seed_default_admin(self, connection) -> None:
        from werkzeug.security import generate_password_hash

        username = (self.default_admin_username or "admin").lower()
        password_hash = generate_password_hash(self.default_admin_password or "admin123")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO admins (admin_id, admin_name, username, password_hash, qr_code, is_active)
                VALUES (%s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                  admin_name = VALUES(admin_name),
                  username = VALUES(username),
                  password_hash = VALUES(password_hash),
                  qr_code = VALUES(qr_code),
                  is_active = 1
                """,
                (
                    self.default_admin_id or "ADMIN001",
                    self.default_admin_name or "Depot Admin",
                    username,
                    password_hash,
                    self.default_admin_qr or "ADMIN_MASTER_001",
                )
            )
            connection.commit()
        finally:
            cursor.close()

    def get_admin_by_qr(self, qr_code: str) -> Dict[str, Any] | None:
        clean_qr = _clean_text(qr_code)
        if not clean_qr:
            return None

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT admin_id, admin_name, qr_code, is_active, updated_at
                FROM admins
                WHERE qr_code = %s AND is_active = 1
                """,
                (clean_qr,)
            )
            row = cursor.fetchone()
            return self._serialize_row(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def authenticate_admin(self, username: str, password: str) -> Dict[str, Any] | None:
        from werkzeug.security import check_password_hash

        clean_username = _clean_text(username).lower()
        clean_password = _clean_text(password)
        if not clean_username or not clean_password:
            return None

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT admin_id, admin_name, username, password_hash, qr_code, is_active, updated_at
                FROM admins
                WHERE LOWER(username) = %s AND is_active = 1
                LIMIT 1
                """,
                (clean_username,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            password_hash = _clean_text(row.get("password_hash"))
            if not password_hash or not check_password_hash(password_hash, clean_password):
                return None
            row.pop("password_hash", None)
            return self._serialize_row(row)
        finally:
            cursor.close()
            connection.close()

    def get_driver(self, driver_id: str) -> Dict[str, Any] | None:
        clean_driver_id = _clean_id(driver_id)
        if not clean_driver_id:
            return None

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT driver_id, qr_code, driver_name, bus_number, phone_number, updated_at
                FROM drivers
                WHERE driver_id = %s
                """,
                (clean_driver_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._serialize_row(row)
        finally:
            cursor.close()
            connection.close()

    def get_driver_by_qr(self, qr_code: str) -> Dict[str, Any] | None:
        clean_qr = _clean_text(qr_code)
        if not clean_qr:
            return None

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT driver_id, qr_code, driver_name, bus_number, phone_number, updated_at
                FROM drivers
                WHERE qr_code = %s
                """,
                (clean_qr,)
            )
            row = cursor.fetchone()
            return self._serialize_row(row) if row else None
        finally:
            cursor.close()
            connection.close()

    def register_driver_with_qr(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        qr_code = _clean_text(payload.get("qr_code"))
        driver_id = _clean_id(payload.get("driver_id"))
        driver_name = _clean_text(payload.get("driver_name"))
        bus_number = _clean_text(payload.get("bus_number")).upper()
        phone_number = _clean_phone(payload.get("phone_number"))

        if not qr_code:
            raise ValueError("QR code is required.")
        if not driver_id:
            raise ValueError("Driver ID is required.")
        if not driver_name:
            raise ValueError("Driver name is required.")
        if not bus_number:
            raise ValueError("Bus number is required.")
        if not phone_number:
            raise ValueError("Phone number is required.")

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT driver_id
                FROM drivers
                WHERE qr_code = %s
                FOR UPDATE
                """,
                (qr_code,)
            )
            existing_qr = cursor.fetchone()
            if existing_qr and _clean_id(existing_qr.get("driver_id")) != driver_id:
                raise ValueError("This QR code is already linked to another driver ID.")

            cursor.execute(
                """
                INSERT INTO drivers (driver_id, qr_code, driver_name, bus_number, phone_number)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  qr_code = VALUES(qr_code),
                  driver_name = VALUES(driver_name),
                  bus_number = VALUES(bus_number),
                  phone_number = VALUES(phone_number)
                """,
                (driver_id, qr_code, driver_name, bus_number, phone_number)
            )

            connection.commit()
            return {
                "ok": True,
                "driver_id": driver_id,
                "qr_code": qr_code,
                "driver_name": driver_name,
                "bus_number": bus_number,
                "phone_number": phone_number
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def register_and_record_entry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        note = _clean_text(payload.get("note")) or "First QR registration + entry"
        registered = self.register_driver_with_qr(payload)

        movement_result = self.record_entry({
            "driver_id": registered["driver_id"],
            "qr_code": registered["qr_code"],
            "note": note
        })
        movement_result["registered"] = True
        movement_result["new_registration"] = True
        return movement_result

    def record_entry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        driver_id = _clean_id(payload.get("driver_id"))
        note = _clean_text(payload.get("note")) or "QR entry"
        qr_override = _clean_text(payload.get("qr_code"))
        if not driver_id:
            raise ValueError("Driver ID is required.")

        now = datetime.now().replace(microsecond=0)
        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT driver_id, qr_code, driver_name, bus_number, phone_number
                FROM drivers
                WHERE driver_id = %s
                FOR UPDATE
                """,
                (driver_id,)
            )
            driver = cursor.fetchone()
            if not driver:
                raise ValueError("Driver is not registered. Scan QR and register first.")

            # Self-heal stale active state for this driver before validating entry.
            self._sync_active_entry_for_driver(cursor, driver_id)
            cursor.execute(
                "SELECT driver_id FROM active_entries WHERE driver_id = %s FOR UPDATE",
                (driver_id,)
            )
            if cursor.fetchone():
                raise ValueError("This driver is already marked inside the depot.")

            cursor.execute(
                """
                INSERT INTO active_entries (driver_id, entry_time, note)
                VALUES (%s, %s, %s)
                """,
                (driver_id, now, note)
            )

            qr_code = qr_override or _clean_text(driver.get("qr_code"))
            cursor.execute(
                """
                INSERT INTO movement_logs
                  (driver_id, driver_name, bus_number, phone_number, qr_code, movement, event_time, note)
                VALUES
                  (%s, %s, %s, %s, %s, 'entry', %s, %s)
                """,
                (
                    driver_id,
                    _clean_text(driver.get("driver_name")),
                    _clean_text(driver.get("bus_number")).upper(),
                    _clean_phone(driver.get("phone_number")),
                    qr_code,
                    now,
                    note
                )
            )
            movement_log_id = int(cursor.lastrowid or 0)

            connection.commit()
            return {
                "ok": True,
                "registered": True,
                "movement": "entry",
                "log_id": movement_log_id,
                "driver_id": driver_id,
                "driver_name": _clean_text(driver.get("driver_name")),
                "bus_number": _clean_text(driver.get("bus_number")).upper(),
                "phone_number": _clean_phone(driver.get("phone_number")),
                "qr_code": qr_code,
                "event_time": now.isoformat(sep=" ")
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_exit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        driver_id = _clean_id(payload.get("driver_id"))
        note = _clean_text(payload.get("note")) or "QR exit"
        if not driver_id:
            raise ValueError("Driver ID is required.")

        now = datetime.now().replace(microsecond=0)
        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            # Self-heal stale active state for this driver before validating exit.
            self._sync_active_entry_for_driver(cursor, driver_id)
            cursor.execute(
                """
                SELECT d.driver_id, d.qr_code, d.driver_name, d.bus_number, d.phone_number
                FROM active_entries a
                JOIN drivers d ON d.driver_id = a.driver_id
                WHERE a.driver_id = %s
                FOR UPDATE
                """,
                (driver_id,)
            )
            driver = cursor.fetchone()
            if not driver:
                raise ValueError("No active entry found for this driver ID.")

            cursor.execute(
                """
                INSERT INTO movement_logs
                  (driver_id, driver_name, bus_number, phone_number, qr_code, movement, event_time, note)
                VALUES
                  (%s, %s, %s, %s, %s, 'exit', %s, %s)
                """,
                (
                    driver_id,
                    _clean_text(driver.get("driver_name")),
                    _clean_text(driver.get("bus_number")).upper(),
                    _clean_phone(driver.get("phone_number")),
                    _clean_text(driver.get("qr_code")),
                    now,
                    note
                )
            )
            movement_log_id = int(cursor.lastrowid or 0)

            cursor.execute("DELETE FROM active_entries WHERE driver_id = %s", (driver_id,))
            connection.commit()
            return {
                "ok": True,
                "registered": True,
                "movement": "exit",
                "log_id": movement_log_id,
                "driver_id": driver_id,
                "driver_name": _clean_text(driver.get("driver_name")),
                "bus_number": _clean_text(driver.get("bus_number")).upper(),
                "phone_number": _clean_phone(driver.get("phone_number")),
                "qr_code": _clean_text(driver.get("qr_code")),
                "event_time": now.isoformat(sep=" ")
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def toggle_scan_by_qr(self, qr_code: str, note: str = "Auto QR scan") -> Dict[str, Any]:
        clean_qr = _clean_text(qr_code)
        if not clean_qr:
            raise ValueError("QR code is required.")

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        now = datetime.now().replace(microsecond=0)
        try:
            cursor.execute(
                """
                SELECT driver_id, qr_code, driver_name, bus_number, phone_number
                FROM drivers
                WHERE qr_code = %s
                FOR UPDATE
                """,
                (clean_qr,)
            )
            driver = cursor.fetchone()
            if not driver:
                connection.rollback()
                return {
                    "ok": True,
                    "registered": False,
                    "qr_code": clean_qr
                }

            driver_id = _clean_id(driver.get("driver_id"))
            driver_name = _clean_text(driver.get("driver_name"))
            bus_number = _clean_text(driver.get("bus_number")).upper()
            phone_number = _clean_phone(driver.get("phone_number"))

            # Self-heal stale active state for this driver before toggle.
            self._sync_active_entry_for_driver(cursor, driver_id)
            cursor.execute(
                "SELECT driver_id FROM active_entries WHERE driver_id = %s FOR UPDATE",
                (driver_id,)
            )
            active = cursor.fetchone()
            if active:
                movement = "exit"
                cursor.execute("DELETE FROM active_entries WHERE driver_id = %s", (driver_id,))
            else:
                movement = "entry"
                cursor.execute(
                    """
                    INSERT INTO active_entries (driver_id, entry_time, note)
                    VALUES (%s, %s, %s)
                    """,
                    (driver_id, now, note)
                )

            cursor.execute(
                """
                INSERT INTO movement_logs
                  (driver_id, driver_name, bus_number, phone_number, qr_code, movement, event_time, note)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (driver_id, driver_name, bus_number, phone_number, clean_qr, movement, now, note)
            )
            movement_log_id = int(cursor.lastrowid or 0)

            connection.commit()
            return {
                "ok": True,
                "registered": True,
                "movement": movement,
                "log_id": movement_log_id,
                "driver_id": driver_id,
                "driver_name": driver_name,
                "bus_number": bus_number,
                "phone_number": phone_number,
                "qr_code": clean_qr,
                "event_time": now.isoformat(sep=" ")
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def dashboard_data(self, limit: int = 60) -> Dict[str, Any]:
        limit = max(10, min(int(limit), 200))
        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT
                  SUM(CASE WHEN movement='entry' THEN 1 ELSE 0 END) AS entries_total,
                  SUM(CASE WHEN movement='exit' THEN 1 ELSE 0 END) AS exits_total
                FROM movement_logs
                """
            )
            totals = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT
                  SUM(CASE WHEN movement='entry' THEN 1 ELSE 0 END) AS entries_today,
                  SUM(CASE WHEN movement='exit' THEN 1 ELSE 0 END) AS exits_today
                FROM movement_logs
                WHERE DATE(event_time) = CURDATE()
                """
            )
            today = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT
                  m.driver_id,
                  d.driver_name,
                  d.bus_number,
                  d.phone_number,
                  d.qr_code,
                  m.event_time AS entry_time,
                  m.note
                FROM movement_logs m
                JOIN (
                  SELECT driver_id, MAX(id) AS latest_id
                  FROM movement_logs
                  GROUP BY driver_id
                ) latest ON latest.latest_id = m.id
                JOIN drivers d ON d.driver_id = m.driver_id
                WHERE m.movement = 'entry'
                ORDER BY m.event_time DESC, m.id DESC
                """
            )
            active_rows = [self._serialize_row(row) for row in (cursor.fetchall() or [])]
            inside_count = len(active_rows)

            cursor.execute(
                """
                SELECT
                  id,
                  movement,
                  driver_id,
                  driver_name,
                  bus_number,
                  phone_number,
                  qr_code,
                  event_time,
                  note
                FROM movement_logs
                ORDER BY event_time DESC, id DESC
                LIMIT %s
                """,
                (limit,)
            )
            recent_rows = [self._serialize_row(row) for row in (cursor.fetchall() or [])]

            return {
                "connected": True,
                "counts": {
                    "inside": inside_count,
                    "entries_total": int(totals.get("entries_total") or 0),
                    "exits_total": int(totals.get("exits_total") or 0),
                    "entries_today": int(today.get("entries_today") or 0),
                    "exits_today": int(today.get("exits_today") or 0)
                },
                "active_entries": active_rows,
                "recent_logs": recent_rows
            }
        finally:
            cursor.close()
            connection.close()

    def daily_report(self, report_date: str | None = None) -> Dict[str, Any]:
        if report_date:
            try:
                report_dt = datetime.strptime(report_date, "%Y-%m-%d").date()
            except ValueError as error:
                raise ValueError("Invalid date format. Use YYYY-MM-DD.") from error
        else:
            report_dt = date.today()

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT
                  SUM(CASE WHEN movement='entry' THEN 1 ELSE 0 END) AS entries,
                  SUM(CASE WHEN movement='exit' THEN 1 ELSE 0 END) AS exits
                FROM movement_logs
                WHERE DATE(event_time) = %s
                """,
                (report_dt,)
            )
            totals = cursor.fetchone() or {}
            entries = int(totals.get("entries") or 0)
            exits = int(totals.get("exits") or 0)

            cursor.execute(
                """
                SELECT
                  HOUR(event_time) AS hour_of_day,
                  SUM(CASE WHEN movement='entry' THEN 1 ELSE 0 END) AS entries,
                  SUM(CASE WHEN movement='exit' THEN 1 ELSE 0 END) AS exits
                FROM movement_logs
                WHERE DATE(event_time) = %s
                GROUP BY HOUR(event_time)
                ORDER BY hour_of_day
                """,
                (report_dt,)
            )
            hourly_rows = [self._serialize_row(row) for row in (cursor.fetchall() or [])]

            cursor.execute(
                """
                SELECT
                  id,
                  movement,
                  driver_id,
                  driver_name,
                  bus_number,
                  phone_number,
                  qr_code,
                  event_time,
                  note
                FROM movement_logs
                WHERE DATE(event_time) = %s
                ORDER BY event_time ASC, id ASC
                """,
                (report_dt,)
            )
            day_events = [self._serialize_row(row) for row in (cursor.fetchall() or [])]

            cursor.execute("SELECT COUNT(*) AS currently_inside FROM active_entries")
            inside_now = int((cursor.fetchone() or {}).get("currently_inside") or 0)

            cursor.execute(
                """
                SELECT
                  driver_id,
                  driver_name,
                  bus_number,
                  phone_number,
                  SUM(CASE WHEN movement='entry' THEN 1 ELSE 0 END) AS entry_count,
                  SUM(CASE WHEN movement='exit' THEN 1 ELSE 0 END) AS exit_count,
                  MIN(CASE WHEN movement='entry' THEN event_time END) AS first_entry,
                  MAX(CASE WHEN movement='exit' THEN event_time END) AS last_exit
                FROM movement_logs
                WHERE DATE(event_time) = %s
                GROUP BY driver_id, driver_name, bus_number, phone_number
                ORDER BY first_entry ASC
                """,
                (report_dt,)
            )
            flow_rows = [self._serialize_row(row) for row in (cursor.fetchall() or [])]

            return {
                "report_date": report_dt.isoformat(),
                "summary": {
                    "entries": entries,
                    "exits": exits,
                    "inside_end_of_day_estimate": entries - exits,
                    "inside_now": inside_now
                },
                "hourly": hourly_rows,
                "flowchart": flow_rows,
                "events": day_events
            }
        finally:
            cursor.close()
            connection.close()

    def delete_movement_log(self, log_id: int) -> Dict[str, Any]:
        try:
            clean_id = int(log_id)
        except Exception as error:
            raise ValueError("Invalid movement log ID.") from error
        if clean_id <= 0:
            raise ValueError("Invalid movement log ID.")

        connection = self._get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT id, movement, driver_id, driver_name, event_time
                FROM movement_logs
                WHERE id = %s
                FOR UPDATE
                """,
                (clean_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Movement log not found.")

            cursor.execute("DELETE FROM movement_logs WHERE id = %s", (clean_id,))
            self._sync_active_entry_for_driver(cursor, row.get("driver_id"))
            connection.commit()
            serialized = self._serialize_row(row)
            return {
                "ok": True,
                "deleted_id": clean_id,
                "deleted_log": serialized
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _sync_active_entry_for_driver(self, cursor, driver_id: str) -> None:
        clean_driver_id = _clean_id(driver_id)
        if not clean_driver_id:
            return

        cursor.execute(
            """
            SELECT movement, event_time, note
            FROM movement_logs
            WHERE driver_id = %s
            ORDER BY event_time DESC, id DESC
            LIMIT 1
            """,
            (clean_driver_id,)
        )
        latest = cursor.fetchone()
        if not latest:
            cursor.execute("DELETE FROM active_entries WHERE driver_id = %s", (clean_driver_id,))
            return

        latest_movement = _clean_text(latest.get("movement")).lower()
        if latest_movement != "entry":
            cursor.execute("DELETE FROM active_entries WHERE driver_id = %s", (clean_driver_id,))
            return

        entry_time = latest.get("event_time")
        note = _clean_text(latest.get("note"))
        cursor.execute(
            """
            INSERT INTO active_entries (driver_id, entry_time, note)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
              entry_time = VALUES(entry_time),
              note = VALUES(note)
            """,
            (clean_driver_id, entry_time, note)
        )

    def clear_logs_and_active_entries(self) -> Dict[str, Any]:
        return self.clear_session_data()

    def clear_session_data(self) -> Dict[str, Any]:
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute("DELETE FROM active_entries")
            cursor.execute("DELETE FROM movement_logs")
            connection.commit()
            return {"ok": True}
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _serialize_row(self, row: Dict[str, Any] | None) -> Dict[str, Any]:
        if not row:
            return {}
        result = dict(row)
        for key, value in list(result.items()):
            if isinstance(value, datetime):
                result[key] = value.isoformat(sep=" ")
            elif isinstance(value, date):
                result[key] = value.isoformat()
        return result
