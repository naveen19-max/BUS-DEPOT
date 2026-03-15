(function () {
  const state = {
    refreshTimer: null,
    latestLogsCount: 0
  };

  function sanitizeId(value) {
    return (value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function sanitizeText(value) {
    return (value || "").trim();
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = value;
    }
  }

  function showToast(message, type) {
    const toast = document.getElementById("toast");
    if (!toast) {
      return;
    }
    toast.textContent = message;
    toast.style.background = type === "error" ? "rgba(142, 23, 23, 0.92)" : "rgba(9, 24, 46, 0.9)";
    toast.classList.add("show");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 2800);
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

  function renderActiveTable(rows) {
    const body = document.getElementById("activeTable");
    if (!body) {
      return;
    }
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No active drivers inside depot.</td></tr>';
      return;
    }

    body.innerHTML = rows.map(function (row) {
      return `
        <tr>
          <td>${row.driver_id || "-"}</td>
          <td>${row.driver_name || "-"}</td>
          <td>${row.bus_number || "-"}</td>
          <td>${row.phone_number || "-"}</td>
          <td>${toLocalTime(row.entry_time)}</td>
        </tr>
      `;
    }).join("");
  }

  function renderLogsTable(rows) {
    const body = document.getElementById("logsTable");
    if (!body) {
      return;
    }
    state.latestLogsCount = rows.length;

    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">No movement logs yet.</td></tr>';
      return;
    }

    body.innerHTML = rows.map(function (row) {
      const tagClass = row.movement === "entry" ? "tag-entry" : "tag-exit";
      const tagText = row.movement === "entry" ? "ENTRY" : "EXIT";
      return `
        <tr>
          <td><span class="${tagClass}">${tagText}</span></td>
          <td>${row.driver_id || "-"}</td>
          <td>${row.driver_name || "-"}</td>
          <td>${row.bus_number || "-"}</td>
          <td>${row.phone_number || "-"}</td>
          <td>${toLocalTime(row.event_time)}</td>
        </tr>
      `;
    }).join("");
  }

  function updateDatabaseChip(db) {
    const chip = document.getElementById("dbStatus");
    if (!chip) {
      return;
    }
    if (db && db.connected) {
      chip.textContent = `MySQL: connected (${db.database})`;
      chip.style.color = "#0e5f42";
    } else {
      chip.textContent = `MySQL: disconnected${db && db.error ? " | " + db.error : ""}`;
      chip.style.color = "#852323";
    }
  }

  function updateCameraChip(camera) {
    const chip = document.getElementById("cameraStatus");
    if (!chip) {
      return;
    }
    if (!camera) {
      chip.textContent = "Camera: status unavailable";
      return;
    }
    chip.textContent = `Camera ${camera.camera_index}: ${camera.camera_status || "unknown"}`;
    chip.style.color = (camera.camera_status || "").toLowerCase().includes("connected") ? "#0e5f42" : "#7a3f12";
  }

  async function fetchDriverProfile(driverId) {
    const clean = sanitizeId(driverId);
    if (!clean) {
      return null;
    }
    const response = await fetch(`/api/driver/${encodeURIComponent(clean)}`, { cache: "no-store" });
    const data = await response.json();
    if (!data.ok || !data.found) {
      return null;
    }
    return data.driver;
  }

  async function refreshDashboard() {
    try {
      const response = await fetch("/api/dashboard", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Unable to load dashboard.");
      }

      setText("insideCount", data.counts.inside || 0);
      setText("entriesToday", data.counts.entries_today || 0);
      setText("exitsToday", data.counts.exits_today || 0);
      setText("totalLogs", (data.counts.entries_total || 0) + (data.counts.exits_total || 0));

      updateDatabaseChip(data.database);
      updateCameraChip(data.camera);
      renderActiveTable(data.active_entries || []);
      renderLogsTable(data.recent_logs || []);
    } catch (error) {
      showToast(error.message || "Dashboard refresh failed.", "error");
    }
  }

  function serializeForm(form) {
    const fields = Object.fromEntries(new FormData(form).entries());
    fields.driver_id = sanitizeId(fields.driver_id);
    fields.driver_name = sanitizeText(fields.driver_name);
    fields.bus_number = sanitizeText(fields.bus_number).toUpperCase();
    fields.phone_number = sanitizeText(fields.phone_number);
    fields.note = sanitizeText(fields.note);
    return fields;
  }

  function bindDriverAutofill() {
    const entryForm = document.getElementById("entryForm");
    if (!entryForm) {
      return;
    }

    const idInput = entryForm.elements.driver_id;
    idInput.addEventListener("blur", async function () {
      const driverId = sanitizeId(idInput.value);
      if (!driverId) {
        return;
      }
      idInput.value = driverId;

      try {
        const profile = await fetchDriverProfile(driverId);
        if (!profile) {
          return;
        }
        entryForm.elements.driver_name.value = profile.driver_name || "";
        entryForm.elements.bus_number.value = profile.bus_number || "";
        entryForm.elements.phone_number.value = profile.phone_number || "";
      } catch (error) {
        showToast("Driver lookup failed.", "error");
      }
    });
  }

  function bindEntryForm() {
    const form = document.getElementById("entryForm");
    if (!form) {
      return;
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = serializeForm(form);

      if (!payload.driver_id || !payload.driver_name || !payload.bus_number || !payload.phone_number) {
        showToast("Please fill driver ID, name, bus number and phone.", "error");
        return;
      }

      try {
        const response = await fetch("/api/entry", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Entry save failed.");
        }
        showToast(`Entry recorded: ${data.driver_name} (${data.driver_id})`, "success");
        form.reset();
        await refreshDashboard();
      } catch (error) {
        showToast(error.message || "Entry save failed.", "error");
      }
    });
  }

  function bindExitForm() {
    const form = document.getElementById("exitForm");
    if (!form) {
      return;
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = serializeForm(form);

      if (!payload.driver_id) {
        showToast("Driver ID is required for exit.", "error");
        return;
      }

      try {
        const response = await fetch("/api/exit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
          throw new Error(data.error || "Exit save failed.");
        }
        showToast(`Exit recorded: ${data.driver_name} (${data.driver_id})`, "success");
        form.reset();
        await refreshDashboard();
      } catch (error) {
        showToast(error.message || "Exit save failed.", "error");
      }
    });
  }

  function bindActions() {
    const refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", refreshDashboard);
    }

    const restartCameraBtn = document.getElementById("restartCameraBtn");
    if (restartCameraBtn) {
      restartCameraBtn.addEventListener("click", async function () {
        try {
          await fetch("/api/camera/restart", { method: "POST" });
          showToast("Camera restart requested.", "success");
          await refreshDashboard();
        } catch (error) {
          showToast("Failed to restart camera.", "error");
        }
      });
    }

    const clearBtn = document.getElementById("clearSessionBtn");
    if (clearBtn) {
      clearBtn.addEventListener("click", async function () {
        const confirmed = window.confirm("Clear all session movement logs and active entries?");
        if (!confirmed) {
          return;
        }

        try {
          const response = await fetch("/api/reset", { method: "POST" });
          const data = await response.json();
          if (!response.ok || !data.ok) {
            throw new Error(data.error || "Unable to clear session.");
          }
          showToast("Session logs cleared.", "success");
          await refreshDashboard();
        } catch (error) {
          showToast(error.message || "Unable to clear session.", "error");
        }
      });
    }
  }

  function startAutoRefresh() {
    window.clearInterval(state.refreshTimer);
    state.refreshTimer = window.setInterval(refreshDashboard, 3000);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    bindDriverAutofill();
    bindEntryForm();
    bindExitForm();
    bindActions();
    await refreshDashboard();
    startAutoRefresh();
  });
})();
