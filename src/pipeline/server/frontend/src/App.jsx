import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CategoryScale,
  Chart,
  Filler,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Title,
  Tooltip,
} from "chart.js";

Chart.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  LineController,
  Title,
  Tooltip,
  Legend,
  Filler
);

const POLL_INTERVAL_MS = 5000;
const FREQ_CHART_MAX_HZ = 5000;
const FREQ_CHART_STEP_HZ = 250;
const FREQ_CHART_GRID = Array.from(
  { length: FREQ_CHART_MAX_HZ / FREQ_CHART_STEP_HZ + 1 },
  (_, index) => index * FREQ_CHART_STEP_HZ
);

const EMPTY_ANALYSIS = {
  freq_hz: FREQ_CHART_GRID,
  underhang_mag: FREQ_CHART_GRID.map(() => null),
  overhang_mag: FREQ_CHART_GRID.map(() => null),
  message: "No data available yet",
};

const MODEL_FAMILY_PRIORITY = {
  ml: 0,
  dl: 1,
};

const UNDERHANG_SERIES_STYLE = {
  label: "Underhang radial",
  borderColor: "#0a7f79",
  backgroundColor: "rgba(10,127,121,0.25)",
};

const OVERHANG_SERIES_STYLE = {
  label: "Overhang radial",
  borderColor: "#c47a12",
  backgroundColor: "rgba(196,122,18,0.25)",
};


function detectAccelType(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized.includes("lis3dh")) return "lis3dh";
  if (normalized.includes("adxl345")) return "adxl345";
  return null;
}

function swapUidAccel(sensorUid, nextAccel) {
  const currentAccel = detectAccelType(sensorUid);
  if (!currentAccel || !nextAccel || currentAccel === nextAccel) {
    return null;
  }
  return sensorUid.replace(new RegExp(currentAccel, "ig"), nextAccel);
}

/**
 * If `sensorUid` contains `lis3dh` or `adxl345` and the companion sensor
 * also exists in the provided list, returns both UIDs. Otherwise null.
 */
function findSensorPair(sensorUid, sensorList) {
  if (!sensorUid || !sensorList) return null;

  const accel = detectAccelType(sensorUid);
  if (!accel) return null;

  if (accel === "lis3dh") {
    const adxlUid = swapUidAccel(sensorUid, "adxl345");
    if (adxlUid && sensorList.some((s) => s.sensor_uid === adxlUid)) {
      return { lis3dh: sensorUid, adxl345: adxlUid };
    }
  }

  if (accel === "adxl345") {
    const lisUid = swapUidAccel(sensorUid, "lis3dh");
    if (lisUid && sensorList.some((s) => s.sensor_uid === lisUid)) {
      return { lis3dh: lisUid, adxl345: sensorUid };
    }
  }

  return null;
}

function buildCsvDownloadHref(recordId, fileId) {
  const accel = detectAccelType(fileId);
  if (accel === "lis3dh") {
    return `/api/inference/${recordId}/csv/lis3dh`;
  }
  if (accel === "adxl345") {
    return `/api/inference/${recordId}/csv/adxl345`;
  }
  return `/api/inference/${recordId}/csv`;
}

function extractCaptureSequence(fileId) {
  if (!fileId) return null;
  const match = String(fileId).match(/-(\d+)-(lis3dh|adxl345)\b/i);
  if (!match) return null;
  const seq = Number(match[1]);
  return Number.isFinite(seq) ? seq : null;
}

function pairInferenceRowsByCaptureSequence(lisRows, adxlRows) {
  const lis = Array.isArray(lisRows) ? [...lisRows] : [];
  const adxl = Array.isArray(adxlRows) ? [...adxlRows] : [];

  const adxlBySeq = new Map();
  for (const row of adxl) {
    const seq = extractCaptureSequence(row?.file_id);
    if (seq == null) continue;
    if (!adxlBySeq.has(seq)) {
      adxlBySeq.set(seq, row);
    }
  }

  return lis
    .map((row) => {
      const seq = extractCaptureSequence(row?.file_id);
      if (seq == null) return null;
      const companion = adxlBySeq.get(seq);
      if (!companion) return null;
      return {
        ...row,
        companion_file_id: companion.file_id,
        companion_record_id: companion.record_id,
        companion_sensor_uid: companion.sensor_uid,
      };
    })
    .filter(Boolean)
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    .slice(0, 25);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
  });

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.message || `Request failed (${response.status})`);
  }

  return body;
}

function formatTimestamp(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
}

function modelResultRows(modelResults) {
  const entries = Object.entries(modelResults || {});
  if (entries.length === 0) {
    return <span>-</span>;
  }

  return entries.map(([name, result]) => {
    const status = result.status || "unknown";
    const inference = result.inference || "-";
    const confidence = Number(result.confidence || 0).toFixed(3);
    return (
      <div key={name}>
        <strong>{name}</strong>: {inference} ({status}, {confidence})
      </div>
    );
  });
}

function toNumericArray(values) {
  if (!Array.isArray(values)) {
    return [];
  }

  return values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
}

function interpolateSeries(targetFreqs, sourceFreqs, sourceValues) {
  const n = Math.min(sourceFreqs.length, sourceValues.length);
  if (n === 0) {
    return targetFreqs.map(() => null);
  }

  const points = sourceFreqs
    .slice(0, n)
    .map((freq, index) => ({
      freq,
      value: sourceValues[index],
    }))
    .filter(
      (point) =>
        Number.isFinite(point.freq) &&
        Number.isFinite(point.value) &&
        point.freq >= 0 &&
        point.freq <= FREQ_CHART_MAX_HZ
    )
    .sort((a, b) => a.freq - b.freq);

  if (points.length === 0) {
    return targetFreqs.map(() => null);
  }

  return targetFreqs.map((targetFreq) => {
    if (targetFreq < points[0].freq || targetFreq > points[points.length - 1].freq) {
      return null;
    }

    const rightIndex = points.findIndex((point) => point.freq >= targetFreq);
    if (rightIndex < 0) {
      return null;
    }

    const rightPoint = points[rightIndex];
    if (rightPoint.freq === targetFreq || rightIndex === 0) {
      return rightPoint.value;
    }

    const leftPoint = points[rightIndex - 1];
    const denominator = rightPoint.freq - leftPoint.freq;
    if (denominator <= 0) {
      return rightPoint.value;
    }

    const ratio = (targetFreq - leftPoint.freq) / denominator;
    return leftPoint.value + ratio * (rightPoint.value - leftPoint.value);
  });
}

function normalizeFrequencyAnalysis(payload) {
  const freqHz = toNumericArray(payload?.freq_hz);
  const underhangMag = toNumericArray(payload?.underhang_mag);
  const overhangMag = toNumericArray(payload?.overhang_mag);
  const rawMessage = typeof payload?.message === "string" ? payload.message.trim() : "";
  const message = rawMessage.toLowerCase() === "ok" ? "" : rawMessage;

  return {
    freq_hz: FREQ_CHART_GRID,
    underhang_mag: interpolateSeries(FREQ_CHART_GRID, freqHz, underhangMag),
    overhang_mag: interpolateSeries(FREQ_CHART_GRID, freqHz, overhangMag),
    message,
  };
}

function hasAnyFiniteValue(values) {
  if (!Array.isArray(values)) {
    return false;
  }

  return values.some((value) => Number.isFinite(Number(value)));
}

function sumAbsoluteFiniteValues(values) {
  if (!Array.isArray(values)) {
    return 0;
  }

  return values.reduce((accumulator, value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return accumulator;
    }
    return accumulator + Math.abs(numeric);
  }, 0);
}

function inferBearingTypeFromAnalysis(analysis) {
  const underhangMag = analysis?.underhang_mag || [];
  const overhangMag = analysis?.overhang_mag || [];

  const hasUnderhang = hasAnyFiniteValue(underhangMag);
  const hasOverhang = hasAnyFiniteValue(overhangMag);

  if (hasUnderhang && !hasOverhang) {
    return "underhang";
  }
  if (hasOverhang && !hasUnderhang) {
    return "overhang";
  }
  if (hasUnderhang && hasOverhang) {
    const underhangEnergy = sumAbsoluteFiniteValues(underhangMag);
    const overhangEnergy = sumAbsoluteFiniteValues(overhangMag);
    return overhangEnergy > underhangEnergy ? "overhang" : "underhang";
  }

  return null;
}

function buildChartSignature(seriesLabel, points) {
  const serializedPoints = points.map((point) => `${point.x}:${point.y ?? "null"}`).join("|");
  return `${seriesLabel}::${serializedPoints}`;
}

function buildNumericSeriesSignature(values) {
  if (!Array.isArray(values)) {
    return "";
  }

  return values
    .map((value) => {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? numeric.toFixed(8) : "null";
    })
    .join(",");
}

function buildAnalysisSignature(analysis) {
  return [
    buildNumericSeriesSignature(analysis?.freq_hz || []),
    buildNumericSeriesSignature(analysis?.underhang_mag || []),
    buildNumericSeriesSignature(analysis?.overhang_mag || []),
    String(analysis?.message || ""),
  ].join("::");
}

function buildInferenceRowsSignature(rows) {
  if (!Array.isArray(rows)) {
    return "";
  }

  return rows
    .map((row) =>
      [
        row?.record_id ?? "",
        row?.created_at ?? "",
        row?.sensor_uid ?? "",
        row?.bearing_type ?? "",
        row?.selected_model ?? "",
        row?.selected_inference ?? "",
        row?.file_id ?? "",
        row?.current_ma ?? "",
      ].join(":")
    )
    .join("|");
}

function sortModelOptions(options) {
  if (!Array.isArray(options)) {
    return [];
  }

  return [...options].sort((left, right) => {
    const leftFamilyPriority =
      MODEL_FAMILY_PRIORITY[left?.family] ?? Number.MAX_SAFE_INTEGER;
    const rightFamilyPriority =
      MODEL_FAMILY_PRIORITY[right?.family] ?? Number.MAX_SAFE_INTEGER;

    if (leftFamilyPriority !== rightFamilyPriority) {
      return leftFamilyPriority - rightFamilyPriority;
    }

    return String(left?.model ?? "").localeCompare(String(right?.model ?? ""), undefined, {
      sensitivity: "base",
    });
  });
}

function AuthScreen({ mode, setMode, authForm, setAuthForm, authError, authBusy, onSubmit }) {
  return (
    <main className="auth-shell">
      <section className="auth-card">
        <p className="eyebrow">Edge PdM Server</p>
        <h1>{mode === "login" ? "Welcome Back" : "Create Account"}</h1>
        <p className="subtitle">
          {mode === "login"
            ? "Sign in to manage sensors, select return models, and inspect inference history."
            : "Register your account. Credentials are stored in PostgreSQL as salted password hashes."}
        </p>

        <div className="auth-toggle" role="tablist" aria-label="Authentication mode">
          <button
            type="button"
            className={mode === "login" ? "active" : ""}
            onClick={() => setMode("login")}
          >
            Login
          </button>
          <button
            type="button"
            className={mode === "register" ? "active" : ""}
            onClick={() => setMode("register")}
          >
            Register
          </button>
        </div>

        <form className="stack-form" onSubmit={onSubmit}>
          <label>
            Username
            <input
              type="text"
              value={authForm.username}
              onChange={(event) =>
                setAuthForm((prev) => ({
                  ...prev,
                  username: event.target.value,
                }))
              }
              required
              minLength={3}
              maxLength={128}
            />
          </label>

          <label>
            Password
            <input
              type="password"
              value={authForm.password}
              onChange={(event) =>
                setAuthForm((prev) => ({
                  ...prev,
                  password: event.target.value,
                }))
              }
              required
              minLength={8}
              maxLength={128}
            />
          </label>

          <button type="submit" disabled={authBusy}>
            {authBusy ? "Working..." : mode === "login" ? "Login" : "Register"}
          </button>
        </form>

        {mode === "login" ? (
          <p className="hint-row">
            New user?
            <button type="button" className="link-button" onClick={() => setMode("register")}>
              Register here
            </button>
          </p>
        ) : (
          <p className="hint-row">
            Already have an account?
            <button type="button" className="link-button" onClick={() => setMode("login")}>
              Login here
            </button>
          </p>
        )}

        <p className="flash error">{authError}</p>
        <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">
          Open Swagger Docs
        </a>
      </section>
    </main>
  );
}

function App() {
  const [session, setSession] = useState(null);
  const [authMode, setAuthMode] = useState("login");
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const [authForm, setAuthForm] = useState({ username: "", password: "" });

  const [sensors, setSensors] = useState([]);
  const sensorsRef = useRef([]);          // kept in sync with sensors for use in callbacks
  const [modelOptions, setModelOptions] = useState({});
  const modelOptionsRef = useRef({});

  const [analysisSensor, setAnalysisSensor] = useState("");
  const analysisSensorRef = useRef("");

  const [inferenceRows, setInferenceRows] = useState([]);
  const [analysis, setAnalysis] = useState(EMPTY_ANALYSIS);

  const [sensorForm, setSensorForm] = useState({
    sensor_uid: "",
    bearing_type: "underhang",
    selected_model: "LightGBM",
  });
  const [sensorError, setSensorError] = useState("");
  const [sensorMessage, setSensorMessage] = useState("");

  const chartCanvasRef = useRef(null);
  const chartRef = useRef(null);
  const chartSignatureRef = useRef("");
  const analysisSignatureRef = useRef(buildAnalysisSignature(EMPTY_ANALYSIS));
  const inferenceRowsSignatureRef = useRef("");

  const refreshModelOptions = useCallback(async (bearingTypes) => {
    const next = { ...modelOptionsRef.current };

    await Promise.all(
      bearingTypes.map(async (bearingType) => {
        if (next[bearingType]) {
          return;
        }
        const payload = await fetchJson(`/api/models/${bearingType}`);
        next[bearingType] = payload.models || [];
      })
    );

    modelOptionsRef.current = next;
    setModelOptions(next);
  }, []);

  const refreshInferenceViews = useCallback(async (sensorUid) => {
    if (!sensorUid) {
      inferenceRowsSignatureRef.current = "";
      analysisSignatureRef.current = buildAnalysisSignature(EMPTY_ANALYSIS);
      setInferenceRows([]);
      setAnalysis(EMPTY_ANALYSIS);
      return;
    }

    // Resolve the effective sensor UID from the active_accel preference.
    // If the selected sensor has active_accel set, and that accel belongs to the
    // companion sensor, we fetch inference + analysis from the companion instead.
    const pair = findSensorPair(sensorUid, sensorsRef.current);
    const currentSensor = sensorsRef.current.find((s) => s.sensor_uid === sensorUid);
    const activeAccel = currentSensor?.active_accel ?? null;

    let effectiveUid = sensorUid;
    if (pair && activeAccel) {
      const selectedSensorAccel = detectAccelType(sensorUid);
      if (activeAccel === "adxl345" && selectedSensorAccel === "lis3dh") {
        effectiveUid = pair.adxl345;
      } else if (activeAccel === "lis3dh" && selectedSensorAccel === "adxl345") {
        effectiveUid = pair.lis3dh;
      }
    }

    // Analysis (frequency chart + avg/std) uses the active_accel preference.
    const analysisUrl = `/api/analysis/${encodeURIComponent(effectiveUid)}`;

    // Inference rows are paired by shared capture sequence in file_id:
    // <device>-<capture_sequence>-lis3dh and <device>-<capture_sequence>-adxl345.
    const inferencePromise = pair
      ? Promise.all([
          fetchJson(`/api/inference/latest?sensor_uid=${encodeURIComponent(pair.lis3dh)}`).catch(() => []),
          fetchJson(`/api/inference/latest?sensor_uid=${encodeURIComponent(pair.adxl345)}`).catch(() => []),
        ]).then(([lisRows, adxlRows]) => pairInferenceRowsByCaptureSequence(lisRows, adxlRows))
      : fetchJson(`/api/inference/latest?sensor_uid=${encodeURIComponent(sensorUid)}`)
          .then((rows) => {
            const lisRows = (rows || []).filter((r) => detectAccelType(r?.file_id) === "lis3dh");
            const adxlRows = (rows || []).filter((r) => detectAccelType(r?.file_id) === "adxl345");
            return pairInferenceRowsByCaptureSequence(lisRows, adxlRows);
          });

    const [rows, analysisPayload] = await Promise.all([
      inferencePromise,
      fetchJson(analysisUrl).catch((error) => ({
        ...EMPTY_ANALYSIS,
        message: error.message,
      })),
    ]);

    const rowsSignature = buildInferenceRowsSignature(rows);
    if (rowsSignature !== inferenceRowsSignatureRef.current) {
      inferenceRowsSignatureRef.current = rowsSignature;
      setInferenceRows(rows);
    }

    const normalizedAnalysis = normalizeFrequencyAnalysis(analysisPayload);
    const analysisSignature = buildAnalysisSignature(normalizedAnalysis);
    if (analysisSignature !== analysisSignatureRef.current) {
      analysisSignatureRef.current = analysisSignature;
      setAnalysis(normalizedAnalysis);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    const sensorRows = await fetchJson("/api/sensors");

    // Prefetch recent inference rows for each sensor to detect which accel
    // types have been seen (LIS3DH / ADXL345). This lets the UI show the
    // Active Accel dropdown even when the registered `sensor_uid` doesn't
    // contain the accel token (common when both accels share a base UID).
    const inferenceLists = await Promise.all(
      sensorRows.map((s) =>
        fetchJson(`/api/inference/latest?sensor_uid=${encodeURIComponent(s.sensor_uid)}`).catch(() => [])
      )
    );

    const enriched = sensorRows.map((s, i) => {
      const rows = inferenceLists[i] || [];
      const accels = new Set();
      for (const r of rows) {
        const accel = detectAccelType(r?.file_id);
        if (accel) {
          accels.add(accel);
        }
      }
      return { ...s, available_accels: Array.from(accels) };
    });

    setSensors(enriched);
    sensorsRef.current = enriched;

    const uniqueBearingTypes = Array.from(new Set(sensorRows.map((sensor) => sensor.bearing_type)));
    await refreshModelOptions(uniqueBearingTypes);

    let selected = analysisSensorRef.current;
    if (!selected || !sensorRows.some((sensor) => sensor.sensor_uid === selected)) {
      selected = sensorRows[0]?.sensor_uid || "";
      analysisSensorRef.current = selected;
      setAnalysisSensor(selected);
    }

    await refreshInferenceViews(selected);
  }, [refreshInferenceViews, refreshModelOptions]);

  const analysisBearingType = useMemo(() => inferBearingTypeFromAnalysis(analysis), [analysis]);

  const bootstrapSession = useCallback(async () => {
    try {
      const currentSession = await fetchJson("/api/session");
      setSession(currentSession);
      await refreshAll();
    } catch (_error) {
      setSession(null);
    }
  }, [refreshAll]);

  useEffect(() => {
    bootstrapSession();
  }, [bootstrapSession]);

  useEffect(() => {
    if (!session) {
      return undefined;
    }

    const timer = setInterval(async () => {
      try {
        await refreshAll();
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setSensorError(message);
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [session, refreshAll]);

  useEffect(() => {
    if (!session || !chartCanvasRef.current || chartRef.current) {
      return;
    }

    chartSignatureRef.current = "";

    chartRef.current = new Chart(chartCanvasRef.current, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: UNDERHANG_SERIES_STYLE.label,
            data: [],
            borderColor: UNDERHANG_SERIES_STYLE.borderColor,
            backgroundColor: UNDERHANG_SERIES_STYLE.backgroundColor,
            borderWidth: 1.5,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        animation: false,
        plugins: {
          legend: {
            display: false,
          },
        },
        scales: {
          x: {
            type: "linear",
            title: { display: true, text: "Frequency (Hz)" },
            min: 0,
            max: FREQ_CHART_MAX_HZ,
            ticks: {
              stepSize: FREQ_CHART_STEP_HZ,
              autoSkip: false,
              maxRotation: 0,
              callback: (value) => Number(value).toFixed(0),
            },
          },
          y: {
            title: { display: true, text: "Normalized magnitude" },
            min: 0,
            max: 1,
          },
        },
      },
    });

    return () => {
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
  }, [session]);

  useEffect(() => {
    if (!chartRef.current) {
      return;
    }

    const chart = chartRef.current;
    const freqHz = analysis.freq_hz || [];
    const underhangMag = analysis.underhang_mag || [];
    const overhangMag = analysis.overhang_mag || [];

    const isOverhang = analysisBearingType === "overhang";
    const seriesStyle = isOverhang ? OVERHANG_SERIES_STYLE : UNDERHANG_SERIES_STYLE;
    const activeMagnitudes = isOverhang ? overhangMag : underhangMag;
    const points = freqHz.map((freq, index) => ({
      x: Number(freq),
      y: activeMagnitudes[index] ?? null,
    }));
    const signature = buildChartSignature(seriesStyle.label, points);

    if (signature === chartSignatureRef.current) {
      return;
    }

    chartSignatureRef.current = signature;
    chart.data.datasets = [
      {
        label: seriesStyle.label,
        data: points,
        borderColor: seriesStyle.borderColor,
        backgroundColor: seriesStyle.backgroundColor,
        borderWidth: 1.5,
        pointRadius: 0,
      },
    ];
    chart.update("none");
  }, [analysis, analysisBearingType]);

  const handleAuthSubmit = async (event) => {
    event.preventDefault();
    setAuthError("");
    setAuthBusy(true);

    try {
      const endpoint = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
      const payload = await fetchJson(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authForm),
      });

      setSession({ username: payload.username, user_id: payload.user_id });
      setAuthForm({ username: "", password: "" });
      setSensorError("");
      setSensorMessage("");
      await refreshAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setAuthError(message);
    } finally {
      setAuthBusy(false);
    }
  };

  const handleLogout = async () => {
    await fetchJson("/api/auth/logout", { method: "POST" });
    inferenceRowsSignatureRef.current = "";
    analysisSignatureRef.current = buildAnalysisSignature(EMPTY_ANALYSIS);
    setSession(null);
    setSensors([]);
    setInferenceRows([]);
    setAnalysis(EMPTY_ANALYSIS);
    setSensorMessage("");
    setSensorError("");
  };

  const handleCreateSensor = async (event) => {
    event.preventDefault();
    setSensorError("");
    setSensorMessage("");

    try {
      await fetchJson("/api/sensors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sensorForm),
      });
      setSensorForm({
        sensor_uid: "",
        bearing_type: sensorForm.bearing_type,
        selected_model: "LightGBM",
      });
      setSensorMessage(`Sensor ${sensorForm.sensor_uid} created.`);
      await refreshAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSensorError(message);
    }
  };

  const patchSensor = async (sensorUid, patch) => {
    setSensorError("");
    setSensorMessage("");

    try {
      await fetchJson(`/api/sensors/${encodeURIComponent(sensorUid)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      await refreshAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSensorError(message);
    }
  };

  const handleDeleteSensor = async (sensorUid) => {
    const confirmed = window.confirm(`Delete sensor ${sensorUid}? This will remove its stored inference history.`);
    if (!confirmed) {
      return;
    }

    setSensorError("");
    setSensorMessage("");

    try {
      await fetchJson(`/api/sensors/${encodeURIComponent(sensorUid)}`, {
        method: "DELETE",
      });
      setSensorMessage(`Sensor ${sensorUid} deleted.`);
      await refreshAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSensorError(message);
    }
  };

  const handleAnalysisSensorChange = async (event) => {
    const sensorUid = event.target.value;
    analysisSensorRef.current = sensorUid;
    setAnalysisSensor(sensorUid);

    try {
      await refreshInferenceViews(sensorUid);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSensorError(message);
    }
  };

  const sensorCountLabel = useMemo(() => {
    if (sensors.length === 1) {
      return "1 sensor";
    }
    return `${sensors.length} sensors`;
  }, [sensors.length]);

  if (!session) {
    return (
      <AuthScreen
        mode={authMode}
        setMode={setAuthMode}
        authForm={authForm}
        setAuthForm={setAuthForm}
        authError={authError}
        authBusy={authBusy}
        onSubmit={handleAuthSubmit}
      />
    );
  }

  return (
    <div>
      <header className="topbar">
        <div>
          <p className="eyebrow">Edge PdM Server</p>
          <h1>Inference Control Plane</h1>
          <p className="subtitle compact">{sensorCountLabel} linked to {session.username}</p>
        </div>
        <div className="topbar-actions">
          <a className="ghost-link" href="/docs" target="_blank" rel="noreferrer">
            Swagger Docs
          </a>
          <button type="button" className="ghost" onClick={handleLogout}>
            Logout
          </button>
        </div>
      </header>

      <main className="dashboard-layout">
        <section className="panel">
          <h2>Sensor Registry</h2>
          <form className="inline-form" onSubmit={handleCreateSensor}>
            <input
              type="text"
              value={sensorForm.sensor_uid}
              placeholder="sensor ID"
              required
              onChange={(event) =>
                setSensorForm((prev) => ({
                  ...prev,
                  sensor_uid: event.target.value,
                }))
              }
            />
            <select
              value={sensorForm.bearing_type}
              onChange={(event) =>
                setSensorForm((prev) => ({
                  ...prev,
                  bearing_type: event.target.value,
                }))
              }
            >
              <option value="underhang">underhang</option>
              <option value="overhang">overhang</option>
            </select>
            <button type="submit">Add sensor</button>
          </form>

          <p className="flash">{sensorMessage}</p>
          <p className="flash error">{sensorError}</p>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sensor ID</th>
                  <th>Bearing</th>
                  <th>Selected Return Model</th>
                  <th>Active Accel</th>
                  <th>Connected</th>
                  <th>Last Seen</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sensors.map((sensor) => {
                  const options = sortModelOptions(modelOptions[sensor.bearing_type] || []);
                  const sensorPair = findSensorPair(sensor.sensor_uid, sensors);
                  // Show the Active Accel dropdown when this sensor has accel-tagged
                  // history or when the UID itself already includes an accel token.
                  const isAccelSensor =
                    (Array.isArray(sensor.available_accels) && sensor.available_accels.length > 0) ||
                    Boolean(detectAccelType(sensor.sensor_uid));
                  return (
                    <tr key={sensor.sensor_uid}>
                      <td>{sensor.sensor_uid}</td>
                      <td>
                        <select
                          value={sensor.bearing_type}
                          onChange={(event) =>
                            patchSensor(sensor.sensor_uid, { bearing_type: event.target.value })
                          }
                        >
                          <option value="underhang">underhang</option>
                          <option value="overhang">overhang</option>
                        </select>
                      </td>
                      <td>
                        <select
                          value={sensor.selected_model}
                          onChange={(event) =>
                            patchSensor(sensor.sensor_uid, {
                              bearing_type: sensor.bearing_type,
                              selected_model: event.target.value,
                            })
                          }
                        >
                          {options.length === 0 ? (
                            <option value={sensor.selected_model}>{sensor.selected_model}</option>
                          ) : (
                            options.map((modelOption) => (
                              <option
                                key={modelOption.model}
                                value={modelOption.model}
                                disabled={!modelOption.available}
                              >
                                {modelOption.available
                                  ? modelOption.model
                                  : `${modelOption.model} (missing)`}
                              </option>
                            ))
                          )}
                        </select>
                      </td>
                      {/* Active Accel — selects which sensor's data is used for both
                           inference display and spectral analysis. Shown for any
                           sensor with a -lis3dh / -adxl345 suffix, even if the
                           companion sensor hasn't sent data yet. */}
                      {isAccelSensor ? (
                        <td>
                          <select
                            value={
                              sensor.active_accel ||
                              (sensor.available_accels?.includes("adxl345")
                                ? "adxl345"
                                : sensor.available_accels?.includes("lis3dh")
                                ? "lis3dh"
                                : (detectAccelType(sensor.sensor_uid) === "adxl345" ? "adxl345" : "lis3dh"))
                            }
                            onChange={(event) =>
                              patchSensor(sensor.sensor_uid, { active_accel: event.target.value })
                            }
                          >
                            <option value="lis3dh">LIS3DH</option>
                            <option value="adxl345">ADXL345</option>
                          </select>
                        </td>
                      ) : (
                        <td>—</td>
                      )}
                      <td>
                        {sensor.connected ? (
                          <span className="badge on">connected</span>
                        ) : (
                          <span className="badge off">disconnected</span>
                        )}
                      </td>
                      <td>{formatTimestamp(sensor.last_seen_at)}</td>
                        <td>
                          <button
                            type="button"
                            className="danger small"
                            onClick={() => handleDeleteSensor(sensor.sensor_uid)}
                          >
                            Delete
                          </button>
                        </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Frequency Analysis</h2>
              <p className="panel-meta">
                Sensor type: <span className="panel-meta-value">{analysisBearingType || "-"}</span>
              </p>
            </div>
            <select value={analysisSensor} onChange={handleAnalysisSensorChange}>
              {sensors.map((sensor) => (
                <option key={sensor.sensor_uid} value={sensor.sensor_uid}>
                  {sensor.sensor_uid}
                </option>
              ))}
            </select>
          </div>
          <canvas ref={chartCanvasRef} height={120}></canvas>
          {analysis.message ? <p className="flash">{analysis.message}</p> : null}
        </section>

        <section className="panel full-width">
          <h2>Latest Inference Results</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Sensor</th>
                  <th>Bearing Type</th>
                  <th>FILE ID - LIS3DH</th>
                  <th>FILE ID - ADXL345</th>
                  <th>Returned Model</th>
                  <th>Returned Inference</th>
                  <th>Average</th>
                  <th>Std. Dev</th>
                  <th>Current (mA)</th>
                  <th>All Model Outputs</th>
                </tr>
              </thead>
              <tbody>
                {inferenceRows.map((row) => {
                  const primaryAccel = detectAccelType(row.file_id);
                  const companionAccel = detectAccelType(row.companion_file_id);

                  const lis3dhFileId = primaryAccel === "lis3dh"
                    ? row.file_id
                    : companionAccel === "lis3dh"
                    ? row.companion_file_id
                    : null;
                  const lis3dhRecordId = primaryAccel === "lis3dh"
                    ? row.record_id
                    : companionAccel === "lis3dh"
                    ? row.companion_record_id
                    : null;

                  const adxl345FileId = primaryAccel === "adxl345"
                    ? row.file_id
                    : companionAccel === "adxl345"
                    ? row.companion_file_id
                    : null;
                  const adxl345RecordId = primaryAccel === "adxl345"
                    ? row.record_id
                    : companionAccel === "adxl345"
                    ? row.companion_record_id
                    : null;

                  return (
                    <tr key={row.record_id}>
                      <td>{formatTimestamp(row.created_at)}</td>
                      <td>{row.sensor_uid}</td>
                      <td>{row.bearing_type || "-"}</td>
                      <td>
                        {lis3dhFileId && lis3dhRecordId ? (
                          <a
                            href={buildCsvDownloadHref(lis3dhRecordId, lis3dhFileId)}
                            download={`${lis3dhFileId}.csv`}
                            className="file-link"
                            title="Download LIS3DH CSV"
                          >
                            {lis3dhFileId}
                          </a>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td>
                        {adxl345FileId && adxl345RecordId ? (
                          <a
                            href={buildCsvDownloadHref(adxl345RecordId, adxl345FileId)}
                            download={`${adxl345FileId}.csv`}
                            className="file-link"
                            title="Download ADXL345 CSV"
                          >
                            {adxl345FileId}
                          </a>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td>{row.selected_model}</td>
                      <td>{row.selected_inference}</td>
                      <td>{row.avg != null ? row.avg.toFixed(4) : "-"}</td>
                      <td>{row.std_dev != null ? row.std_dev.toFixed(4) : "-"}</td>
                      <td>{row.current_ma != null ? row.current_ma.toFixed(1) + " mA" : "-"}</td>
                      <td>
                        <div className="model-grid">{modelResultRows(row.model_results)}</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
