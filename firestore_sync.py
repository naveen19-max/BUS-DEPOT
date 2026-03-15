from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable


def _clean_text(value: Any) -> str:
    return (str(value or "")).strip()


def _now_iso_utc() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class FirestoreMirror:
    def __init__(self):
        self.enabled = os.getenv("FIRESTORE_SYNC_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
        self.project_id = _clean_text(os.getenv("FIREBASE_PROJECT_ID"))
        self.service_account_path = _clean_text(os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH"))
        self.service_account_json = _clean_text(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))
        self.client = None
        self.last_error = ""
        self._connect()

    @property
    def is_connected(self) -> bool:
        return self.enabled and self.client is not None

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.is_connected,
            "project_id": self.project_id,
            "error": self.last_error,
        }

    def _connect(self) -> None:
        if not self.enabled:
            self.last_error = "Firestore sync disabled."
            return

        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except Exception:
            self.last_error = "firebase-admin is not installed. Run: python -m pip install firebase-admin"
            return

        try:
            try:
                firebase_admin.get_app()
            except ValueError:
                options = {}
                if self.project_id:
                    options["projectId"] = self.project_id

                if self.service_account_json:
                    info = json.loads(self.service_account_json)
                    cred = credentials.Certificate(info)
                    firebase_admin.initialize_app(cred, options or None)
                elif self.service_account_path:
                    cred = credentials.Certificate(self.service_account_path)
                    firebase_admin.initialize_app(cred, options or None)
                else:
                    # Uses GOOGLE_APPLICATION_CREDENTIALS if available.
                    firebase_admin.initialize_app(options=options or None)

            self.client = firestore.client()
            self.last_error = ""
        except Exception as error:
            self.client = None
            self.last_error = f"Firestore init failed: {error}"

    def reconnect(self) -> Dict[str, Any]:
        self.client = None
        self.last_error = ""
        self._connect()
        return self.status()

    def _set_error(self, error: Exception) -> None:
        self.last_error = str(error)

    def sync_movement_event(self, payload: Dict[str, Any]) -> bool:
        if not self.is_connected:
            return False
        try:
            driver_id = _clean_text(payload.get("driver_id")).upper()
            if not driver_id:
                return False

            movement = _clean_text(payload.get("movement")).lower()
            event_time = _clean_text(payload.get("event_time")) or _now_iso_utc()
            log_id = payload.get("log_id")
            log_doc_id = _clean_text(log_id)
            if not log_doc_id:
                safe_time = "".join(ch for ch in event_time if ch.isdigit())
                log_doc_id = f"{driver_id}_{movement}_{safe_time or 'event'}"

            driver_doc = {
                "driver_id": driver_id,
                "driver_name": _clean_text(payload.get("driver_name")),
                "bus_number": _clean_text(payload.get("bus_number")).upper(),
                "phone_number": _clean_text(payload.get("phone_number")),
                "qr_code": _clean_text(payload.get("qr_code")),
                "updated_at": _now_iso_utc(),
            }
            self.client.collection("drivers").document(driver_id).set(driver_doc, merge=True)

            movement_doc = {
                "id": int(log_id) if str(log_id or "").isdigit() else None,
                "driver_id": driver_id,
                "driver_name": driver_doc["driver_name"],
                "bus_number": driver_doc["bus_number"],
                "phone_number": driver_doc["phone_number"],
                "qr_code": driver_doc["qr_code"],
                "movement": movement,
                "event_time": event_time,
                "note": _clean_text(payload.get("note")),
                "synced_at": _now_iso_utc(),
            }
            self.client.collection("movement_logs").document(log_doc_id).set(movement_doc, merge=True)

            active_ref = self.client.collection("active_entries").document(driver_id)
            if movement == "entry":
                active_ref.set(
                    {
                        "driver_id": driver_id,
                        "driver_name": driver_doc["driver_name"],
                        "bus_number": driver_doc["bus_number"],
                        "phone_number": driver_doc["phone_number"],
                        "qr_code": driver_doc["qr_code"],
                        "entry_time": event_time,
                        "note": _clean_text(payload.get("note")),
                        "updated_at": _now_iso_utc(),
                    },
                    merge=True,
                )
            elif movement == "exit":
                active_ref.delete()

            self.client.collection("system").document("live").set(
                {
                    "last_event_type": movement,
                    "last_driver_id": driver_id,
                    "last_event_time": event_time,
                    "updated_at": _now_iso_utc(),
                },
                merge=True,
            )
            self.last_error = ""
            return True
        except Exception as error:
            self._set_error(error)
            return False

    def delete_movement_log(self, log_id: int) -> bool:
        if not self.is_connected:
            return False
        try:
            doc_id = _clean_text(log_id)
            if not doc_id:
                return False
            self.client.collection("movement_logs").document(doc_id).delete()
            self.last_error = ""
            return True
        except Exception as error:
            self._set_error(error)
            return False

    def clear_runtime_data(self) -> bool:
        if not self.is_connected:
            return False
        try:
            self._delete_collection_docs("movement_logs")
            self._delete_collection_docs("active_entries")
            self.client.collection("system").document("live").set(
                {
                    "last_event_type": "reset",
                    "updated_at": _now_iso_utc(),
                },
                merge=True,
            )
            self.last_error = ""
            return True
        except Exception as error:
            self._set_error(error)
            return False

    def sync_dashboard_snapshot(self, payload: Dict[str, Any]) -> bool:
        if not self.is_connected:
            return False
        try:
            counts = payload.get("counts") or {}
            active_entries = payload.get("active_entries") or []
            recent_logs = payload.get("recent_logs") or []

            self.client.collection("system").document("live").set(
                {
                    "counts": counts,
                    "dashboard_synced_at": _now_iso_utc(),
                },
                merge=True,
            )

            self._replace_active_entries(active_entries)
            self._upsert_recent_logs(recent_logs)
            self.last_error = ""
            return True
        except Exception as error:
            self._set_error(error)
            return False

    def _replace_active_entries(self, rows: Iterable[Dict[str, Any]]) -> None:
        self._delete_collection_docs("active_entries")
        for row in rows:
            driver_id = _clean_text(row.get("driver_id")).upper()
            if not driver_id:
                continue
            self.client.collection("active_entries").document(driver_id).set(
                {
                    "driver_id": driver_id,
                    "driver_name": _clean_text(row.get("driver_name")),
                    "bus_number": _clean_text(row.get("bus_number")).upper(),
                    "phone_number": _clean_text(row.get("phone_number")),
                    "qr_code": _clean_text(row.get("qr_code")),
                    "entry_time": _clean_text(row.get("entry_time")),
                    "note": _clean_text(row.get("note")),
                    "updated_at": _now_iso_utc(),
                },
                merge=True,
            )

    def _upsert_recent_logs(self, rows: Iterable[Dict[str, Any]]) -> None:
        for row in rows:
            doc_id = _clean_text(row.get("id"))
            if not doc_id:
                continue
            self.client.collection("movement_logs").document(doc_id).set(
                {
                    "id": int(row.get("id")) if str(row.get("id") or "").isdigit() else None,
                    "driver_id": _clean_text(row.get("driver_id")).upper(),
                    "driver_name": _clean_text(row.get("driver_name")),
                    "bus_number": _clean_text(row.get("bus_number")).upper(),
                    "phone_number": _clean_text(row.get("phone_number")),
                    "qr_code": _clean_text(row.get("qr_code")),
                    "movement": _clean_text(row.get("movement")).lower(),
                    "event_time": _clean_text(row.get("event_time")),
                    "note": _clean_text(row.get("note")),
                    "synced_at": _now_iso_utc(),
                },
                merge=True,
            )

    def _delete_collection_docs(self, collection_name: str, page_size: int = 200) -> None:
        while True:
            docs = list(self.client.collection(collection_name).limit(page_size).stream())
            if not docs:
                break
            batch = self.client.batch()
            for doc in docs:
                batch.delete(doc.reference)
            batch.commit()
