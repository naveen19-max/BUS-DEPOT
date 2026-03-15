(function () {
  let refreshTimer = null;
  let loading = false;
  let lastSeenLogId = 0;
  let isInitialLogSync = false;
  let islandTimer = null;

  function qs(id) {
    return document.getElementById(id);
  }

  function toLocalTime(value) {
    if (!value) {
      return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString();
  }

  function showToast(message, type) {
    const toast = qs("toast");
    if (!toast) {
      return;
    }
    toast.textContent = message;
    toast.style.background = type === "error" ? "rgba(130, 24, 24, 0.92)" : "rgba(8, 22, 39, 0.92)";
    toast.classList.add("show");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 2800);
  }

  function askNotificationPermission() {
    if (!("Notification" in window)) {
      return;
    }
    if (Notification.permission === "default") {
      Notification.requestPermission().catch(function () {
        // ignore
      });
    }
  }

  function browserNotify(title, subtitle) {
    if (!("Notification" in window)) {
      return;
    }
    if (Notification.permission === "granted") {
      try {
        new Notification(title, { body: subtitle });
      } catch (_error) {
        // ignore
      }
    }
  }

  function showDynamicIsland(type, title, subtitle) {
    const island = qs("dynamicIsland");
    const titleEl = qs("islandTitle");
    const subtitleEl = qs("islandSubtitle");
    if (!island || !titleEl || !subtitleEl) {
      return;
    }
    island.classList.remove("entry", "exit");
    if (type === "entry" || type === "exit") {
      island.classList.add(type);
    }
    titleEl.textContent = title;
    subtitleEl.textContent = subtitle;
    island.classList.add("show");
    window.clearTimeout(islandTimer);
    islandTimer = window.setTimeout(function () {
      island.classList.remove("show", "entry", "exit");
    }, 2800);
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || !data.ok) {
      if (response.status === 401) {
        window.location.href = `/admin/login?next=${encodeURIComponent("/dashboard")}`;
      }
      throw new Error(data.error || "Request failed.");
    }
    return data;
  }

  function updateStatus(database, camera, firestore) {
    const dbChip = qs("dbChip");
    const cameraChip = qs("cameraChip");
    const firestoreChip = qs("firestoreChip");
    if (dbChip) {
      if (database && database.connected) {
        dbChip.textContent = `MySQL: connected (${database.database})`;
        dbChip.style.color = "#0d6c45";
      } else {
        dbChip.textContent = "MySQL: disconnected";
        dbChip.style.color = "#932727";
      }
    }
    if (cameraChip) {
      const status = (camera && camera.camera_status) || "unknown";
      cameraChip.textContent = `Camera: ${status}`;
      cameraChip.style.color = status.toLowerCase().includes("connected") ? "#0d6c45" : "#8b5a18";
    }
    if (firestoreChip) {
      const enabled = !!(firestore && firestore.enabled);
      const connected = !!(firestore && firestore.connected);
      if (!enabled) {
        firestoreChip.textContent = "Firestore: disabled";
        firestoreChip.style.color = "#8b5a18";
      } else if (connected) {
        const project = (firestore && firestore.project_id) || "project";
        firestoreChip.textContent = `Firestore: connected (${project})`;
        firestoreChip.style.color = "#0d6c45";
      } else {
        firestoreChip.textContent = "Firestore: disconnected";
        firestoreChip.style.color = "#932727";
      }
    }
  }

  function renderActive(rows) {
    const body = qs("activeTable");
    if (!body) {
      return;
    }
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No drivers currently inside depot.</td></tr>';
      return;
    }
    body.innerHTML = rows.map(function (row) {
      return `
        <tr>
          <td>${row.driver_name || "-"}</td>
          <td>${row.driver_id || "-"}</td>
          <td>${row.bus_number || "-"}</td>
          <td>${row.phone_number || "-"}</td>
          <td>${toLocalTime(row.entry_time)}</td>
        </tr>
      `;
    }).join("");
  }

  function movementTag(movement) {
    if ((movement || "").toLowerCase() === "entry") {
      return '<span class="tag tag-entry">ENTRY</span>';
    }
    return '<span class="tag tag-exit">EXIT</span>';
  }

  function renderLogs(rows) {
    const body = qs("logsTable");
    if (!body) {
      return;
    }
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">No movement logs yet.</td></tr>';
      return;
    }
    body.innerHTML = rows.map(function (row) {
      const logId = Number(row.id || 0);
      return `
        <tr>
          <td>${movementTag(row.movement)}</td>
          <td>${row.driver_name || "-"}</td>
          <td>${row.driver_id || "-"}</td>
          <td>${row.bus_number || "-"}</td>
          <td>${row.phone_number || "-"}</td>
          <td>${toLocalTime(row.event_time)}</td>
          <td>${logId > 0 ? `<button type="button" class="ghost-btn danger-ghost tiny-btn" data-log-id="${logId}">Delete</button>` : "-"}</td>
        </tr>
      `;
    }).join("");
  }

  function updateCounts(counts) {
    qs("insideCount").textContent = counts.inside || 0;
    qs("entriesToday").textContent = counts.entries_today || 0;
    qs("exitsToday").textContent = counts.exits_today || 0;
    const totalLogs = qs("totalLogs");
    if (totalLogs) {
      totalLogs.textContent = (counts.entries_total || 0) + (counts.exits_total || 0);
    }
  }

  function notifyForNewLogs(rows) {
    if (!rows || !rows.length) {
      return;
    }
    const topId = Number(rows[0].id || 0);
    if (!isInitialLogSync) {
      isInitialLogSync = true;
      lastSeenLogId = topId;
      return;
    }

    const freshRows = rows
      .filter(function (row) { return Number(row.id || 0) > lastSeenLogId; })
      .sort(function (a, b) { return Number(a.id || 0) - Number(b.id || 0); });
    lastSeenLogId = Math.max(lastSeenLogId, topId);

    if (!freshRows.length) {
      return;
    }
    const latest = freshRows[freshRows.length - 1];
    const movement = (latest.movement || "").toLowerCase();
    const isEntry = movement === "entry";
    const title = isEntry ? "Driver Entry Detected" : "Driver Exit Detected";
    const subtitle = `${latest.driver_name || "-"} (${latest.driver_id || "-"}) | ${latest.bus_number || "-"}`;
    showDynamicIsland(isEntry ? "entry" : "exit", title, subtitle);
    browserNotify(title, subtitle);
  }

  async function refreshDashboard() {
    if (loading) {
      return;
    }
    loading = true;
    try {
      const data = await requestJson("/api/dashboard", { cache: "no-store" });
      updateCounts(data.counts || {});
      updateStatus(data.database, data.camera, data.firestore);
      renderActive(data.active_entries || []);
      renderLogs(data.recent_logs || []);
      notifyForNewLogs(data.recent_logs || []);
    } catch (error) {
      showToast(error.message || "Dashboard refresh failed.", "error");
    } finally {
      loading = false;
    }
  }

  async function loadAdminLabel() {
    try {
      await requestJson("/api/admin/session", { cache: "no-store" });
      const label = qs("adminLabel");
      if (label) {
        label.textContent = "Admin monitoring panel.";
      }
    } catch (_error) {
      // handled in requestJson
    }
  }

  function bindActions() {
    const refreshBtn = qs("refreshBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", refreshDashboard);
    }

    const reconnectDbBtn = qs("reconnectDbBtn");
    if (reconnectDbBtn) {
      reconnectDbBtn.addEventListener("click", async function () {
        try {
          const payload = await requestJson("/api/database/reconnect", { method: "POST" });
          const database = payload.database || {};
          if (database.connected) {
            showToast(`MySQL reconnected (${database.database || "bus_depot"}).`, "success");
          } else {
            showToast(database.error || "MySQL reconnect failed.", "error");
          }
          await refreshDashboard();
        } catch (error) {
          showToast(error.message || "Unable to reconnect MySQL.", "error");
        }
      });
    }

    const reconnectFirestoreBtn = qs("reconnectFirestoreBtn");
    if (reconnectFirestoreBtn) {
      reconnectFirestoreBtn.addEventListener("click", async function () {
        try {
          const payload = await requestJson("/api/firestore/reconnect", { method: "POST" });
          const firestore = payload.firestore || {};
          if (firestore.connected) {
            showToast(`Firestore reconnected (${firestore.project_id || "project"}).`, "success");
          } else {
            showToast(firestore.error || "Firestore reconnect failed.", "error");
          }
          await refreshDashboard();
        } catch (error) {
          showToast(error.message || "Unable to reconnect Firestore.", "error");
        }
      });
    }

    const restartCameraBtn = qs("restartCameraBtn");
    if (restartCameraBtn) {
      restartCameraBtn.addEventListener("click", async function () {
        try {
          await requestJson("/api/camera/restart", { method: "POST" });
          showToast("Camera restarted. Checking stream status...", "success");
          await refreshDashboard();
        } catch (error) {
          showToast(error.message || "Unable to restart camera.", "error");
        }
      });
    }

    const clearTotalLogsBtn = qs("clearTotalLogsBtn");
    if (clearTotalLogsBtn) {
      clearTotalLogsBtn.addEventListener("click", async function () {
        const ok = window.confirm("Delete all movement logs and reset current drivers inside?");
        if (!ok) {
          return;
        }
        try {
          await requestJson("/api/movement-logs/clear", { method: "POST" });
          lastSeenLogId = 0;
          isInitialLogSync = false;
          showToast("Total movement logs deleted and inside count reset.", "success");
          await refreshDashboard();
        } catch (error) {
          showToast(error.message || "Unable to clear movement logs.", "error");
        }
      });
    }

    const logoutBtn = qs("logoutBtn");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", async function () {
        try {
          await requestJson("/api/admin/logout", { method: "POST" });
          window.location.href = "/admin/login";
        } catch (error) {
          showToast(error.message || "Logout failed.", "error");
        }
      });
    }

    const logsTable = qs("logsTable");
    if (logsTable) {
      logsTable.addEventListener("click", async function (event) {
        const button = event.target.closest("button[data-log-id]");
        if (!button) {
          return;
        }
        const logId = Number(button.getAttribute("data-log-id"));
        if (!logId) {
          return;
        }
        const ok = window.confirm("Delete this movement log from dashboard and report?");
        if (!ok) {
          return;
        }
        try {
          await requestJson(`/api/movement-log/${logId}`, { method: "DELETE" });
          showToast("Movement log deleted.", "success");
          await refreshDashboard();
        } catch (error) {
          showToast(error.message || "Unable to delete movement log.", "error");
        }
      });
    }
  }

  function startPolling() {
    window.clearInterval(refreshTimer);
    refreshTimer = window.setInterval(refreshDashboard, 3200);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    askNotificationPermission();
    bindActions();
    await loadAdminLabel();
    await refreshDashboard();
    startPolling();
  });
})();
