import streamlit as st
from PIL import Image
from io import BytesIO
import base64
from google import genai
from google.genai import types
from st_img_pastebutton import paste

client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

st.title("🌱 Agri-Pulse: Smart Crop & Soil Doctor")

tab1, tab2 = st.tabs(["💬 AI Visual Chatbot", "📊 Sensor Diagnosis & Store Locator"])

with tab1:
    st.subheader("AI Crop Leaf & Plant Doctor Chat")
    st.write("Upload or paste crop images directly, chat with Gemini, and get instant visual diagnostics.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message.get("image") is not None:
                st.image(message["image"], width=300)
            st.markdown(message["content"])

    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded_file = st.file_uploader("Attach crop image (optional)", type=["jpg", "jpeg", "png"], key="chat_file_upload")
    with col2:
        st.write("Or paste from clipboard:")
        paste_data = paste(label="📋 Paste Image", key="chat_image_paste")

    current_image = None
    if uploaded_file is not None:
        current_image = Image.open(uploaded_file)
    elif paste_data is not None:
        header, encoded = paste_data.split(",", 1)
        binary_data = base64.b64decode(encoded)
        current_image = Image.open(BytesIO(binary_data))

    if current_image is not None:
        st.image(current_image, caption="Attached Image Ready", width=150)

    prompt = st.chat_input("Ask about your crops or send an attached image for diagnosis...")

    if prompt:
        user_msg = {"role": "user", "content": prompt, "image": current_image}
        st.session_state.messages.append(user_msg)
        
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
                    contents_payload.append(prompt)
                    
                    response = client.models.generate_content(
                        model='gemini-3.6-flash',
                        contents=contents_payload
                    )
                    
                    bot_reply = response.text
                    st.markdown(bot_reply)
                    st.session_state.messages.append({"role": "assistant", "content": bot_reply})
                    st.rerun()
                except Exception as e:
                    st.error(f"An error occurred: {e}")

with tab2:
    st.subheader("IoT Sensor Reading & Automated Treatment Finder")
    st.write("Values below update automatically in real-time as you move the slide potentiometers in your Wokwi simulation:")

    @st.fragment(run_every=3)
    def render_live_sensors():
        query_params = st.query_params
        
        live_moisture = int(query_params.get("moisture", 50))
        live_ph = float(query_params.get("ph", 6.5))
        live_n = int(query_params.get("n", 50))
        live_p = int(query_params.get("p", 50))
        live_k = int(query_params.get("k", 50))
        live_crop = query_params.get("crop", "Tomato")

        st.markdown("### 🎛️ Live Sensor Gauges")
        s_moisture = st.slider("Soil Moisture Level (%) [Wokwi Live]", 0, 100, live_moisture, key="live_m_slider")
        s_ph = st.slider("Soil pH Level [Wokwi Live]", 0.0, 14.0, live_ph, key="live_ph_slider")
        
        col_n, col_p, col_k = st.columns(3)
        with col_n:
            s_n = st.slider("Nitrogen (N) %", 0, 100, live_n, key="live_n_slider")
        with col_p:
            s_p = st.slider("Phosphorus (P) %", 0, 100, live_p, key="live_p_slider")
        with col_k:
            s_k = st.slider("Potassium (K) %", 0, 100, live_k, key="live_k_slider")

        crop_options = ["Tomato", "Paddy/Rice", "Potato", "Chili", "Wheat"]
        crop_index = crop_options.index(live_crop) if live_crop in crop_options else 0
        s_crop = st.selectbox("Select Target Crop Type", crop_options, index=crop_index, key="live_crop_select")

        if "moisture" in query_params or st.button("Run Live Diagnosis"):
            with st.spinner("Analyzing live Wokwi hardware data with Gemini..."):
                try:
                    sensor_query = (
                        f"A live IoT sensor reading from Wokwi reports the following exact metrics for a {s_crop} crop: "
                        f"- Soil Moisture: {s_moisture}% "
                        f"- Soil pH: {s_ph} "
                        f"- Nitrogen (N): {s_n}% "
                        f"- Phosphorus (P): {s_p}% "
                        f"- Potassium (K): {s_k}% "
                        "Analyze whether these exact levels indicate severe nutritional deficiencies, water stress, or pH imbalances. "
                        "Provide: 1) Detailed Diagnosis based on these exact values, 2) Exact treatment or fertilizer solutions required, "
                        "and 3) Recommended online platforms (like BigHaat or AgriBegri) and local store types in Bengaluru."
                    )

                    response = client.models.generate_content(
                        model='gemini-3.6-flash',
                        contents=[sensor_query]
                    )

                    st.success(f"Live Wokwi Data Synchronized! (Moisture: {s_moisture}%, N: {s_n}%, P: {s_p}%, K: {s_k}%)")
                    st.markdown(response.text)

                except Exception as e:
                    st.error(f"An error occurred: {e}")

    render_live_sensors()
