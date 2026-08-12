import streamlit as st
import streamlit.components.v1 as components
import requests
import pandas as pd
import time
import re
import json
from urllib.parse import quote
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

# Optional: browser geolocation + generic JS eval (used for voice input). Falls back gracefully if not installed.
try:
    from streamlit_js_eval import get_geolocation, streamlit_js_eval
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
    st.header("🌾 Crop Selection")
    st.session_state.selected_crop = st.selectbox(
        "Select your crop",
        CROP_OPTIONS,
        index=CROP_OPTIONS.index(st.session_state.selected_crop),
    )
    st.caption("This crop applies to both the chatbot and the live sensor diagnosis.")

    st.divider()
    st.header("🌐 Diagnosis Language")
    st.session_state.selected_language = st.selectbox(
        "Show diagnosis in",
        LANGUAGE_OPTIONS,
        index=LANGUAGE_OPTIONS.index(st.session_state.selected_language),
    )
    st.caption("Switches instantly — the diagnosis is generated in every language up front, so no extra Gemini call is used when you change this.")

    st.divider()
    st.header("📍 Field Location")
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
            # Auto-detect once when the app loads (no button click needed).
            # A field doesn't move, so we only need this once per session —
            # re-detecting is still available via the button below if you
            # ever need to correct it (e.g. wrong browser permission cache).
            if not st.session_state.get("_geo_auto_attempted"):
                st.session_state["_geo_auto_attempted"] = True
                loc_data = get_geolocation()
                if loc_data and "coords" in loc_data:
                    st.session_state.field_lat = loc_data["coords"]["latitude"]
                    st.session_state.field_lon = loc_data["coords"]["longitude"]

            if st.session_state.field_lat and st.session_state.field_lon:
                st.success(f"Location auto-detected: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
            else:
                st.warning("Waiting for browser location permission — allow it in the popup, or click below to retry.")

            if st.button("📡 Detect my location again"):
                loc_data = get_geolocation()
                if loc_data and "coords" in loc_data:
                    st.session_state.field_lat = loc_data["coords"]["latitude"]
                    st.session_state.field_lon = loc_data["coords"]["longitude"]
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
            st.success(f"Location saved: {manual_lat:.4f}, {manual_lon:.4f}")

    if st.session_state.field_lat and st.session_state.field_lon:
        st.caption(f"📍 Current: {st.session_state.field_lat:.4f}, {st.session_state.field_lon:.4f}")
    else:
        st.caption("⚠️ No location set yet — weather and nearby-store links won't work until you set one.")

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

    # -----------------------------------------------------------------
    # Voice input — uses the browser's native SpeechRecognition API via
    # streamlit_js_eval to transcribe speech to text, in whichever
    # language you pick. No backend call, no Gemini quota used for the
    # transcription itself — only the eventual chat reply uses Gemini.
    #
    # IMPORTANT: streamlit_js_eval has a known bug where calling it from
    # inside an if-branch (e.g. `if st.button(...): streamlit_js_eval(...)`)
    # loses the resolved value, because on the rerun that delivers the
    # result, the button is no longer "clicked" so that branch — and the
    # component call inside it — no longer exists in the tree. The fix:
    # the button only sets a session_state flag; the actual js_eval call
    # below is UNCONDITIONAL (always in the tree every rerun), and checks
    # the flag internally to decide whether to actually touch the mic.
    # -----------------------------------------------------------------
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
            st.write("")  # vertical spacing to align button with selectbox
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
            speech_js = "null"  # no-op — doesn't touch the mic when not armed

        # Key changes each time the button is clicked, forcing a fresh
        # component mount instead of replaying a stale cached result.
        transcript = streamlit_js_eval(
            js_expressions=speech_js,
            key=f"voice_listener_{st.session_state['voice_key_counter']}"
        )

        if st.session_state["voice_armed"] and transcript:
            st.session_state["voice_armed"] = False  # disarm so it stops listening on future reruns
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

    # A typed prompt takes priority if both exist; otherwise use the voice
    # transcript and clear it so it doesn't resend on the next rerun.
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

    def parse_multilang_diagnosis(raw_text):
        """
        Pulls the PRODUCT_QUERY line out, then splits the remaining text into
        a {language: diagnosis_text} dict using the ===LANG_XXX=== markers
        Gemini was asked to output. If Gemini didn't follow the format (e.g.
        older cached responses, or a model hiccup), everything falls back
        into "English" so the app still shows something sensible.
        Returns (translations_dict, product_query, raw_text_without_pq).
        """
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
        """Builds nearby-store and online-store search links for the recommended product."""
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
        """
        Renders a 'Read Aloud' / 'Stop' button pair using the browser's
        native Web Speech API (speechSynthesis) — no backend, no API key,
        works offline from Gemini entirely. Voice availability for non-English
        languages depends on what voices the user's OS/browser has installed;
        if a language voice isn't available, most browsers fall back to a
        default voice rather than failing silently.
        """
        speech_lang = LANG_SPEECH_CODES.get(language, "en-IN")
        safe_text = json.dumps(text)  # safely escapes quotes/newlines for embedding in JS
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
                diag_response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[sensor_query]
                )
                translations, product_query, raw_multilang_text = parse_multilang_diagnosis(diag_response.text)
                if product_query and product_query.strip().lower() == "none":
                    product_query = None

                st.session_state["last_diagnosis_signature"] = signature
                st.session_state["last_diagnosis_translations"] = translations
                st.session_state["last_diagnosis_product_query"] = product_query
                st.session_state["last_diagnosis_time"] = time.time()

                # Log this diagnosis to the backend's history table. Best-effort:
                # if this fails (e.g. transient network hiccup), the diagnosis
                # still displays fine above — it just won't appear in history.
                # We store the raw multi-language text (with markers intact)
                # so the history panel can also show it in any language.
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
                        },
                        timeout=5
                    )
                except Exception:
                    pass
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

        if "last_diagnosis_translations" in st.session_state:
            selected_lang = st.session_state.selected_language
            translations = st.session_state["last_diagnosis_translations"]
            display_text = translations.get(selected_lang) or translations.get("English", "Diagnosis text unavailable.")

            st.markdown("### 🩺 Diagnosis & Treatment")
            if selected_lang not in translations:
                st.caption(f"⚠️ {selected_lang} translation wasn't returned this time — showing English instead.")
            st.markdown(display_text)
            render_voice_readout(display_text, selected_lang)

            # -------------------------------------------------------------
            # Shopping links — nearby stores (via live location) + online
            # stores, based on the product Gemini recommended in the
            # diagnosis above. No Places API needed: nearby uses a Google
            # Maps search link centered on the user's saved field location.
            # -------------------------------------------------------------
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

        # -----------------------------------------------------------------
        # Diagnosis history — browse past AI diagnoses over time, pulled
        # from the backend's diagnoses table. Most recent first.
        # -----------------------------------------------------------------
        st.markdown("### 📜 Diagnosis History")
        try:
            history_resp = requests.get(f"{API_URL}/diagnosis-history", params={"limit": 20}, timeout=5)
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