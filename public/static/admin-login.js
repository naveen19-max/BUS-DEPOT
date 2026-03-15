(function () {
  let refreshTimer = null;
  let autoTriedForScan = "";
  let preferredTarget = "/dashboard";

  function qs(id) {
    return document.getElementById(id);
  }

  function sanitizeText(value) {
    return (value || "").trim();
  }

  function safeNext(value) {
    const clean = sanitizeText(value);
    if (clean === "/report") {
      return "/report";
    }
    return "/dashboard";
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
      throw new Error(data.error || "Request failed.");
    }
    return data;
  }

  function setPreferredTarget(nextPath) {
    preferredTarget = safeNext(nextPath);
    const openDashboardBtn = qs("openDashboardBtn");
    const openReportBtn = qs("openReportBtn");
    if (openDashboardBtn && openReportBtn) {
      if (preferredTarget === "/report") {
        openDashboardBtn.classList.remove("is-active");
        openReportBtn.classList.add("is-active");
      } else {
        openReportBtn.classList.remove("is-active");
        openDashboardBtn.classList.add("is-active");
      }
    }
  }

  async function performLoginWithLatest(showError) {
    try {
      const data = await requestJson("/api/admin/login/latest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ next: preferredTarget })
      });
      showToast("Admin QR verified. Opening page...", "success");
      window.location.href = safeNext(data.next || preferredTarget);
    } catch (error) {
      if (showError) {
        showToast(error.message || "Latest scan login failed.", "error");
      }
    }
  }

  async function checkSession() {
    try {
      const data = await requestJson("/api/admin/session", { cache: "no-store" });
      if (data.authenticated) {
        window.location.href = preferredTarget;
      }
    } catch (_error) {
      // no-op
    }
  }

  async function refreshState() {
    try {
      const data = await requestJson("/api/scanner/state", { cache: "no-store" });
      const latest = data.last_admin_scan;
      const latestScan = qs("latestAdminScan");
      const latestScanAt = qs("latestAdminScanAt");
      const hint = qs("adminScanHint");

      if (!latest) {
        if (latestScan) {
          latestScan.textContent = "No admin scan detected.";
        }
        if (latestScanAt) {
          latestScanAt.textContent = "-";
        }
        if (hint) {
          hint.textContent = "Waiting for admin QR scan...";
        }
        return;
      }

      if (latestScan) {
        latestScan.textContent = "Authorized admin card detected";
      }
      if (latestScanAt) {
        latestScanAt.textContent = toLocalTime(latest.event_time);
      }
      if (hint) {
        hint.textContent = `Admin QR detected. Opening ${preferredTarget === "/report" ? "report" : "dashboard"} if authorized.`;
      }

      if (latest.event_time && autoTriedForScan !== latest.event_time) {
        autoTriedForScan = latest.event_time;
        await performLoginWithLatest(false);
      }
    } catch (error) {
      showToast(error.message || "Unable to read scanner state.", "error");
    }
  }

  function bindEvents() {
    const openDashboardBtn = qs("openDashboardBtn");
    if (openDashboardBtn) {
      openDashboardBtn.addEventListener("click", function () {
        setPreferredTarget("/dashboard");
        performLoginWithLatest(true);
      });
    }

    const openReportBtn = qs("openReportBtn");
    if (openReportBtn) {
      openReportBtn.addEventListener("click", function () {
        setPreferredTarget("/report");
        performLoginWithLatest(true);
      });
    }

    const adminPasswordForm = qs("adminPasswordForm");
    if (adminPasswordForm) {
      adminPasswordForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        const payload = Object.fromEntries(new FormData(adminPasswordForm).entries());
        payload.username = sanitizeText(payload.username).toLowerCase();
        payload.password = sanitizeText(payload.password);
        payload.next = preferredTarget;
        if (!payload.username || !payload.password) {
          showToast("Enter admin username and password.", "error");
          return;
        }
        try {
          const data = await requestJson("/api/admin/login/password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          showToast("Admin credentials verified. Opening page...", "success");
          window.location.href = safeNext(data.next || preferredTarget);
        } catch (error) {
          showToast(error.message || "Username/password login failed.", "error");
        }
      });
    }
  }

  function startPolling() {
    window.clearInterval(refreshTimer);
    refreshTimer = window.setInterval(refreshState, 2000);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    const hiddenNext = qs("nextUrl");
    setPreferredTarget(hiddenNext ? hiddenNext.value : "/dashboard");
    bindEvents();
    await checkSession();
    await refreshState();
    startPolling();
  });
})();
