from fastapi import FastAPI
from datetime import datetime
import sqlite3
import os
import requests

app = FastAPI(title="Agri-Pulse IoT API")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_history.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            moisture INTEGER,
            ph REAL,
            n INTEGER,
            p INTEGER,
            k INTEGER,
            crop TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

sensor_data = {
    "moisture": 0,
    "ph": 6.0,
    "n": 0,
    "p": 0,
    "k": 0,
    "crop": "Tomato"
}

MAX_HISTORY = 200


@app.get("/")
def home():
    return {
        "status": "success",
        "message": "Agri-Pulse FastAPI server is running",
        "update_endpoint": "/update",
        "sensor_endpoint": "/sensor-data",
        "history_endpoint": "/history",
        "weather_endpoint": "/weather-forecast?lat=..&lon=.."
    }


@app.get("/update")
def update_sensor_data(
    moisture: int = 0,
    ph: float = 6.0,
    n: int = 0,
    p: int = 0,
    k: int = 0,
    crop: str = "Tomato"
):
    sensor_data["moisture"] = moisture
    sensor_data["ph"] = ph
    sensor_data["n"] = n
    sensor_data["p"] = p
    sensor_data["k"] = k
    sensor_data["crop"] = crop

    timestamp = datetime.utcnow().isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO readings (timestamp, moisture, ph, n, p, k, crop) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (timestamp, moisture, ph, n, p, k, crop)
    )
    conn.execute("""
        DELETE FROM readings
        WHERE id NOT IN (
            SELECT id FROM readings ORDER BY id DESC LIMIT ?
        )
    """, (MAX_HISTORY,))
    conn.commit()
    conn.close()

    print("Received sensor data:")
    print(sensor_data)

    return {
        "status": "success",
        "message": "Sensor data updated",
        "data": sensor_data
    }


@app.get("/sensor-data")
def get_sensor_data():
    return sensor_data


@app.get("/history")
def get_sensor_history():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, moisture, ph, n, p, k, crop FROM readings ORDER BY id ASC"
    ).fetchall()
    conn.close()

    readings = [dict(row) for row in rows]

    return {
        "count": len(readings),
        "readings": readings
    }


@app.get("/weather-forecast")
def get_weather_forecast(lat: float, lon: float):
    """
    Fetches today + next 3 days forecast for the given field coordinates.
    lat/lon are passed in by the caller (Streamlit app), not hardcoded,
    so this works for any user's field location.
    """
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&timezone=auto&forecast_days=4"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        daily = data["daily"]
        forecast = []
        for i in range(len(daily["time"])):
            forecast.append({
                "date": daily["time"][i],
                "temp_max": daily["temperature_2m_max"][i],
                "temp_min": daily["temperature_2m_min"][i],
                "rain_chance": daily["precipitation_probability_max"][i],
            })

        return {
            "status": "success",
            "lat": lat,
            "lon": lon,
            "forecast": forecast  # index 0 = today, 1-3 = next 3 days
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}