import streamlit as st
import streamlit.components.v1 as components
import requests
import pandas as pd
import time
import re
import json
import smtplib
from email.mime.text import MIMEText
from urllib.parse import quote
from PIL import Image
from io import BytesIO
import base64
from google import genai
from fpdf import FPDF

# Optional: clipboard paste support. Falls back gracefully if not installed.
try:
    from st_img_pastebutton import paste
    PASTE_AVAILABLE = True
except ImportError:
    PASTE_AVAILABLE = False

# Optional: browser geolocation + generic JS eval (used for voice input). Falls back gracefully if not installed.
try:
    from streamlit_js_eval import get_geolocation, streamlit_js_eval
    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False

st.set_page_config(page_title="Agri-Pulse", page_icon="🌱", layout="wide")

# ===========================================================
# GLOBAL THEME / CUSTOM CSS — dark-mode-friendly (translucent
# overlays instead of solid light backgrounds, so it works
# whether the user's Streamlit theme is light or dark)
# ===========================================================
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');

:root {
    --ap-green: #2e7d32;
    --ap-green-dark: #1b5e20;
    --ap-green-tint: rgba(76, 175, 80, 0.14);
    --ap-green-border: rgba(76, 175, 80, 0.35);
    --ap-accent: #f9a825;
}

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}
h1, h2, h3, h4, .ap-hero-title {
    font-family: 'Poppins', sans-serif !important;
}

/* Buttons */
.stButton > button, .stDownloadButton > button, .stLinkButton > a, .stFormSubmitButton > button {
    border-radius: 10px !important;
    border: 1px solid var(--ap-green) !important;
    background-color: var(--ap-green) !important;
    color: white !important;
    font-weight: 600 !important;
    transition: all 0.15s ease-in-out;
}
.stButton > button:hover, .stDownloadButton > button:hover,
.stLinkButton > a:hover, .stFormSubmitButton > button:hover {
    background-color: var(--ap-green-dark) !important;
    border-color: var(--ap-green-dark) !important;
    transform: translateY(-1px);
}

/* Metrics as soft cards — translucent so it adapts to light/dark theme */
div[data-testid="stMetric"] {
    background-color: var(--ap-green-tint);
    border: 1px solid var(--ap-green-border);
    border-radius: 12px;
    padding: 12px 10px;
}

/* Sidebar — no forced background color, just a subtle border */
section[data-testid="stSidebar"] {
    border-right: 1px solid var(--ap-green-border);
}

/* Tabs */
button[data-baseweb="tab"] {
    font-weight: 600;
    border-radius: 8px 8px 0 0 !important;
}

/* Hero section on login page — dark gradient with white text works in both themes */
.ap-hero {
    background: linear-gradient(135deg, var(--ap-green) 0%, var(--ap-green-dark) 100%);
    border-radius: 18px;
    padding: 36px 32px;
    color: white;
    margin-bottom: 24px;
}
.ap-hero-title {
    font-size: 2.1rem;
    font-weight: 700;
    margin-bottom: 6px;
    color: white;
}
.ap-hero-subtitle {
    font-size: 1.05rem;
    opacity: 0.92;
    max-width: 640px;
    color: white;
}
.ap-feature-card {
    background-color: var(--ap-green-tint);
    border: 1px solid var(--ap-green-border);
    border-radius: 12px;
    padding: 16px;
    height: 100%;
}
.ap-feature-card h4 {
    margin: 0 0 6px 0;
    color: var(--ap-green);
}
.ap-feature-card p {
    margin: 0;
    font-size: 0.9rem;
    opacity: 0.85;
}

/* Footer */
.ap-footer {
    text-align: center;
    opacity: 0.6;
    font-size: 0.8rem;
    padding: 18px 0 6px 0;
}

/* ---- Mobile responsiveness ---- */
@media (max-width: 640px) {
    /* Let any row of Streamlit columns wrap instead of squeezing */
    div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        row-gap: 10px;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        min-width: 46% !important;
        flex: 1 1 46% !important;
    }
    .ap-hero {
        padding: 22px 18px;
    }
    .ap-hero-title {
        font-size: 1.5rem;
    }
    .ap-hero-subtitle {
        font-size: 0.92rem;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
    }
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# ---------------------------------------------------------
# REPLACE with your current Codespace port-8000 forwarded URL
# (base URL only, no /update or /sensor-data on the end)
# ---------------------------------------------------------
API_URL = "https://urban-succotash-4j594pvvq5rx37r6p-8000.app.github.dev"

DIAGNOSIS_COOLDOWN_SECONDS = 300  # 5 min minimum between auto-triggered Gemini calls

ALERT_THRESHOLDS = {
    "moisture_low": 35,
    "ph_low": 5.5,
    "ph_high": 7.5,
    "n_low": 30,
    "p_low": 30,
    "k_low": 30,
}


def check_alert_condition(moisture, ph, n, p, k):
    reasons = []
    if moisture < ALERT_THRESHOLDS["moisture_low"]:
        reasons.append(f"Soil moisture critically low ({moisture}%)")
    if ph < ALERT_THRESHOLDS["ph_low"] or ph > ALERT_THRESHOLDS["ph_high"]:
        reasons.append(f"Soil pH out of safe range ({ph})")
    if n < ALERT_THRESHOLDS["n_low"]:
        reasons.append(f"Nitrogen deficiency ({n}%)")
    if p < ALERT_THRESHOLDS["p_low"]:
        reasons.append(f"Phosphorus deficiency ({p}%)")
    if k < ALERT_THRESHOLDS["k_low"]:
        reasons.append(f"Potassium deficiency ({k}%)")
    return reasons


def send_alert_email(to_email, crop, reasons, diagnosis_text):
    try:
        sender = st.secrets["EMAIL_ADDRESS"]
        app_password = st.secrets["EMAIL_APP_PASSWORD"]

        subject = f"🌱 Agri-Pulse Alert: {crop} needs attention"
        body = (
            f"Your {crop} field triggered an alert:\n\n"
            + "\n".join(f"- {r}" for r in reasons)
            + "\n\n--- Full Diagnosis ---\n\n"
            + diagnosis_text
        )

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, app_password)
            server.send_message(msg)

        return True, None
    except Exception as e:
        return False, str(e)


def call_gemini_safely(contents, model='gemini-3.6-flash'):
    try:
        response = client.models.generate_content(model=model, contents=contents)
        return True, response.text
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", err_str)
            wait_seconds = int(match.group(1)) if match else None
            if wait_seconds:
                friendly = f"⏳ AI quota reached. Try again in about {wait_seconds} seconds."
            else:
                friendly = "⏳ Daily AI quota reached (free tier). Try again later, or use a different API key."
            return False, friendly
        return False, f"An error occurred: {err_str}"


def api_register(username, password):
    try:
        r = requests.post(f"{API_URL}/register", json={"username": username, "password": password}, timeout=10)
        return r.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_login(username, password):
    try:
        r = requests.post(f"{API_URL}/login", json={"username": username, "password": password}, timeout=10)
        return r.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_get_settings(username):
    try:
        r = requests.get(f"{API_URL}/settings", params={"username": username}, timeout=10)
        return r.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_save_settings(username, **kwargs):
    try:
        requests.post(f"{API_URL}/settings", json={"username": username, **kwargs}, timeout=10)
    except Exception:
        pass


def maybe_sync_setting(field_name, value):
    cache_key = f"_synced_{field_name}"
    if st.session_state.get(cache_key) != value:
        st.session_state[cache_key] = value
        api_save_settings(st.session_state.username, **{field_name: value})


# ===========================================================
# LOGIN / SIGNUP GATE
# ===========================================================
if "username" not in st.session_state:
    st.session_state.username = None

if st.session_state.username is None:
    # ---- Hero / landing section ----
    st.markdown(
        """
        <div class="ap-hero">
            <div class="ap-hero-title">🌱 Agri-Pulse</div>
            <div class="ap-hero-subtitle">
                Your smart crop & soil doctor. Agri-Pulse reads live soil sensor data (moisture, pH, N-P-K),
                cross-checks it against your local weather forecast, and gives you an instant AI diagnosis —
                in English, Hindi, Kannada, Tamil, or Telugu — plus exactly what to buy and where to get it.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        st.markdown(
            """<div class="ap-feature-card"><h4>📡 Live Sensor Readings</h4>
            <p>Streams moisture, pH, and N-P-K data straight from your Wokwi/IoT setup, with trend charts over time.</p>
            </div>""",
            unsafe_allow_html=True,
        )
    with fcol2:
        st.markdown(
            """<div class="ap-feature-card"><h4>🩺 AI Diagnosis</h4>
            <p>Gemini analyzes your exact readings and weather outlook to flag deficiencies and recommend treatment.</p>
            </div>""",
            unsafe_allow_html=True,
        )
    with fcol3:
        st.markdown(
            """<div class="ap-feature-card"><h4>🛒 Actionable Fixes</h4>
            <p>Get product recommendations with direct links to nearby stores, Amazon, Flipkart, and BigHaat.</p>
            </div>""",
            unsafe_allow_html=True,
        )

    st.write("")
    st.subheader("Log in or create an account to continue")

    login_tab, signup_tab = st.tabs(["🔐 Login", "📝 Sign Up"])

    with login_tab:
        with st.form("login_form"):
            login_username = st.text_input("Username")
            login_password = st.text_input("Password", type="password")
            if st.form_submit_button("Log In"):
                if not login_username or not login_password:
                    st.error("Enter both username and password.")
                else:
                    result = api_login(login_username.strip(), login_password)
                    if result.get("status") == "success":
                        st.session_state.username = result["username"]
                        settings_result = api_get_settings(result["username"])
                        if settings_result.get("status") == "success":
                            s = settings_result["settings"]
                            st.session_state.selected_crop = s.get("crop") or "Tomato"
                            st.session_state.field_lat = s.get("field_lat")
                            st.session_state.field_lon = s.get("field_lon")
                            st.session_state.alert_email = s.get("alert_email") or ""
                            st.session_state.selected_language = s.get("selected_language") or "English"
                        st.rerun()
                    else:
                        st.error(result.get("message", "Login failed."))

    with signup_tab:
        with st.form("signup_form"):
            signup_username = st.text_input("Choose a username")
            signup_password = st.text_input("Choose a password (min 6 characters)", type="password")
            signup_confirm = st.text_input("Confirm password", type="password")
            if st.form_submit_button("Create Account"):
                if not signup_username or not signup_password:
                    st.error("Enter a username and password.")
                elif signup_password != signup_confirm:
                    st.error("Passwords don't match.")
                else:
                    result = api_register(signup_username.strip(), signup_password)
                    if result.get("status") == "success":
                        st.success("Account created! Switch to the Login tab to sign in.")
                    else:
                        st.error(result.get("message", "Registration failed."))

    st.markdown(
        '<div class="ap-footer">🌱 Agri-Pulse — built for farmers, powered by live sensors & AI</div>',
        unsafe_allow_html=True,
    )
    st.stop()

# ---------------------------------------------------------
# Crop list
# ---------------------------------------------------------
CROP_OPTIONS = [
    "Tomato", "Rice", "Wheat", "Maize", "Cotton", "Sugarcane",
    "Potato", "Onion", "Chili", "Soybean", "Groundnut", "Banana",
    "Mango", "Grapes", "Coffee", "Tea", "Millet", "Barley",
    "Chickpea", "Mustard"
]

if "selected_crop" not in st.session_state:
    st.session_state.selected_crop = CROP_OPTIONS[0]

if "field_lat" not in st.session_state:
    st.session_state.field_lat = None
if "field_lon" not in st.session_state:
    st.session_state.field_lon = None

if "last_diagnosis_time" not in st.session_state:
    st.session_state["last_diagnosis_time"] = 0.0

LANGUAGE_OPTIONS = ["English", "Hindi", "Kannada", "Tamil", "Telugu"]
LANG_MARKERS = {
    "English": "===LANG_ENGLISH===",
    "Hindi": "===LANG_HINDI===",
    "Kannada": "===LANG_KANNADA===",
    "Tamil": "===LANG_TAMIL===",
    "Telugu": "===LANG_TELUGU===",
}
LANG_SPEECH_CODES = {
    "English": "en-IN",
    "Hindi": "hi-IN",
    "Kannada": "kn-IN",
    "Tamil": "ta-IN",
    "Telugu": "te-IN",
}

if "selected_language" not in st.session_state:
    st.session_state.selected_language = "English"

with st.sidebar:
    st.success(f"👤 Logged in as **{st.session_state.username}**")
    if st.button("🚪 Log out"):
        for key in ["username", "selected_crop", "field_lat", "field_lon", "alert_email",
                    "selected_language", "messages", "last_diagnosis_translations",
                    "last_diagnosis_signature", "last_alert_signature",
                    "_synced_crop", "_synced_selected_language", "_synced_alert_email"]:
            st.session_state.pop(key, None)
        st.rerun()
    st.divider()

    st.header("🌾 Crop Selection")
    st.session_state.selected_crop = st.selectbox(
        "Select your crop",
        CROP_OPTIONS,
        index=CROP_OPTIONS.index(st.session_state.selected_crop),
    )
    maybe_sync_setting("crop", st.session_state.selected_crop)
    st.caption("This crop applies to both the chatbot and the live sensor diagnosis.")

    st.divider()
    st.header("🌐 Diagnosis Language")
    st.session_state.selected_language = st.selectbox(
        "Show diagnosis in",
        LANGUAGE_OPTIONS,
        index=LANGUAGE_OPTIONS.index(st.session_state.selected_language),
    )
    maybe_sync_setting("selected_language", st.session_state.selected_language)
    st.caption("Switches instantly — the diagnosis is generated in every language up front, so no extra Gemini call is used when you change this.")

    with st.expander("⚙️ Advanced settings (location & alerts)"):
        st.markdown("**📍 Field Location**")
        st.caption("Used to fetch weather for your field, and to find nearby stores after a diagnosis.")

        loc_method = st.radio(
            "How do you want to set your location?",
            ["Use my current location", "Enter manually"],
            key="loc_method"
        )

        if loc_method == "Use my current location":
            if not GEO_AVAILABLE:
                st.warning("Location detection isn't installed. Run: `pip install streamlit-js-eval`, or enter manually.")
            else:
                if not st.session_state.get("_geo_auto_attempted"):
                    st.session_state["_geo_auto_attempted"] = True
                    loc_data = get_geolocation()
                    if loc_data and "coords" in loc_data:
                        st.session_state.field_lat = loc_data["coords"]["latitude"]
                        st.session_state.field_lon = loc_data["coords"]["longitude"]
                        api_save_settings(
                            st.session_state.username,
                            field_lat=st.session_state.field_lat,
                            field_lon=st.session_state.field_lon
                        )

                if st.session_state.field_lat and st.session_state.field_lon:
                    st.success(f"Location auto-detected: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
                else:
                    st.warning("Waiting for browser location permission — allow it in the popup, or click below to retry.")

                if st.button("📡 Detect my location again"):
                    loc_data = get_geolocation()
                    if loc_data and "coords" in loc_data:
                        st.session_state.field_lat = loc_data["coords"]["latitude"]
                        st.session_state.field_lon = loc_data["coords"]["longitude"]
                        api_save_settings(
                            st.session_state.username,
                            field_lat=st.session_state.field_lat,
                            field_lon=st.session_state.field_lon
                        )
                        st.success(f"Location updated: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
                    else:
                        st.error("Couldn't get location. Check browser permissions, or enter manually.")

        else:  # Enter manually
            manual_lat = st.number_input(
                "Latitude",
                value=st.session_state.field_lat if st.session_state.field_lat else 12.9716,
                format="%.6f"
            )
            manual_lon = st.number_input(
                "Longitude",
                value=st.session_state.field_lon if st.session_state.field_lon else 77.5946,
                format="%.6f"
            )
            if st.button("✅ Save location"):
                st.session_state.field_lat = manual_lat
                st.session_state.field_lon = manual_lon
                api_save_settings(st.session_state.username, field_lat=manual_lat, field_lon=manual_lon)
                st.success(f"Location saved: {manual_lat:.4f}, {manual_lon:.4f}")

        if st.session_state.field_lat and st.session_state.field_lon:
            st.caption(f"📍 Current: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
        else:
            st.caption("⚠️ No location set yet — weather and nearby-store links won't work until you set one.")

        st.markdown("**📧 Email Alerts**")
        st.session_state.setdefault("alert_email", "")
        st.session_state.alert_email = st.text_input(
            "Send alerts to",
            value=st.session_state.alert_email,
            placeholder="you@example.com"
        )
        maybe_sync_setting("alert_email", st.session_state.alert_email)
        st.caption("You'll get an email only when a real issue is detected — not on every check.")

st.title("🌱 Agri-Pulse: Smart Crop & Soil Doctor")

tab1, tab2, tab3 = st.tabs(["💬 AI Chatbot", "📊 Live Sensor & Diagnosis", "📈 History & Analytics"])

# ===========================================================
# TAB 1 — Chatbot with image upload + paste + voice input
# ===========================================================
with tab1:
    st.subheader("AI Crop & Plant Doctor Chat")
    st.write(f"Ask anything about **{st.session_state.selected_crop}**, or attach a photo for visual diagnosis.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message.get("image") is not None:
                st.image(message["image"], width=300)
            st.markdown(message["content"])

    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded_file = st.file_uploader(
            "Attach an image (optional)",
            type=["jpg", "jpeg", "png"],
            key="chat_file_upload"
        )
    with col2:
        paste_data = None
        if PASTE_AVAILABLE:
            st.write("Or paste from clipboard:")
            paste_data = paste(label="📋 Paste Image", key="chat_image_paste")
        else:
            st.caption("Clipboard paste unavailable — use upload instead.")

    current_image = None
    if uploaded_file is not None:
        current_image = Image.open(uploaded_file)
    elif paste_data is not None:
        try:
            header, encoded = paste_data.split(",", 1)
            binary_data = base64.b64decode(encoded)
            current_image = Image.open(BytesIO(binary_data))
        except Exception:
            st.warning("Couldn't read pasted image — try uploading instead.")

    if current_image is not None:
        st.image(current_image, caption="Attached image ready", width=150)

    if "voice_armed" not in st.session_state:
        st.session_state["voice_armed"] = False
    if "voice_key_counter" not in st.session_state:
        st.session_state["voice_key_counter"] = 0

    if GEO_AVAILABLE:
        vcol1, vcol2 = st.columns([1, 2])
        with vcol1:
            voice_lang = st.selectbox(
                "🎙️ Voice language",
                LANGUAGE_OPTIONS,
                index=LANGUAGE_OPTIONS.index(st.session_state.selected_language),
                key="chat_voice_lang"
            )
        with vcol2:
            st.write("")
            if st.button("🎤 Tap to Speak"):
                st.session_state["voice_armed"] = True
                st.session_state["voice_key_counter"] += 1

        speech_code = LANG_SPEECH_CODES.get(voice_lang, "en-IN")
        if st.session_state["voice_armed"]:
            speech_js = f"""
            new Promise((resolve) => {{
                try {{
                    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
                    if (!SR) {{ resolve("SPEECH_NOT_SUPPORTED"); return; }}
                    const recognition = new SR();
                    recognition.lang = "{speech_code}";
                    recognition.interimResults = false;
                    recognition.maxAlternatives = 1;
                    recognition.onresult = (event) => resolve(event.results[0][0].transcript);
                    recognition.onerror = (event) => resolve("SPEECH_ERROR:" + event.error);
                    recognition.start();
                }} catch (e) {{
                    resolve("SPEECH_ERROR:" + e.message);
                }}
            }})
            """
        else:
            speech_js = "null"

        transcript = streamlit_js_eval(
            js_expressions=speech_js,
            key=f"voice_listener_{st.session_state['voice_key_counter']}"
        )

        if st.session_state["voice_armed"] and transcript:
            st.session_state["voice_armed"] = False
            if transcript == "SPEECH_NOT_SUPPORTED":
                st.error("Your browser doesn't support voice input — try Chrome or Edge, or type instead.")
            elif isinstance(transcript, str) and transcript.startswith("SPEECH_ERROR"):
                st.error(f"Couldn't capture voice ({transcript.split(':', 1)[-1]}) — check mic permissions, or type instead.")
            else:
                st.session_state["pending_voice_prompt"] = transcript

        if st.session_state.get("pending_voice_prompt"):
            st.info(f"🎤 Heard: \"{st.session_state['pending_voice_prompt']}\"")
    else:
        st.caption("Voice input isn't installed. Run: `pip install streamlit-js-eval`, or type your question below.")

    prompt = st.chat_input("Ask about your crops, or send an attached image for diagnosis...")

    if not prompt and st.session_state.get("pending_voice_prompt"):
        prompt = st.session_state.pop("pending_voice_prompt")

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt, "image": current_image})
        with st.chat_message("user"):
            if current_image is not None:
                st.image(current_image, width=300)
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing with Gemini..."):
                contents_payload = []
                if current_image is not None:
                    contents_payload.append(current_image)

                crop_context = (
                    f"The user's selected crop is: {st.session_state.selected_crop}. "
                    f"Take this into account in your answer unless the question clearly refers to a different crop. "
                )
                contents_payload.append(crop_context + prompt)

                success, result = call_gemini_safely(contents_payload)
                if success:
                    st.markdown(result)
                    st.session_state.messages.append({"role": "assistant", "content": result, "image": None})
                else:
                    st.error(result)

# ===========================================================
# TAB 2 — Live sensor dashboard + automatic diagnosis
# ===========================================================
with tab2:
    st.subheader("📡 Live Wokwi Sensor Data")
    st.caption(f"Diagnosing for: **{st.session_state.selected_crop}**")

    def parse_multilang_diagnosis(raw_text):
        match = re.search(r"PRODUCT_QUERY:\s*(.+)", raw_text)
        product_query = match.group(1).strip() if match else None
        text_without_pq = re.sub(r"PRODUCT_QUERY:\s*.+", "", raw_text).strip()

        marker_items = list(LANG_MARKERS.items())
        translations = {}
        for lang, marker in marker_items:
            start_idx = text_without_pq.find(marker)
            if start_idx == -1:
                continue
            start_idx += len(marker)
            end_idx = len(text_without_pq)
            for other_lang, other_marker in marker_items:
                if other_marker == marker:
                    continue
                idx = text_without_pq.find(other_marker, start_idx)
                if idx != -1 and idx < end_idx:
                    end_idx = idx
            translations[lang] = text_without_pq[start_idx:end_idx].strip()

        if not translations:
            translations["English"] = text_without_pq

        return translations, product_query, text_without_pq

    def build_shopping_links(product_query, lat, lon):
        q = quote(f"{product_query} store")
        links = {}
        if lat and lon:
            links["nearby_maps"] = f"https://www.google.com/maps/search/{q}/@{lat},{lon},14z"
        q_online = quote(product_query)
        links["amazon"] = f"https://www.amazon.in/s?k={q_online}"
        links["flipkart"] = f"https://www.flipkart.com/search?q={q_online}"
        links["bighaat"] = f"https://www.google.com/search?q={q_online}+site:bighaat.com"
        return links

    def render_voice_readout(text, language):
        speech_lang = LANG_SPEECH_CODES.get(language, "en-IN")
        safe_text = json.dumps(text)
        html_code = f"""
        <div style="display:flex; gap:8px; align-items:center; font-family:sans-serif;">
          <button onclick='
            var synth = window.speechSynthesis;
            synth.cancel();
            var utter = new SpeechSynthesisUtterance({safe_text});
            utter.lang = "{speech_lang}";
            synth.speak(utter);
          ' style="padding:8px 14px;border-radius:8px;border:none;background:#2e7d32;color:white;cursor:pointer;font-size:14px;">
            🔊 Read Aloud
          </button>
          <button onclick='window.speechSynthesis.cancel();'
            style="padding:8px 14px;border-radius:8px;border:none;background:#c62828;color:white;cursor:pointer;font-size:14px;">
            ⏹ Stop
          </button>
        </div>
        """
        components.html(html_code, height=50)

    def run_gemini_diagnosis(moisture, ph, nitrogen, phosphorus, potassium, crop, forecast, signature):
        with st.spinner("Analyzing live sensor data with Gemini..."):
            weather_context = ""
            if forecast:
                today = forecast[0]
                tomorrow = forecast[1] if len(forecast) > 1 else None
                weather_context = (
                    f"Weather context: today's forecast is {today['temp_min']:.0f}-{today['temp_max']:.0f}°C "
                    f"with a {today['rain_chance']}% chance of rain. "
                )
                if tomorrow:
                    weather_context += f"Tomorrow has a {tomorrow['rain_chance']}% chance of rain. "
                weather_context += (
                    "Factor this into your irrigation advice — e.g. hold off watering if significant rain "
                    "is expected soon, or flag urgency to irrigate if no rain is coming and moisture is low. "
                )

            sensor_query = (
                f"A live IoT sensor reading reports the following exact metrics for a {crop} crop: "
                f"- Soil Moisture: {moisture}% "
                f"- Soil pH: {ph} "
                f"- Nitrogen (N): {nitrogen}% "
                f"- Phosphorus (P): {phosphorus}% "
                f"- Potassium (K): {potassium}% "
                f"{weather_context}"
                "Analyze whether these exact levels indicate severe nutritional deficiencies, water stress, or pH imbalances "
                f"specifically for {crop}. "
                "Provide: 1) Detailed Diagnosis based on these exact values, 2) Exact treatment or fertilizer solutions required, "
                "including whether to irrigate now given the weather outlook, and 3) Recommended online platforms "
                "(like BigHaat or AgriBegri) and local store types. "
                "\n\nWrite this full diagnosis separately in EACH of the following 5 languages: English, Hindi, "
                "Kannada, Tamil, Telugu. Each language's full diagnosis (all 3 parts above) must start on its own "
                "line with EXACTLY this marker and nothing else on that line, then the diagnosis text in that "
                "language follows on the next lines:\n"
                "===LANG_ENGLISH===\n"
                "===LANG_HINDI===\n"
                "===LANG_KANNADA===\n"
                "===LANG_TAMIL===\n"
                "===LANG_TELUGU===\n"
                "Use native script for each language (Devanagari for Hindi, Kannada script, Tamil script, Telugu script) — "
                "not transliteration. "
                "\n\nFinally, on its own separate line at the very end of your entire response (after all 5 language "
                "sections), output exactly this format (no extra words on that line): "
                "PRODUCT_QUERY: <a short 2-4 word generic product name in English to search for, e.g. 'Urea fertilizer' or "
                "'NPK 19-19-19', or 'None' if no product purchase is needed>"
            )

            success, result = call_gemini_safely([sensor_query])
            if not success:
                st.error(result)
                return

            translations, product_query, raw_multilang_text = parse_multilang_diagnosis(result)
            if product_query and product_query.strip().lower() == "none":
                product_query = None

            st.session_state["last_diagnosis_signature"] = signature
            st.session_state["last_diagnosis_translations"] = translations
            st.session_state["last_diagnosis_product_query"] = product_query
            st.session_state["last_diagnosis_time"] = time.time()

            alert_reasons = check_alert_condition(moisture, ph, nitrogen, phosphorus, potassium)
            alert_signature = tuple(alert_reasons)
            last_alert_signature = st.session_state.get("last_alert_signature")

            if alert_reasons and alert_signature != last_alert_signature:
                recipient = st.session_state.get("alert_email")
                if recipient:
                    english_text = translations.get("English", raw_multilang_text)
                    sent, err = send_alert_email(recipient, crop, alert_reasons, english_text)
                    st.session_state["last_alert_signature"] = alert_signature
                    if sent:
                        st.toast(f"📧 Alert email sent to {recipient}")
                    else:
                        st.warning(f"Could not send alert email: {err}")

            try:
                requests.post(
                    f"{API_URL}/save-diagnosis",
                    json={
                        "crop": crop,
                        "moisture": moisture,
                        "ph": ph,
                        "n": nitrogen,
                        "p": phosphorus,
                        "k": potassium,
                        "diagnosis_text": raw_multilang_text,
                        "product_query": product_query,
                        "username": st.session_state.username,
                    },
                    timeout=5
                )
            except Exception:
                pass

    @st.fragment(run_every=2)
    def live_sensor_dashboard():
        try:
            response = requests.get(f"{API_URL}/sensor-data", timeout=5)
        except Exception as e:
            st.error(f"Connection error: {e}")
            return

        if response.status_code != 200:
            st.error(f"FastAPI error: {response.status_code}")
            return

        try:
            data = response.json()
        except ValueError:
            st.warning("⚠️ Got a non-JSON response from the server (likely a Codespaces auth page). Retrying next cycle...")
            return

        moisture = data["moisture"]
        ph = data["ph"]
        nitrogen = data["n"]
        phosphorus = data["p"]
        potassium = data["k"]

        crop = st.session_state.selected_crop

        st.success("🟢 Connected to FastAPI")

        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("💧 Moisture", f"{moisture}%")
        with col2:
            st.metric("🧪 pH", ph)
        with col3:
            st.metric("🌿 Nitrogen", f"{nitrogen}%")
        with col4:
            st.metric("🌱 Phosphorus", f"{phosphorus}%")
        with col5:
            st.metric("🍃 Potassium", f"{potassium}%")

        st.info(f"🌾 Crop: {crop}")

        forecast = None
        if st.session_state.field_lat and st.session_state.field_lon:
            try:
                weather_resp = requests.get(
                    f"{API_URL}/weather-forecast",
                    params={"lat": st.session_state.field_lat, "lon": st.session_state.field_lon},
                    timeout=5
                )
                if weather_resp.status_code == 200:
                    weather_json = weather_resp.json()
                    if weather_json.get("status") == "success":
                        forecast = weather_json["forecast"]
            except Exception:
                pass

        st.markdown("### ⛅ Weather Forecast")
        if forecast:
            wcols = st.columns(len(forecast))
            labels = ["Today", "Tomorrow", "Day 3", "Day 4"]
            for i, day in enumerate(forecast):
                with wcols[i]:
                    st.markdown(f"**{labels[i]}**")
                    st.caption(day["date"])
                    st.write(f"🌡️ {day['temp_min']:.0f}° – {day['temp_max']:.0f}°")
                    st.write(f"🌧️ {day['rain_chance']}%")
        else:
            st.caption("Set your field location in the sidebar to see weather here.")

        try:
            hist_response = requests.get(f"{API_URL}/history", timeout=5)
            if hist_response.status_code == 200:
                try:
                    hist_data = hist_response.json()["readings"]
                except ValueError:
                    hist_data = None
                    st.warning("⚠️ Could not parse history response (non-JSON). Retrying next cycle...")

                if hist_data:
                    df = pd.DataFrame(hist_data)
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df = df.set_index("timestamp")

                    st.markdown("### 📈 Sensor Trends")
                    st.line_chart(df[["moisture", "n", "p", "k"]])
                    st.line_chart(df[["ph"]])
        except Exception as e:
            st.warning(f"Could not load history: {e}")

        weather_signature = tuple(
            (d["date"], d["rain_chance"]) for d in forecast
        ) if forecast else None
        current_signature = (
            round(moisture), round(ph, 1), round(nitrogen),
            round(phosphorus), round(potassium), crop, weather_signature
        )
        last_signature = st.session_state.get("last_diagnosis_signature")

        now = time.time()
        cooldown_remaining = DIAGNOSIS_COOLDOWN_SECONDS - (now - st.session_state["last_diagnosis_time"])
        cooldown_ready = cooldown_remaining <= 0

        if current_signature != last_signature and cooldown_ready:
            run_gemini_diagnosis(moisture, ph, nitrogen, phosphorus, potassium, crop, forecast, current_signature)
        elif current_signature != last_signature and not cooldown_ready:
            st.caption(f"⏳ Sensor values shifted — next auto-diagnosis available in {int(cooldown_remaining)}s (quota protection).")

        if st.button("🔄 Re-diagnose now"):
            run_gemini_diagnosis(moisture, ph, nitrogen, phosphorus, potassium, crop, forecast, current_signature)

        if "last_diagnosis_translations" in st.session_state:
            selected_lang = st.session_state.selected_language
            translations = st.session_state["last_diagnosis_translations"]
            display_text = translations.get(selected_lang) or translations.get("English", "Diagnosis text unavailable.")

            st.markdown("### 🩺 Diagnosis & Treatment")
            if selected_lang not in translations:
                st.caption(f"⚠️ {selected_lang} translation wasn't returned this time — showing English instead.")
            st.markdown(display_text)
            render_voice_readout(display_text, selected_lang)

            product_query = st.session_state.get("last_diagnosis_product_query")
            if product_query:
                st.markdown("### 🛒 Where to Get It")
                st.caption(f"Recommended: **{product_query}**")

                links = build_shopping_links(
                    product_query,
                    st.session_state.field_lat,
                    st.session_state.field_lon
                )

                scol1, scol2, scol3, scol4 = st.columns(4)
                with scol1:
                    if "nearby_maps" in links:
                        st.link_button("📍 Nearby Stores", links["nearby_maps"], use_container_width=True)
                    else:
                        st.caption("Set your location in the sidebar to see nearby stores.")
                with scol2:
                    st.link_button("🛍️ Amazon", links["amazon"], use_container_width=True)
                with scol3:
                    st.link_button("🛒 Flipkart", links["flipkart"], use_container_width=True)
                with scol4:
                    st.link_button("🌾 BigHaat", links["bighaat"], use_container_width=True)

        st.markdown("### 📜 Diagnosis History")
        try:
            history_resp = requests.get(
                f"{API_URL}/diagnosis-history",
                params={"limit": 20, "username": st.session_state.username},
                timeout=5
            )
            if history_resp.status_code == 200:
                try:
                    history_data = history_resp.json()["history"]
                except ValueError:
                    history_data = None
                    st.warning("⚠️ Could not parse diagnosis history (non-JSON). Retrying next cycle...")

                if history_data:
                    hist_selected_lang = st.session_state.selected_language
                    for entry in history_data:
                        ts_display = entry["timestamp"].replace("T", " ").split(".")[0]
                        with st.expander(f"🩺 {ts_display} — {entry['crop']}"):
                            hcol1, hcol2, hcol3, hcol4, hcol5 = st.columns(5)
                            hcol1.metric("💧 Moisture", f"{entry['moisture']}%")
                            hcol2.metric("🧪 pH", entry["ph"])
                            hcol3.metric("🌿 N", f"{entry['n']}%")
                            hcol4.metric("🌱 P", f"{entry['p']}%")
                            hcol5.metric("🍃 K", f"{entry['k']}%")

                            entry_translations, _entry_pq, _raw = parse_multilang_diagnosis(entry["diagnosis_text"])
                            entry_display_text = entry_translations.get(hist_selected_lang) or entry_translations.get("English", entry["diagnosis_text"])
                            if hist_selected_lang not in entry_translations:
                                st.caption(f"⚠️ {hist_selected_lang} unavailable for this entry — showing English.")
                            st.markdown(entry_display_text)
                            render_voice_readout(entry_display_text, hist_selected_lang)

                            if entry.get("product_query"):
                                st.caption(f"Recommended: {entry['product_query']}")
                else:
                    st.caption("No diagnoses logged yet — once one runs, it'll show up here.")
            else:
                st.caption(f"Could not load diagnosis history (server returned {history_resp.status_code}).")
        except Exception as e:
            st.warning(f"Could not load diagnosis history: {e}")

    live_sensor_dashboard()

# ===========================================================
# TAB 3 — History & Analytics
# ===========================================================
with tab3:
    st.subheader("📈 Diagnosis History & Analytics")
    st.caption("Search, filter, and analyze your past diagnoses.")

    fcol1, fcol2, fcol3 = st.columns([1, 1, 1])
    with fcol1:
        date_range = st.date_input(
            "Date range",
            value=(),
            key="analytics_date_range",
            help="Leave empty to include all dates."
        )
    with fcol2:
        crop_filter_options = ["All crops"] + CROP_OPTIONS
        crop_filter = st.selectbox("Crop", crop_filter_options, key="analytics_crop_filter")
    with fcol3:
        analytics_limit = st.number_input("Max records", min_value=10, max_value=500, value=100, step=10)

    start_date_str, end_date_str = None, None
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date_str = date_range[0].isoformat()
        end_date_str = date_range[1].isoformat()
    elif isinstance(date_range, tuple) and len(date_range) == 1:
        start_date_str = date_range[0].isoformat()

    params = {"limit": int(analytics_limit), "username": st.session_state.username}
    if crop_filter != "All crops":
        params["crop"] = crop_filter
    if start_date_str:
        params["start_date"] = start_date_str
    if end_date_str:
        params["end_date"] = end_date_str

    try:
        analytics_resp = requests.get(f"{API_URL}/diagnosis-history", params=params, timeout=10)
        analytics_data = analytics_resp.json().get("history", []) if analytics_resp.status_code == 200 else []
    except Exception as e:
        st.error(f"Could not load history: {e}")
        analytics_data = []

    if not analytics_data:
        st.info("No diagnoses match these filters yet.")
    else:
        adf = pd.DataFrame(analytics_data)
        adf["timestamp"] = pd.to_datetime(adf["timestamp"])
        adf = adf.sort_values("timestamp")

        st.success(f"Showing {len(adf)} diagnosis record(s).")

        st.markdown("#### 🔍 Trend Summary")
        if len(adf) >= 2:
            metric_labels = {
                "moisture": ("💧 Moisture", "%"),
                "ph": ("🧪 pH", ""),
                "n": ("🌿 Nitrogen", "%"),
                "p": ("🌱 Phosphorus", "%"),
                "k": ("🍃 Potassium", "%"),
            }
            insights = []
            for col, (label, unit) in metric_labels.items():
                first_val = adf[col].iloc[0]
                last_val = adf[col].iloc[-1]
                delta = last_val - first_val
                if abs(delta) < (0.5 if col == "ph" else 2):
                    continue
                direction = "declining" if delta < 0 else "rising"
                span_days = max((adf["timestamp"].iloc[-1] - adf["timestamp"].iloc[0]).days, 1)
                insights.append(
                    f"**{label}** has been {direction} — {first_val}{unit} → {last_val}{unit} "
                    f"over the last {span_days} day(s)."
                )

            if insights:
                for line in insights:
                    st.markdown(f"- {line}")
            else:
                st.caption("No significant trends detected in this range — readings have stayed roughly stable.")
        else:
            st.caption("Need at least 2 records in this range to compute a trend.")

        st.markdown("#### 📊 Charts")
        chart_df = adf.set_index("timestamp")
        st.line_chart(chart_df[["moisture", "n", "p", "k"]])
        st.line_chart(chart_df[["ph"]])

        st.markdown("#### ⬇️ Export")
        ecol1, ecol2 = st.columns(2)

        with ecol1:
            csv_bytes = adf.drop(columns=["diagnosis_text"], errors="ignore").to_csv(index=False).encode("utf-8")
            st.download_button(
                "📄 Download CSV",
                data=csv_bytes,
                file_name="agri_pulse_diagnosis_history.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with ecol2:
            def build_pdf_report(df):
                def safe(text, max_word_len=40):
                    """Make text safe for the core Helvetica font (Latin-1 only)
                    and guarantee every 'word' is short enough to wrap inside
                    the page (fixes 'Not enough horizontal space' crashes)."""
                    text = "" if text is None else str(text)

                    replacements = {
                        "\u2014": "-",   # em dash —
                        "\u2013": "-",   # en dash –
                        "\u2018": "'",   # left single quote '
                        "\u2019": "'",   # right single quote '
                        "\u201c": '"',   # left double quote "
                        "\u201d": '"',   # right double quote "
                        "\u2026": "...", # ellipsis …
                        "\u2192": "->",  # right arrow →
                    }
                    for bad, good in replacements.items():
                        text = text.replace(bad, good)

                    # Drop anything Helvetica can't encode (emoji etc.)
                    text = text.encode("latin-1", "ignore").decode("latin-1")

                    # Break up any unbroken "word" too long to fit on a line
                    # (long URLs, run-on AI text with no spaces, etc.)
                    def break_long_word(word):
                        if len(word) <= max_word_len:
                            return word
                        return " ".join(
                            word[i:i + max_word_len] for i in range(0, len(word), max_word_len)
                        )

                    return " ".join(break_long_word(w) for w in text.split(" "))

                pdf = FPDF()
                pdf.set_auto_page_break(auto=True, margin=15)
                pdf.add_page()

                def cell_ln(text, size=10, bold=False):
                    """cell() that always starts at the left margin and
                    always moves to a fresh line at the left margin after."""
                    pdf.set_x(pdf.l_margin)
                    pdf.set_font("Helvetica", "B" if bold else "", size)
                    pdf.cell(0, 8, safe(text), new_x="LMARGIN", new_y="NEXT")

                def multi_ln(text, size=10, bold=False, line_h=6):
                    pdf.set_x(pdf.l_margin)
                    pdf.set_font("Helvetica", "B" if bold else "", size)
                    pdf.multi_cell(0, line_h, safe(text), new_x="LMARGIN", new_y="NEXT")

                cell_ln("Agri-Pulse Diagnosis Report", size=16, bold=True)
                cell_ln(f"User: {st.session_state.username}")
                cell_ln(f"Records: {len(df)}")
                pdf.ln(4)

                for _, row in df.iterrows():
                    try:
                        ts = row["timestamp"].strftime("%Y-%m-%d %H:%M")
                        multi_ln(f"{ts} - {row['crop']}", size=11, bold=True, line_h=7)
                        multi_ln(
                            f"Moisture: {row['moisture']}%  pH: {row['ph']}  "
                            f"N: {row['n']}%  P: {row['p']}%  K: {row['k']}%",
                            size=10,
                        )
                        pq = row.get("product_query")
                        if pq and str(pq).strip().lower() not in ("none", "nan", ""):
                            multi_ln(f"Recommended: {pq}", size=10)
                        pdf.ln(3)
                    except Exception as row_err:
                        multi_ln(f"[Skipped one record: {row_err}]", size=8)
                        pdf.ln(2)

                return bytes(pdf.output())

            pdf_bytes = build_pdf_report(adf)
            st.download_button(
                "📕 Download PDF Report",
                data=pdf_bytes,
                file_name="agri_pulse_diagnosis_report.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

        st.markdown("#### 📋 Records")
        display_df = adf[["timestamp", "crop", "moisture", "ph", "n", "p", "k", "product_query"]].copy()
        display_df["timestamp"] = display_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(display_df, use_container_width=True, hide_index=True)

st.markdown(
    '<div class="ap-footer">🌱 Agri-Pulse — built for farmers, powered by live sensors & AI</div>',
    unsafe_allow_html=True,
)