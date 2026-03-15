from __future__ import annotations

import atexit
import os
import threading
from collections import deque
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from camera_stream import CameraStream
from database import MySQLDepotRepository
from firestore_sync import FirestoreMirror


BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "bus-depot-secret-change-me")
app.config["JSON_SORT_KEYS"] = False

repo = MySQLDepotRepository()
firestore_mirror = FirestoreMirror()
ALLOW_MANUAL_ADMIN_LOGIN = os.getenv("ALLOW_MANUAL_ADMIN_LOGIN", "0").strip().lower() in {"1", "true", "yes"}
ALLOW_PASSWORD_ADMIN_LOGIN = os.getenv("ALLOW_PASSWORD_ADMIN_LOGIN", "1").strip().lower() in {"1", "true", "yes"}
ADMIN_QR_LOCK = (os.getenv("ADMIN_QR_LOCK", repo.default_admin_qr) or "").strip()
ADMIN_VIEW_ONLY = os.getenv("ADMIN_VIEW_ONLY", "1").strip().lower() not in {"0", "false", "no"}
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
SERVER_CAMERA_ENABLED = os.getenv("SERVER_CAMERA_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000")))

scanner_lock = threading.Lock()
scanner_state = {
    "last_scan": "",
    "last_scan_at": "",
    "last_event": None,
    "pending_registration": None,
    "last_admin_scan": None,
    "recent_events": deque(maxlen=80),
}


def _error_response(message: str, status_code: int = 400):
    return jsonify({"ok": False, "error": message}), status_code


def _clean_text(value: Any) -> str:
    return (str(value or "")).strip()


def _suggest_driver_id_from_qr(qr_code: str) -> str:
    raw = _clean_text(qr_code).upper()
    token = "".join(ch for ch in raw if ch.isalnum())
    if not token:
        return ""
    if len(token) > 14:
        return token[:14]
    return token


def _now_iso() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _safe_next_url(candidate: str | None) -> str:
    value = _clean_text(candidate)
    allowed = {url_for("dashboard_page"), url_for("report_page")}
    if value in allowed:
        return value
    return url_for("dashboard_page")


def _set_admin_session(admin: Dict[str, Any]) -> Dict[str, Any]:
    session["admin"] = {
        "admin_id": admin.get("admin_id"),
        "admin_name": admin.get("admin_name"),
        "qr_code": admin.get("qr_code"),
        "authenticated_at": _now_iso()
    }
    return session["admin"]


def _add_event(event: Dict[str, Any]) -> None:
    with scanner_lock:
        scanner_state["last_event"] = dict(event)
        scanner_state["recent_events"].appendleft(dict(event))


def _sync_firestore_movement(result: Dict[str, Any]) -> None:
    if not firestore_mirror.is_connected:
        return
    try:
        firestore_mirror.sync_movement_event(result)
    except Exception:
        # Keep scanner path resilient even if cloud sync fails.
        pass


def _sync_firestore_snapshot(limit: int = 200) -> None:
    if not firestore_mirror.is_connected:
        return
    if not repo.is_connected:
        return
    try:
        snapshot = repo.dashboard_data(limit=limit)
        firestore_mirror.sync_dashboard_snapshot(snapshot)
    except Exception:
        # Keep dashboard APIs resilient even if cloud sync fails.
        pass


def _set_pending_registration(qr_code: str) -> None:
    pending = {
        "qr_code": qr_code,
        "suggested_driver_id": _suggest_driver_id_from_qr(qr_code),
        "seen_at": _now_iso(),
    }
    with scanner_lock:
        scanner_state["pending_registration"] = pending
    _add_event({
        "event_type": "pending_registration",
        "message": "New QR detected. Register this driver once; next scans will auto entry/exit.",
        **pending
    })


def _set_last_scan(qr_code: str) -> None:
    with scanner_lock:
        scanner_state["last_scan"] = qr_code
        scanner_state["last_scan_at"] = _now_iso()


def _snapshot_scanner_state() -> Dict[str, Any]:
    with scanner_lock:
        pending = dict(scanner_state["pending_registration"]) if scanner_state["pending_registration"] else None
        last_event = dict(scanner_state["last_event"]) if scanner_state["last_event"] else None
        last_admin_scan = dict(scanner_state["last_admin_scan"]) if scanner_state["last_admin_scan"] else None
        events = [dict(item) for item in list(scanner_state["recent_events"])]
        return {
            "last_scan": scanner_state["last_scan"],
            "last_scan_at": scanner_state["last_scan_at"],
            "pending_registration": pending,
            "last_event": last_event,
            "last_admin_scan": last_admin_scan,
            "recent_events": events,
        }


def _process_qr_scan(raw_qr: str) -> None:
    qr_code = _clean_text(raw_qr)
    if not qr_code:
        return

    _set_last_scan(qr_code)

    if not repo.is_connected:
        _add_event({
            "event_type": "error",
            "message": "MySQL is not connected. Scan ignored.",
            "qr_code": qr_code,
            "event_time": _now_iso(),
        })
        return

    admin = None
    if ADMIN_QR_LOCK and qr_code == ADMIN_QR_LOCK:
        try:
            admin = repo.get_admin_by_qr(qr_code)
        except Exception as error:
            _add_event({
                "event_type": "error",
                "message": f"Admin lookup failed: {error}",
                "qr_code": qr_code,
                "event_time": _now_iso(),
            })
            return

    if admin:
        admin_scan = {
            "admin_id": admin.get("admin_id"),
            "admin_name": admin.get("admin_name"),
            "qr_code": qr_code,
            "event_time": _now_iso(),
        }
        with scanner_lock:
            scanner_state["last_admin_scan"] = dict(admin_scan)
        _add_event({
            "event_type": "admin_scan",
            "message": f"Admin QR detected: {admin.get('admin_name')} ({admin.get('admin_id')})",
            **admin_scan
        })
        return

    try:
        movement_result = repo.toggle_scan_by_qr(qr_code, note="Auto QR camera scan")
    except Exception as error:
        _add_event({
            "event_type": "error",
            "message": f"Driver movement update failed: {error}",
            "qr_code": qr_code,
            "event_time": _now_iso(),
        })
        return

    if not movement_result.get("registered"):
        _set_pending_registration(qr_code)
        return

    with scanner_lock:
        pending = scanner_state["pending_registration"]
        if pending and pending.get("qr_code") == qr_code:
            scanner_state["pending_registration"] = None

    movement = movement_result.get("movement", "").lower()
    movement_label = "ENTRY" if movement == "entry" else "EXIT"
    _sync_firestore_movement(movement_result)
    _add_event({
        "event_type": "movement",
        "message": f"{movement_label}: {movement_result.get('driver_name')} ({movement_result.get('driver_id')})",
        **movement_result
    })


camera = CameraStream(
    camera_index=CAMERA_INDEX,
    on_qr_scan=_process_qr_scan,
    scan_cooldown_seconds=3.8
)
if SERVER_CAMERA_ENABLED:
    camera.start()
else:
    with camera.lock:
        camera.status = "Server camera disabled. Use Device Camera Scanner."
        camera.last_frame_jpeg = camera._placeholder("Server camera disabled. Use device camera scanner.")


def _is_admin_authenticated() -> bool:
    admin = session.get("admin")
    return isinstance(admin, dict) and bool(admin.get("admin_id"))


def _get_admin_session() -> Dict[str, Any] | None:
    admin = session.get("admin")
    if isinstance(admin, dict) and admin.get("admin_id"):
        return admin
    return None


def admin_api_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_admin_authenticated():
            return _error_response("Admin authentication required.", 401)
        return func(*args, **kwargs)
    return wrapper


def admin_page_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_admin_authenticated():
            return redirect(url_for("admin_login_page", next=request.path))
        return func(*args, **kwargs)
    return wrapper


@app.get("/")
def index():
    return redirect(url_for("scanner_page"))


@app.get("/scanner")
def scanner_page():
    return render_template("scanner.html")


@app.get("/admin/login")
def admin_login_page():
    next_url = _safe_next_url(request.args.get("next"))
    return render_template("admin_login.html", next_url=next_url)


@app.get("/dashboard")
@admin_page_required
def dashboard_page():
    return render_template("dashboard.html")


@app.get("/report")
@admin_page_required
def report_page():
    return render_template("report.html")


@app.get("/video_feed")
def video_feed():
    return Response(
        camera.mjpeg_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "server_camera_enabled": SERVER_CAMERA_ENABLED,
        "camera": camera.get_state(),
        "database": repo.status(),
        "firestore": firestore_mirror.status(),
        "admin_authenticated": _is_admin_authenticated(),
        "admin": _get_admin_session()
    })


@app.get("/api/scanner/state")
def api_scanner_state():
    payload = _snapshot_scanner_state()
    payload["ok"] = True
    payload["server_camera_enabled"] = SERVER_CAMERA_ENABLED
    payload["camera"] = camera.get_state()
    payload["database"] = repo.status()
    payload["firestore"] = firestore_mirror.status()
    return jsonify(payload)


@app.post("/api/scanner/manual-scan")
def api_scanner_manual_scan():
    payload = request.get_json(silent=True) or {}
    qr_code = _clean_text(payload.get("qr_code"))
    if not qr_code:
        return _error_response("QR code is required.", 400)
    _process_qr_scan(qr_code)
    return jsonify({"ok": True, "scanner": _snapshot_scanner_state()})


@app.post("/api/scanner/register")
def api_scanner_register():
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)

    payload = request.get_json(silent=True) or {}
    qr_code = _clean_text(payload.get("qr_code"))
    if not qr_code:
        snapshot = _snapshot_scanner_state()
        pending = snapshot.get("pending_registration")
        if pending:
            qr_code = _clean_text(pending.get("qr_code"))

    registration_payload = {
        "qr_code": qr_code,
        "driver_id": _clean_text(payload.get("driver_id")),
        "driver_name": _clean_text(payload.get("driver_name")),
        "bus_number": _clean_text(payload.get("bus_number")),
        "phone_number": _clean_text(payload.get("phone_number")),
        "note": _clean_text(payload.get("note")) or "Registered from scanner page"
    }

    try:
        result = repo.register_and_record_entry(registration_payload)
    except ValueError as error:
        return _error_response(str(error), 400)
    except Exception as error:
        return _error_response(str(error), 500)

    with scanner_lock:
        pending = scanner_state["pending_registration"]
        if pending and pending.get("qr_code") == qr_code:
            scanner_state["pending_registration"] = None

    _add_event({
        "event_type": "movement",
        "message": f"ENTRY: {result.get('driver_name')} ({result.get('driver_id')}) registered and logged in.",
        **result
    })
    _sync_firestore_movement(result)
    return jsonify({"ok": True, "result": result})


@app.post("/api/admin/login")
def api_admin_login():
    if not ALLOW_MANUAL_ADMIN_LOGIN:
        return _error_response("Manual admin login disabled. Use admin QR scan login.", 403)

    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)

    payload = request.get_json(silent=True) or {}
    qr_code = _clean_text(payload.get("qr_code"))
    next_url = _safe_next_url(payload.get("next"))
    if not qr_code:
        return _error_response("Admin QR code is required.", 400)

    if ADMIN_QR_LOCK and qr_code != ADMIN_QR_LOCK:
        return _error_response("This QR code is not allowed for admin access.", 401)

    try:
        admin = repo.get_admin_by_qr(qr_code)
    except Exception as error:
        return _error_response(str(error), 500)
    if not admin:
        return _error_response("Invalid admin QR code.", 401)

    admin_session = _set_admin_session(admin)
    return jsonify({"ok": True, "admin": admin_session, "next": next_url})


@app.post("/api/admin/login/latest")
def api_admin_login_latest():
    next_url = _safe_next_url((request.get_json(silent=True) or {}).get("next"))
    snapshot = _snapshot_scanner_state()
    latest = snapshot.get("last_admin_scan")
    if not latest:
        return _error_response("No recent admin QR scan detected.", 401)

    scan_time = latest.get("event_time")
    try:
        scan_dt = datetime.fromisoformat(scan_time.replace(" ", "T"))
    except Exception:
        return _error_response("Invalid admin scan timestamp.", 401)

    age_seconds = (datetime.now() - scan_dt).total_seconds()
    if age_seconds > 30:
        return _error_response("Latest admin QR scan expired. Scan admin card again.", 401)
    if ADMIN_QR_LOCK and latest.get("qr_code") != ADMIN_QR_LOCK:
        return _error_response("Latest QR is not authorized for admin access.", 401)

    admin_session = _set_admin_session(latest)
    return jsonify({"ok": True, "admin": admin_session, "next": next_url})


@app.post("/api/admin/login/password")
def api_admin_login_password():
    if not ALLOW_PASSWORD_ADMIN_LOGIN:
        return _error_response("Username/password admin login is disabled.", 403)
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)

    payload = request.get_json(silent=True) or {}
    username = _clean_text(payload.get("username")).lower()
    password = _clean_text(payload.get("password"))
    next_url = _safe_next_url(payload.get("next"))
    if not username:
        return _error_response("Username is required.", 400)
    if not password:
        return _error_response("Password is required.", 400)

    try:
        admin = repo.authenticate_admin(username, password)
    except Exception as error:
        return _error_response(str(error), 500)
    if not admin:
        return _error_response("Invalid username or password.", 401)

    admin_session = _set_admin_session(admin)
    return jsonify({"ok": True, "admin": admin_session, "next": next_url})


@app.get("/api/admin/session")
def api_admin_session():
    return jsonify({
        "ok": True,
        "authenticated": _is_admin_authenticated(),
        "admin": _get_admin_session()
    })


@app.post("/api/admin/logout")
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.get("/api/dashboard")
@admin_api_required
def api_dashboard():
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)

    try:
        data = repo.dashboard_data(limit=120)
    except Exception as error:
        return _error_response(str(error), 500)

    data["camera"] = camera.get_state()
    data["database"] = repo.status()
    data["firestore"] = firestore_mirror.status()
    data["ok"] = True
    return jsonify(data)


@app.get("/api/report/daily")
@admin_api_required
def api_report_daily():
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)

    report_date = _clean_text(request.args.get("date"))
    try:
        report = repo.daily_report(report_date if report_date else None)
    except ValueError as error:
        return _error_response(str(error), 400)
    except Exception as error:
        return _error_response(str(error), 500)

    report["ok"] = True
    report["camera"] = camera.get_state()
    report["database"] = repo.status()
    report["firestore"] = firestore_mirror.status()
    return jsonify(report)


@app.post("/api/reset")
@admin_api_required
def api_reset():
    if ADMIN_VIEW_ONLY:
        return _error_response("Dashboard is in view-only mode.", 403)
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)
    try:
        result = repo.clear_session_data()
    except Exception as error:
        return _error_response(str(error), 500)
    firestore_mirror.clear_runtime_data()
    return jsonify(result)


@app.delete("/api/movement-log/<int:log_id>")
@admin_api_required
def api_delete_movement_log(log_id: int):
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)
    try:
        result = repo.delete_movement_log(log_id)
    except ValueError as error:
        return _error_response(str(error), 400)
    except Exception as error:
        return _error_response(str(error), 500)
    deleted_id = result.get("deleted_id")
    if deleted_id:
        firestore_mirror.delete_movement_log(deleted_id)
    _sync_firestore_snapshot(limit=240)
    return jsonify(result)


@app.post("/api/movement-logs/clear")
@admin_api_required
def api_clear_movement_logs():
    if not repo.is_connected:
        return _error_response(repo.status().get("error") or "MySQL not connected.", 500)
    try:
        result = repo.clear_logs_and_active_entries()
    except Exception as error:
        return _error_response(str(error), 500)
    firestore_mirror.clear_runtime_data()
    return jsonify(result)


@app.post("/api/camera/restart")
def api_camera_restart():
    if not SERVER_CAMERA_ENABLED:
        return _error_response("Server camera is disabled in cloud mode.", 400)
    payload = request.get_json(silent=True) or {}
    camera_index = payload.get("camera_index")
    camera.restart(camera_index=camera_index)
    return jsonify({"ok": True, "camera": camera.get_state()})


@app.post("/api/database/reconnect")
def api_database_reconnect():
    status = repo.reconnect()
    return jsonify({"ok": True, "database": status})


@app.post("/api/firestore/reconnect")
def api_firestore_reconnect():
    status = firestore_mirror.reconnect()
    return jsonify({"ok": True, "firestore": status})


@atexit.register
def _shutdown():
    camera.stop()


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, threaded=True)
