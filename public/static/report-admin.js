(function () {
  const state = {
    reportDate: "",
    summary: {},
    hourly: [],
    flowchart: [],
    events: [],
    filteredFlowchart: [],
    filteredEvents: []
  };

  function qs(id) {
    return document.getElementById(id);
  }

  function normalize(value) {
    return (value || "").toString().trim().toLowerCase();
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

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || !data.ok) {
      if (response.status === 401) {
        window.location.href = `/admin/login?next=${encodeURIComponent("/report")}`;
      }
      throw new Error(data.error || "Request failed.");
    }
    return data;
  }

  function setSummary(summary) {
    qs("entriesCount").textContent = summary.entries || 0;
    qs("exitsCount").textContent = summary.exits || 0;
    qs("insideEstimate").textContent = summary.inside_end_of_day_estimate || 0;
    qs("insideNow").textContent = summary.inside_now || 0;
  }

  function renderHourly(hourlyRows) {
    const box = qs("hourlyChart");
    if (!box) {
      return;
    }
    if (!hourlyRows.length) {
      box.innerHTML = '<div class="empty">No activity for this day.</div>';
      return;
    }

    const maxValue = hourlyRows.reduce(function (max, row) {
      return Math.max(max, Number(row.entries || 0), Number(row.exits || 0));
    }, 1);

    box.innerHTML = hourlyRows.map(function (row) {
      const hour = Number(row.hour_of_day || 0);
      const label = `${String(hour).padStart(2, "0")}:00`;
      const entries = Number(row.entries || 0);
      const exits = Number(row.exits || 0);
      const entryWidth = Math.max(3, Math.round((entries / maxValue) * 100));
      const exitWidth = Math.max(3, Math.round((exits / maxValue) * 100));
      return `
        <div class="hour-row">
          <div class="hour-label">${label}</div>
          <div class="hour-bars">
            <div class="bar bar-entry"><span style="width:${entryWidth}%"></span></div>
            <div class="bar bar-exit"><span style="width:${exitWidth}%"></span></div>
          </div>
        </div>
      `;
    }).join("");
  }

  function renderFlow(flowRows) {
    const box = qs("flowList");
    if (!box) {
      return;
    }
    if (!flowRows.length) {
      box.innerHTML = '<div class="empty">No driver flow records for this filter.</div>';
      return;
    }
    box.innerHTML = flowRows.map(function (row) {
      const firstEntry = row.first_entry ? toLocalTime(row.first_entry) : "No entry";
      const lastExit = row.last_exit ? toLocalTime(row.last_exit) : "No exit";
      return `
        <div class="flow-item">
          <strong>${row.driver_name || "-"} (${row.driver_id || "-"})</strong>
          <div>${row.bus_number || "-"} | ${row.phone_number || "-"}</div>
          <div class="flow-route">
            <span>${firstEntry}</span>
            <span class="flow-arrow">&#8594;</span>
            <span>${lastExit}</span>
          </div>
          <div class="flow-route">
            <span>Entries: ${row.entry_count || 0}</span>
            <span>Exits: ${row.exit_count || 0}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  function movementTag(movement) {
    if ((movement || "").toLowerCase() === "entry") {
      return '<span class="tag tag-entry">ENTRY</span>';
    }
    return '<span class="tag tag-exit">EXIT</span>';
  }

  function renderEvents(events) {
    const body = qs("reportEvents");
    if (!body) {
      return;
    }
    if (!events.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">No movement logs for this filter.</td></tr>';
      return;
    }
    body.innerHTML = events.map(function (event) {
      return `
        <tr>
          <td>${toLocalTime(event.event_time)}</td>
          <td>${movementTag(event.movement)}</td>
          <td>${event.driver_name || "-"}</td>
          <td>${event.driver_id || "-"}</td>
          <td>${event.bus_number || "-"}</td>
          <td>${event.phone_number || "-"}</td>
          <td>${event.note || "-"}</td>
        </tr>
      `;
    }).join("");
  }

  function applyFilters() {
    const query = normalize(qs("reportSearch") ? qs("reportSearch").value : "");
    if (!query) {
      state.filteredFlowchart = state.flowchart.slice();
      state.filteredEvents = state.events.slice();
    } else {
      state.filteredFlowchart = state.flowchart.filter(function (row) {
        return [
          row.driver_name,
          row.driver_id,
          row.bus_number,
          row.phone_number
        ].some(function (field) {
          return normalize(field).includes(query);
        });
      });
      state.filteredEvents = state.events.filter(function (event) {
        return [
          event.driver_name,
          event.driver_id,
          event.bus_number,
          event.phone_number,
          event.note
        ].some(function (field) {
          return normalize(field).includes(query);
        });
      });
    }
    renderFlow(state.filteredFlowchart);
    renderEvents(state.filteredEvents);
  }

  async function loadAdminLabel() {
    try {
      await requestJson("/api/admin/session", { cache: "no-store" });
      const label = qs("adminLabel");
      if (label) {
        label.textContent = "Admin view-only mode for daily analytics.";
      }
    } catch (_error) {
      // handled globally
    }
  }

  async function loadReport(dateValue) {
    try {
      const data = await requestJson(`/api/report/daily?date=${encodeURIComponent(dateValue)}`, { cache: "no-store" });
      state.reportDate = data.report_date || dateValue;
      state.summary = data.summary || {};
      state.hourly = data.hourly || [];
      state.flowchart = data.flowchart || [];
      state.events = data.events || [];

      setSummary(state.summary);
      renderHourly(state.hourly);
      applyFilters();
    } catch (error) {
      showToast(error.message || "Report load failed.", "error");
    }
  }

  function bindForm() {
    const form = qs("reportFilterForm");
    if (!form) {
      return;
    }
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const dateValue = qs("reportDate").value;
      if (!dateValue) {
        showToast("Select a report date.", "error");
        return;
      }
      await loadReport(dateValue);
    });
  }

  function bindSearch() {
    const search = qs("reportSearch");
    if (!search) {
      return;
    }
    search.addEventListener("input", function () {
      applyFilters();
    });
  }

  function csvEscape(value) {
    const text = (value == null ? "" : String(value));
    if (text.includes(",") || text.includes('"') || text.includes("\n")) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  }

  function downloadCsv() {
    const rows = state.filteredEvents.length ? state.filteredEvents : state.events;
    if (!rows.length) {
      showToast("No report data to export.", "error");
      return;
    }
    const header = [
      "event_time",
      "movement",
      "driver_name",
      "driver_id",
      "bus_number",
      "phone_number",
      "note"
    ];
    const csvRows = [header.join(",")];
    rows.forEach(function (row) {
      csvRows.push([
        csvEscape(row.event_time || ""),
        csvEscape((row.movement || "").toUpperCase()),
        csvEscape(row.driver_name || ""),
        csvEscape(row.driver_id || ""),
        csvEscape(row.bus_number || ""),
        csvEscape(row.phone_number || ""),
        csvEscape(row.note || "")
      ].join(","));
    });
    const blob = new Blob([csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const datePart = state.reportDate || "day-report";
    link.href = url;
    link.download = `bus-depot-report-${datePart}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function bindExport() {
    const btn = qs("downloadReportBtn");
    if (!btn) {
      return;
    }
    btn.addEventListener("click", function () {
      downloadCsv();
    });
  }

  function bindLogout() {
    const btn = qs("logoutBtn");
    if (!btn) {
      return;
    }
    btn.addEventListener("click", async function () {
      try {
        await requestJson("/api/admin/logout", { method: "POST" });
        window.location.href = "/admin/login";
      } catch (error) {
        showToast(error.message || "Logout failed.", "error");
      }
    });
  }

  function setToday() {
    const input = qs("reportDate");
    if (!input) {
      return "";
    }
    const now = new Date();
    const today = now.toISOString().slice(0, 10);
    input.value = today;
    return today;
  }

  document.addEventListener("DOMContentLoaded", async function () {
    bindForm();
    bindSearch();
    bindExport();
    bindLogout();
    await loadAdminLabel();
    const today = setToday();
    if (today) {
      await loadReport(today);
    }
  });
})();
