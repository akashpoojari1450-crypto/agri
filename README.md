# 🌱 Agri-Pulse: Smart Crop & Soil Doctor

An IoT-integrated precision agriculture assistant that connects a simulated
ESP32 sensor node (Wokwi) to a live web dashboard, using AI (Gemini) to
turn raw soil readings into actionable, multilingual crop diagnoses.

---

## System Architecture

```text
┌─────────────────┐
│      WOKWI       │
│  ESP32 + 4x      │
│  Potentiometers  │
│  (simulating     │
│  N / P / K /     │
│  Moisture)       │
└────────┬─────────┘
         │ HTTP GET /update
         ▼
┌─────────────────┐
│    FASTAPI       │
│  (Codespace,     │
│  Port 8000)      │
│  Stores latest   │
│  sensor state +  │
│  diagnosis log   │
└────────┬─────────┘
         │ HTTP GET /sensor-data, /history, /diagnosis-history
         ▼
┌─────────────────┐
│    STREAMLIT     │
│  (Codespace,     │
│  Port 8501)      │
│  Live dashboard  │
│  + AI chatbot     │
└────────┬─────────┘
         │
         ▼
┌─────────────────┐
│   GEMINI API     │
│  Diagnosis,      │
│  chat, image      │
│  analysis         │
└─────────────────┘
```

**Why FastAPI sits in the middle:** Streamlit only executes its Python
script in response to a live browser WebSocket connection — a plain HTTP
request from the ESP32 doesn't trigger it. FastAPI is a real, always-on
REST endpoint that receives the ESP32's data and holds it in memory;
Streamlit then polls FastAPI on a timer, which is what makes the dashboard
update automatically without a page refresh.

---

## Features

### 📊 Live Sensor Dashboard (Tab 2)
- Real-time gauges for Soil Moisture, pH, Nitrogen, Phosphorus, and
  Potassium, polled from Wokwi via FastAPI every 2 seconds
- Crop selector (20 crop types) applied across the whole app
- Historical trend charts (moisture/N/P/K and pH over time), backed by a
  readings log on the FastAPI side
- Weather forecast card (via field geolocation), factored directly into
  irrigation advice ("hold off — rain expected" vs "irrigate now")

### 🩺 AI Diagnosis Engine
- Gemini analyzes the exact sensor values against the selected crop's
  needs and returns a structured diagnosis: deficiency analysis,
  fertilizer/treatment plan, and sourcing recommendations
- Generated in 5 languages simultaneously (English, Hindi, Kannada,
  Tamil, Telugu) in a single Gemini call, so switching the display
  language is instant and doesn't burn extra API quota
- Quota-aware triggering: only re-diagnoses when sensor values genuinely
  change (rounded signature, not raw noise) and enforces a 5-minute
  cooldown between automatic calls, with a manual "Re-diagnose now"
  override for demos
- Diagnosis history log — past diagnoses are stored server-side and
  browsable in an expandable timeline

### 🛒 Automated Sourcing
- Gemini names a specific product to buy (e.g. "Urea fertilizer")
- App auto-generates shopping links: nearby stores (Google Maps, centered
  on the user's field location), plus Amazon, Flipkart, and BigHaat
  search links

### 💬 AI Chatbot (Tab 1)
- Multimodal chat — upload or paste an image of a crop/leaf and ask
  Gemini for a visual diagnosis
- Voice input via the browser's native SpeechRecognition API (multi-
  language), transcribing speech to the chat box
- Voice output ("Read Aloud") via the browser's native SpeechSynthesis
  API, reading any diagnosis back in the selected language — no extra
  API cost, works offline from Gemini entirely

### 📍 Location Awareness
- Auto-detects field location via browser geolocation on load, with a
  manual lat/long entry fallback
- Drives both the weather forecast and the "nearby stores" shopping link

---

## Tech Stack

| Layer | Technology |
|---|---|
| Sensor node | ESP32 (Wokwi simulation), C++ |
| Ingest API | FastAPI, Uvicorn |
| Dashboard / UI | Streamlit |
| AI | Google Gemini (`google-genai` SDK) — text + multimodal image analysis |
| Voice I/O | Browser Web Speech API (`streamlit_js_eval`) |
| Geolocation | Browser Geolocation API (`streamlit_js_eval`) |
| Data | In-memory store + history log (FastAPI), Pandas for charting |
| Image handling | Pillow, `st_img_pastebutton` (optional, graceful fallback) |

---

## Project Structure

```text
agri/
├── ai_diagnosis.py       # Streamlit app (chatbot + live dashboard)
├── sensor_server.py       # FastAPI ingest server
├── requirements.txt
├── .streamlit/
│   └── secrets.toml       # GEMINI_API_KEY (gitignored)
└── .gitignore
```

---

## Running It

**Terminal 1 — FastAPI:**
```bash
uvicorn sensor_server:app --host 0.0.0.0 --port 8000
```

**Terminal 2 — Streamlit:**
```bash
streamlit run ai_diagnosis.py --server.address 0.0.0.0 --server.port 8501
```

Both ports need to be set to **Public** in the Codespaces Ports tab. The
ESP32 firmware's `serverUrl` should point at the port-8000 URL + `/update`;
Streamlit's `API_URL` should point at the same base port-8000 URL
(no path).

---

## Engineering Notes / Lessons Learned

- **Streamlit's execution model** doesn't suit receiving arbitrary inbound
  HTTP requests directly — a dedicated backend (FastAPI) is the correct
  pattern for any external device pushing data in.
- **`streamlit_js_eval` has a documented quirk**: calling it from inside an
  `if st.button(...):` block loses the resolved value on the rerun that
  delivers it, since the branch no longer exists in the render tree at that
  point. Fix: keep the button as a flag-setter only, and make the actual
  JS call unconditional every rerun, gated internally by the flag.
- **Gemini free-tier quota (20 req/day)** required active protection —
  solved via rounded-value change detection plus a cooldown window, so
  sensor noise or rapid polling doesn't burn quota on near-duplicate calls.