import streamlit as st
import requests
import pandas as pd
import time
from PIL import Image
from io import BytesIO
import base64
from google import genai

# Optional: clipboard paste support. Falls back gracefully if not installed.
try:
    from st_img_pastebutton import paste
    PASTE_AVAILABLE = True
except ImportError:
    PASTE_AVAILABLE = False

# Optional: browser geolocation. Falls back gracefully if not installed.
try:
    from streamlit_js_eval import get_geolocation
    GEO_AVAILABLE = True
except ImportError:
    GEO_AVAILABLE = False

st.set_page_config(page_title="Agri-Pulse", page_icon="🌱")

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# ---------------------------------------------------------
# REPLACE with your current Codespace port-8000 forwarded URL
# (base URL only, no /update or /sensor-data on the end)
# ---------------------------------------------------------
API_URL = "https://urban-succotash-4j594pvvq5rx37r6p-8000.app.github.dev"

# ---------------------------------------------------------
# Gemini quota protection settings
# ---------------------------------------------------------
DIAGNOSIS_COOLDOWN_SECONDS = 300  # 5 min minimum between auto-triggered Gemini calls

# ---------------------------------------------------------
# Crop list — shared across both tabs via sidebar
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

with st.sidebar:
    st.header("🌾 Crop Selection")
    st.session_state.selected_crop = st.selectbox(
        "Select your crop",
        CROP_OPTIONS,
        index=CROP_OPTIONS.index(st.session_state.selected_crop),
    )
    st.caption("This crop applies to both the chatbot and the live sensor diagnosis.")

    st.divider()
    st.header("📍 Field Location")
    st.caption("Used to fetch weather for your field. Set this once.")

    loc_method = st.radio(
        "How do you want to set your location?",
        ["Use my current location", "Enter manually"],
        key="loc_method"
    )

    if loc_method == "Use my current location":
        if not GEO_AVAILABLE:
            st.warning("Location detection isn't installed. Run: `pip install streamlit-js-eval`, or enter manually.")
        else:
            if st.button("📡 Detect my location"):
                loc_data = get_geolocation()
                if loc_data and "coords" in loc_data:
                    st.session_state.field_lat = loc_data["coords"]["latitude"]
                    st.session_state.field_lon = loc_data["coords"]["longitude"]
                    st.success(f"Location set: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
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
            st.success(f"Location saved: {manual_lat:.4f}, {manual_lon:.4f}")

    if st.session_state.field_lat and st.session_state.field_lon:
        st.caption(f"📍 Current: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
    else:
        st.caption("⚠️ No location set yet — weather won't load until you set one.")

st.title("🌱 Agri-Pulse: Smart Crop & Soil Doctor")

tab1, tab2 = st.tabs(["💬 AI Chatbot", "📊 Live Sensor & Diagnosis"])

# ===========================================================
# TAB 1 — Chatbot with image upload + paste
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

    prompt = st.chat_input("Ask about your crops, or send an attached image for diagnosis...")

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt, "image": current_image})
        with st.chat_message("user"):
            if current_image is not None:
                st.image(current_image, width=300)
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing with Gemini..."):
                try:
                    contents_payload = []
                    if current_image is not None:
                        contents_payload.append(current_image)

                    crop_context = (
                        f"The user's selected crop is: {st.session_state.selected_crop}. "
                        f"Take this into account in your answer unless the question clearly refers to a different crop. "
                    )
                    contents_payload.append(crop_context + prompt)

                    response = client.models.generate_content(
                        model='gemini-3.6-flash',
                        contents=contents_payload
                    )
                    bot_reply = response.text
                    st.markdown(bot_reply)
                    st.session_state.messages.append({"role": "assistant", "content": bot_reply, "image": None})
                except Exception as e:
                    st.error(f"An error occurred: {e}")

# ===========================================================
# TAB 2 — Live sensor dashboard + automatic diagnosis
# ===========================================================
with tab2:
    st.subheader("📡 Live Wokwi Sensor Data")
    st.caption(f"Diagnosing for: **{st.session_state.selected_crop}**")

    def run_gemini_diagnosis(moisture, ph, nitrogen, phosphorus, potassium, crop, forecast, signature):
        """Calls Gemini for a diagnosis and stores the result + timestamp in session_state."""
        with st.spinner("Analyzing live sensor data with Gemini..."):
            try:
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
                    "(like BigHaat or AgriBegri) and local store types."
                )
                diag_response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[sensor_query]
                )
                st.session_state["last_diagnosis_signature"] = signature
                st.session_state["last_diagnosis_result"] = diag_response.text
                st.session_state["last_diagnosis_time"] = time.time()
            except Exception as e:
                st.error(f"An error occurred: {e}")

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

        # Crop comes from the user's sidebar selection, not the sensor payload
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

        # -----------------------------------------------------
        # Weather card — uses field_lat/field_lon from sidebar
        # -----------------------------------------------------
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

        # ---------------------------------------------------------
        # Historical trend chart
        # ---------------------------------------------------------
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

        # -----------------------------------------------------------------
        # Gemini diagnosis trigger — rounded signature + cooldown to
        # protect the free-tier quota (20 requests/day). Sensor noise on
        # every 2s refresh no longer counts as a "real" change.
        # -----------------------------------------------------------------
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

        # Manual override — lets you force a fresh diagnosis on demand
        # during the demo, ignoring the cooldown (still burns 1 quota unit).
        if st.button("🔄 Re-diagnose now"):
            run_gemini_diagnosis(moisture, ph, nitrogen, phosphorus, potassium, crop, forecast, current_signature)

        if "last_diagnosis_result" in st.session_state:
            st.markdown("### 🩺 Diagnosis & Treatment")
            st.markdown(st.session_state["last_diagnosis_result"])

    live_sensor_dashboard()