(function () {
  let refreshTimer = null;
  let loading = false;
  let eventInitDone = false;
  let lastEventKey = "";
  let lastAdminAutoLoginKey = "";
  let islandTimer = null;
  let deviceStream = null;
  let deviceScanTimer = null;
  let barcodeDetector = null;
  let deviceScanBusy = false;
  let lastDeviceCode = "";
  let lastDeviceCodeAt = 0;

  function qs(id) {
    return document.getElementById(id);
  }

  function sanitizeId(value) {
    return (value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }

  function sanitizeText(value) {
    return (value || "").trim();
  }

  function sanitizePhone(value) {
    const raw = sanitizeText(value);
    if (raw.startsWith("+")) {
      return `+${raw.slice(1).replace(/\D/g, "")}`;
    }
    return raw.replace(/\D/g, "");
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

  function makeEventKey(event) {
    if (!event) {
      return "";
    }
    return [
      event.event_time || event.seen_at || "",
      event.event_type || "",
      event.movement || "",
      event.driver_id || "",
      event.admin_id || "",
      event.qr_code || ""
    ].join("|");
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Request failed.");
    }
    return data;
  }

  async function handleLastEvent(lastEvent) {
    const key = makeEventKey(lastEvent);
    if (!eventInitDone) {
      eventInitDone = true;
      lastEventKey = key;
      return;
    }
    if (!key || key === lastEventKey) {
      return;
    }
    lastEventKey = key;
    if (!lastEvent) {
      return;
    }

    if (lastEvent.event_type === "admin_scan") {
      const adminKey = `${lastEvent.event_time || ""}|${lastEvent.qr_code || ""}`;
      if (adminKey && adminKey !== lastAdminAutoLoginKey) {
        lastAdminAutoLoginKey = adminKey;
        try {
          const data = await requestJson("/api/admin/login/latest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ next: "/dashboard" })
          });
          showDynamicIsland("entry", "Admin Access Granted", "Opening dashboard...");
          browserNotify("Admin Access Granted", "Opening dashboard...");
          window.location.href = data.next || "/dashboard";
        } catch (_error) {
          // Ignore and remain on scanner page.
        }
      }
      return;
    }

    if (lastEvent.event_type === "movement") {
      const movement = (lastEvent.movement || "").toLowerCase();
      const isEntry = movement === "entry";
      const title = isEntry ? "Driver Entry Detected" : "Driver Exit Detected";
      const subtitle = `${lastEvent.driver_name || "-"} (${lastEvent.driver_id || "-"}) | ${lastEvent.bus_number || "-"}`;
      showDynamicIsland(isEntry ? "entry" : "exit", title, subtitle);
      browserNotify(title, subtitle);
    }
  }

  function updateStatusChips(data) {
    const dbChip = qs("dbChip");
    const cameraChip = qs("cameraChip");
    const firestoreChip = qs("firestoreChip");
    if (dbChip) {
      if (data.database && data.database.connected) {
        dbChip.textContent = `MySQL: connected (${data.database.database})`;
        dbChip.style.color = "#0d6c45";
      } else {
        dbChip.textContent = "MySQL: disconnected";
        dbChip.style.color = "#932727";
      }
    }
    if (cameraChip) {
      const status = (data.camera && data.camera.camera_status) || "unknown";
      cameraChip.textContent = `Camera: ${status}`;
      cameraChip.style.color = status.toLowerCase().includes("connected") ? "#0d6c45" : "#8b5a18";
    }
    if (firestoreChip) {
      const enabled = !!(data.firestore && data.firestore.enabled);
      const connected = !!(data.firestore && data.firestore.connected);
      if (!enabled) {
        firestoreChip.textContent = "Firestore: disabled";
        firestoreChip.style.color = "#8b5a18";
      } else if (connected) {
        const project = (data.firestore && data.firestore.project_id) || "project";
        firestoreChip.textContent = `Firestore: connected (${project})`;
        firestoreChip.style.color = "#0d6c45";
      } else {
        firestoreChip.textContent = "Firestore: disconnected";
        firestoreChip.style.color = "#932727";
      }
    }
  }

  function updateLastState(data) {
    const lastQr = qs("lastQr");
    const lastQrAt = qs("lastQrAt");
    const lastEventMessage = qs("lastEventMessage");

    if (lastQr) {
      lastQr.textContent = data.last_scan || "Waiting for scan...";
    }
    if (lastQrAt) {
      lastQrAt.textContent = toLocalTime(data.last_scan_at);
    }
    if (lastEventMessage) {
      lastEventMessage.textContent = (data.last_event && data.last_event.message) || "No events yet.";
    }
  }

  function updateRegistrationPanel(data) {
    const panel = qs("registerPanel");
    const qrInput = qs("regQrCode");
    const driverIdInput = qs("regDriverId");
    if (!panel || !qrInput || !driverIdInput) {
      return;
    }

    const pending = data.pending_registration;
    if (!pending || !pending.qr_code) {
      panel.classList.remove("show");
      qrInput.value = "";
      return;
    }

    panel.classList.add("show");
    qrInput.value = pending.qr_code || "";
    if (!driverIdInput.value) {
      driverIdInput.value = pending.suggested_driver_id || "";
    }
  }

  function eventTag(event) {
    const movement = (event.movement || "").toLowerCase();
    if (movement === "entry") {
      return '<span class="tag tag-entry">ENTRY</span>';
    }
    if (movement === "exit") {
      return '<span class="tag tag-exit">EXIT</span>';
    }
    if (event.event_type === "admin_scan") {
      return '<span class="tag tag-info">ADMIN</span>';
    }
    if (event.event_type === "pending_registration") {
      return '<span class="tag tag-info">REGISTER</span>';
    }
    return '<span class="tag tag-info">INFO</span>';
  }

  function renderEvents(data) {
    const body = qs("scannerEvents");
    if (!body) {
      return;
    }
    const rows = data.recent_events || [];
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">No scanner events yet.</td></tr>';
      return;
    }

    body.innerHTML = rows.map(function (event) {
      const who = event.driver_name || event.admin_name || "-";
      const when = event.event_time || event.seen_at || event.last_scan_at || "-";
      return `
        <tr>
          <td>${toLocalTime(when)}</td>
          <td>${eventTag(event)}</td>
          <td>${who}</td>
          <td>${event.bus_number || "-"}</td>
          <td>${event.phone_number || "-"}</td>
          <td>${event.message || "-"}</td>
        </tr>
      `;
    }).join("");
  }

  async function refreshState() {
    if (loading) {
      return;
    }
    loading = true;
    try {
      const data = await requestJson("/api/scanner/state", { cache: "no-store" });
      updateStatusChips(data);
      updateLastState(data);
      updateRegistrationPanel(data);
      renderEvents(data);
      await handleLastEvent(data.last_event);
    } catch (error) {
      showToast(error.message || "Scanner refresh failed.", "error");
    } finally {
      loading = false;
    }
  }

  function bindRegisterForm() {
    const form = qs("registerForm");
    if (!form) {
      return;
    }
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.qr_code = sanitizeText(payload.qr_code);
      payload.driver_id = sanitizeId(payload.driver_id);
      payload.driver_name = sanitizeText(payload.driver_name);
      payload.bus_number = sanitizeText(payload.bus_number).toUpperCase();
      payload.phone_number = sanitizePhone(payload.phone_number);
      payload.note = sanitizeText(payload.note);

      if (!payload.qr_code || !payload.driver_id || !payload.driver_name || !payload.bus_number || !payload.phone_number) {
        showToast("Please complete all required registration fields.", "error");
        return;
      }

      try {
        const data = await requestJson("/api/scanner/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        showToast(`Driver registered and entry recorded: ${data.result.driver_name}`, "success");
        showDynamicIsland(
          "entry",
          "Driver Entry Detected",
          `${data.result.driver_name || "-"} (${data.result.driver_id || "-"}) | ${data.result.bus_number || "-"}`
        );
        browserNotify(
          "Driver Entry Detected",
          `${data.result.driver_name || "-"} (${data.result.driver_id || "-"})`
        );
        form.reset();
        await refreshState();
      } catch (error) {
        showToast(error.message || "Registration failed.", "error");
      }
    });
  }

  function bindManualScan() {
    const form = qs("manualScanForm");
    if (!form) {
      return;
    }
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.qr_code = sanitizeText(payload.qr_code);
      if (!payload.qr_code) {
        showToast("Enter QR text to test.", "error");
        return;
      }
      try {
        await requestJson("/api/scanner/manual-scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        form.reset();
        await refreshState();
      } catch (error) {
        showToast(error.message || "Manual scan failed.", "error");
      }
    });
  }

  function bindCameraRestart() {
    const btn = qs("restartCameraBtn");
    if (!btn) {
      return;
    }
    btn.addEventListener("click", async function () {
      try {
        await requestJson("/api/camera/restart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        showToast("Camera restart requested.", "success");
        await refreshState();
      } catch (error) {
        showToast(error.message || "Camera restart failed.", "error");
      }
    });
  }

  function setDeviceScanStatus(text) {
    const el = qs("deviceScanStatus");
    if (el) {
      el.textContent = text;
    }
  }

  function stopDeviceScanner() {
    window.clearInterval(deviceScanTimer);
    deviceScanTimer = null;
    if (deviceStream) {
      deviceStream.getTracks().forEach(function (track) {
        track.stop();
      });
      deviceStream = null;
    }
    const video = qs("deviceCamera");
    if (video) {
      video.srcObject = null;
    }
    setDeviceScanStatus("Device scanner stopped.");
  }

  async function handleDeviceQr(text) {
    const qrCode = sanitizeText(text);
    if (!qrCode) {
      return;
    }
    const now = Date.now();
    if (qrCode === lastDeviceCode && (now - lastDeviceCodeAt) < 3500) {
      return;
    }
    lastDeviceCode = qrCode;
    lastDeviceCodeAt = now;
    try {
      await requestJson("/api/scanner/manual-scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qr_code: qrCode })
      });
      setDeviceScanStatus(`Detected QR: ${qrCode.slice(0, 32)}`);
      await refreshState();
    } catch (error) {
      setDeviceScanStatus(error.message || "QR submit failed.");
    }
  }

  async function scanDeviceFrame() {
    if (deviceScanBusy || !barcodeDetector) {
      return;
    }
    const video = qs("deviceCamera");
    if (!video || !video.srcObject || video.readyState < 2) {
      return;
    }

    deviceScanBusy = true;
    try {
      const results = await barcodeDetector.detect(video);
      if (results && results.length) {
        for (const item of results) {
          if (item && item.rawValue) {
            await handleDeviceQr(item.rawValue);
            break;
          }
        }
      }
    } catch (_error) {
      // Keep scanner alive on intermittent frame decode errors.
    } finally {
      deviceScanBusy = false;
    }
  }

  async function startDeviceScanner() {
    if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
      showToast("Device camera needs HTTPS in hosted mode.", "error");
      setDeviceScanStatus("Blocked: use HTTPS domain to access device camera.");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showToast("Device camera API is unavailable in this browser.", "error");
      setDeviceScanStatus("Browser camera API unavailable.");
      return;
    }
    if (!("BarcodeDetector" in window)) {
      showToast("This browser does not support BarcodeDetector. Use manual QR fallback.", "error");
      setDeviceScanStatus("Browser not supported for auto QR decode.");
      return;
    }

    try {
      const formats = await window.BarcodeDetector.getSupportedFormats();
      if (!formats || !formats.includes("qr_code")) {
        showToast("QR scanning format not supported in this browser.", "error");
        setDeviceScanStatus("QR format not supported. Use Chrome/Edge on Android/Desktop.");
        return;
      }
      barcodeDetector = new window.BarcodeDetector({ formats: ["qr_code"] });
    } catch (error) {
      showToast(error.message || "Unable to initialize QR detector.", "error");
      setDeviceScanStatus("Unable to initialize QR detector.");
      return;
    }

    try {
      stopDeviceScanner();
      const video = qs("deviceCamera");
      if (!video) {
        return;
      }
      deviceStream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 }
        },
        audio: false
      });
      video.srcObject = deviceStream;
      await video.play().catch(function () {
        // ignore autoplay restrictions, user can tap start again.
      });
      setDeviceScanStatus("Device scanner running. Point QR card at camera.");
      deviceScanTimer = window.setInterval(scanDeviceFrame, 350);
    } catch (error) {
      showToast(error.message || "Could not start device camera.", "error");
      setDeviceScanStatus("Camera permission denied or unavailable.");
    }
  }

  function bindDeviceScanner() {
    const startBtn = qs("startDeviceScanBtn");
    const stopBtn = qs("stopDeviceScanBtn");
    if (startBtn) {
      startBtn.addEventListener("click", function () {
        startDeviceScanner().catch(function (error) {
          showToast(error.message || "Unable to start device scanner.", "error");
        });
      });
    }
    if (stopBtn) {
      stopBtn.addEventListener("click", stopDeviceScanner);
    }

    window.addEventListener("beforeunload", stopDeviceScanner);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stopDeviceScanner();
      }
    });
  }

  function startPolling() {
    window.clearInterval(refreshTimer);
    refreshTimer = window.setInterval(refreshState, 2200);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    askNotificationPermission();
    bindRegisterForm();
    bindManualScan();
    bindDeviceScanner();
    bindCameraRestart();
    await refreshState();
    startPolling();
  });
})();
