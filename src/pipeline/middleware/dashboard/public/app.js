const els = {
  inferenceUrl: document.getElementById("inference_url"),
  configForm: document.getElementById("config-form"),
  configMessage: document.getElementById("config-message"),
  httpBindHost: document.getElementById("http-bind-host"),
  httpPort: document.getElementById("http-port"),
  receivedUploads: document.getElementById("received-uploads"),
  completedFiles: document.getElementById("completed-files"),
  mqttReceived: document.getElementById("mqtt-received"),
  mqttForwarded: document.getElementById("mqtt-forwarded"),

  inferenceOk: document.getElementById("inference-ok"),
  inferenceError: document.getElementById("inference-error"),
  sensorCount: document.getElementById("sensor-count"),
  sensorList: document.getElementById("sensor-list"),
  lastSensorId: document.getElementById("last-sensor-id"),
  lastFileId: document.getElementById("last-file-id"),
  lastCurrentMa: document.getElementById("last-current-ma"),
};

async function fetchStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Status request failed (${response.status})`);
  }
  return response.json();
}

async function fetchConfig() {
  const response = await fetch("/api/config", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Config request failed (${response.status})`);
  }
  return response.json();
}

function renderStatus(status) {
  els.httpBindHost.textContent = status.http_bind_host || "-";
  els.httpPort.textContent = status.http_port != null ? String(status.http_port) : "-";

  els.receivedUploads.textContent = String(status.received_uploads);
  els.completedFiles.textContent = String(status.completed_files);
  els.mqttReceived.textContent = String(status.mqtt_received);
  els.mqttForwarded.textContent = String(status.mqtt_forwarded);

  els.inferenceOk.textContent = String(status.inference_ok);
  els.inferenceError.textContent = String(status.inference_error);

  els.sensorCount.textContent = String(status.sensors_connected ?? 0);
  els.lastSensorId.textContent = status.last_sensor_id || "-";
  els.lastFileId.textContent = status.last_file_id || "-";
  els.lastCurrentMa.textContent = status.last_current_ma != null
    ? status.last_current_ma.toFixed(1) + " mA"
    : "N/A";

  els.sensorList.innerHTML = "";
  for (const sensorId of (status.sensors_seen || [])) {
    const li = document.createElement("li");
    li.textContent = sensorId;
    els.sensorList.appendChild(li);
  }
}

function showMessage(text, isError = false) {
  els.configMessage.textContent = text;
  els.configMessage.classList.toggle("error", isError);
}

async function refresh() {
  try {
    const [status, config] = await Promise.all([fetchStatus(), fetchConfig()]);
    renderStatus(status);
    els.inferenceUrl.value = config.inference_url;
    showMessage("Live status synced.", false);
  } catch (error) {
    showMessage(error.message, true);
  }
}

els.configForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const payload = {
    inference_url: els.inferenceUrl.value.trim(),
  };

  try {
    const response = await fetch("/api/config", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const message = body.message || `Update failed (${response.status})`;
      throw new Error(message);
    }

    showMessage("Inference endpoint updated.", false);
    await refresh();
  } catch (error) {
    showMessage(error.message, true);
  }
});

refresh();
setInterval(refresh, 3000);
