from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import os
import hashlib
import secrets
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diagnoses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            crop TEXT,
            moisture INTEGER,
            ph REAL,
            n INTEGER,
            p INTEGER,
            k INTEGER,
            diagnosis_text TEXT,
            product_query TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            username TEXT PRIMARY KEY,
            crop TEXT DEFAULT 'Tomato',
            field_lat REAL,
            field_lon REAL,
            alert_email TEXT,
            selected_language TEXT DEFAULT 'English',
            FOREIGN KEY (username) REFERENCES users (username)
        )
    """)

    try:
        conn.execute("ALTER TABLE diagnoses ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass  # already exists

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
MAX_DIAGNOSIS_HISTORY = 100


class DiagnosisRecord(BaseModel):
    crop: str
    moisture: int
    ph: float
    n: int
    p: int
    k: int
    diagnosis_text: str
    product_query: str | None = None
    username: str | None = None


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class UserSettings(BaseModel):
    username: str
    crop: str | None = None
    field_lat: float | None = None
    field_lon: float | None = None
    alert_email: str | None = None
    selected_language: str | None = None


def hash_password(password: str, salt: bytes | None = None):
    if salt is None:
        salt = secrets.token_bytes(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return pwd_hash.hex(), salt.hex()


def verify_password(password: str, stored_hash_hex: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    new_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(new_hash, stored_hash_hex)


@app.get("/")
def home():
    return {
        "status": "success",
        "message": "Agri-Pulse FastAPI server is running",
        "update_endpoint": "/update",
        "sensor_endpoint": "/sensor-data",
        "history_endpoint": "/history",
        "weather_endpoint": "/weather-forecast?lat=..&lon=..",
        "save_diagnosis_endpoint": "/save-diagnosis (POST)",
        "diagnosis_history_endpoint": "/diagnosis-history?username=..&crop=..&start_date=..&end_date=..",
        "register_endpoint": "/register (POST)",
        "login_endpoint": "/login (POST)",
        "settings_endpoint": "/settings (GET/POST)"
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


@app.post("/save-diagnosis")
def save_diagnosis(record: DiagnosisRecord):
    timestamp = datetime.utcnow().isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO diagnoses (timestamp, crop, moisture, ph, n, p, k, diagnosis_text, product_query, username) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            timestamp, record.crop, record.moisture, record.ph,
            record.n, record.p, record.k, record.diagnosis_text,
            record.product_query, record.username
        )
    )
    conn.execute("""
        DELETE FROM diagnoses
        WHERE id NOT IN (
            SELECT id FROM diagnoses ORDER BY id DESC LIMIT ?
        )
    """, (MAX_DIAGNOSIS_HISTORY,))
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "message": "Diagnosis saved",
        "timestamp": timestamp
    }


@app.get("/diagnosis-history")
def get_diagnosis_history(
    limit: int = 50,
    username: str | None = None,
    crop: str | None = None,
    start_date: str | None = None,  # "YYYY-MM-DD"
    end_date: str | None = None,    # "YYYY-MM-DD"
):
    """Returns past diagnoses, most recent first. Optionally filtered by
    username, crop, and/or a date range (inclusive)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = (
        "SELECT timestamp, crop, moisture, ph, n, p, k, diagnosis_text, product_query, username "
        "FROM diagnoses WHERE 1=1"
    )
    params = []

    if username:
        query += " AND username = ?"
        params.append(username)
    if crop:
        query += " AND crop = ?"
        params.append(crop)
    if start_date:
        query += " AND timestamp >= ?"
        params.append(start_date)
    if end_date:
        query += " AND timestamp <= ?"
        params.append(end_date + "T23:59:59")

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    history = [dict(row) for row in rows]

    return {
        "count": len(history),
        "history": history
    }


@app.get("/weather-forecast")
def get_weather_forecast(lat: float, lon: float):
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
            "forecast": forecast
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================
# AUTH & PER-USER SETTINGS
# ===========================================================

@app.post("/register")
def register(req: RegisterRequest):
    username = req.username.strip()
    if not username or not req.password:
        return {"status": "error", "message": "Username and password are required."}
    if len(req.password) < 6:
        return {"status": "error", "message": "Password must be at least 6 characters."}

    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        return {"status": "error", "message": "That username is already taken."}

    pwd_hash, salt = hash_password(req.password)
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (username, pwd_hash, salt, datetime.utcnow().isoformat())
    )
    conn.execute(
        "INSERT INTO user_settings (username, crop, selected_language) VALUES (?, ?, ?)",
        (username, "Tomato", "English")
    )
    conn.commit()
    conn.close()

    return {"status": "success", "message": "Account created.", "username": username}


@app.post("/login")
def login(req: LoginRequest):
    username = req.username.strip()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not row or not verify_password(req.password, row["password_hash"], row["salt"]):
        return {"status": "error", "message": "Invalid username or password."}

    return {"status": "success", "message": "Login successful.", "username": row["username"]}


@app.get("/settings")
def get_settings(username: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM user_settings WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not row:
        return {"status": "error", "message": "No settings found for this user."}

    return {"status": "success", "settings": dict(row)}


@app.post("/settings")
def update_settings(settings: UserSettings):
    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute(
        "SELECT username FROM user_settings WHERE username = ?", (settings.username,)
    ).fetchone()
    if not existing:
        conn.execute("INSERT INTO user_settings (username) VALUES (?)", (settings.username,))

    fields, values = [], []
    for field in ["crop", "field_lat", "field_lon", "alert_email", "selected_language"]:
        val = getattr(settings, field)
        if val is not None:
            fields.append(f"{field} = ?")
            values.append(val)

    if fields:
        values.append(settings.username)
        conn.execute(f"UPDATE user_settings SET {', '.join(fields)} WHERE username = ?", values)

    conn.commit()
    conn.close()

    return {"status": "success", "message": "Settings updated."}