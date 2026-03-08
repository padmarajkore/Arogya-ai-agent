import sqlite3
import os
import json
from datetime import datetime
from typing import Optional
import uuid
import chromadb

# Paths
os.makedirs('./data', exist_ok=True)
DB_FILE = './data/health_data.db'
CHROMA_DB_DIR = './data/chroma_db'
DOCTORS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'doctors.json')
REPORTS_DIR = os.path.abspath('health_reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

# Tracks generated PDF reports awaiting download {user_id: pdf_path}
PENDING_REPORTS: dict = {}
# Persistent record of the latest report per user (survives across turns)
LAST_REPORTS: dict = {}

# Initialize local Vector DB
try:
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
    memory_collection = chroma_client.get_or_create_collection(name="user_health_memories")
except Exception as e:
    print(f"Error initializing ChromaDB: {e}")
    memory_collection = None

def _init_db():
    """
    Initialize the SQLite database for parametric data."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS health_parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            parameter_name TEXT NOT NULL,
            parameter_value REAL,
            unit TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS health_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            reminder_message TEXT NOT NULL,
            trigger_time TEXT NOT NULL,
            is_completed INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS doctor_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            rating REAL NOT NULL,
            feedback_text TEXT,
            visit_date TEXT,
            submitted_at TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

_init_db()


# ── Auth Functions ─────────────────────────────────────────────────────────────

import hashlib

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username: str, password: str, full_name: str = "") -> dict:
    """
    Register a new user. Returns success or error."""
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.register_user(username, password, full_name)
    if len(username.strip()) < 3:
        return {"status": "error", "message": "Username must be at least 3 characters."}
    if len(password) < 6:
        return {"status": "error", "message": "Password must be at least 6 characters."}
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO users (username, password_hash, full_name, created_at) VALUES (?, ?, ?, ?)',
            (username.strip().lower(), _hash_password(password), full_name.strip(),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        return {"status": "success", "username": username.strip().lower()}
    except sqlite3.IntegrityError:
        return {"status": "error", "message": "Username already exists. Please choose another."}
    finally:
        conn.close()

def login_user(username: str, password: str) -> dict:
    """
    Verify login credentials. Returns success with username or error."""
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.login_user(username, password)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT username, full_name FROM users WHERE username = ? AND password_hash = ?',
        (username.strip().lower(), _hash_password(password))
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"status": "success", "username": row[0], "full_name": row[1] or row[0]}
    return {"status": "error", "message": "Invalid username or password."}

import json

def store_health_parameter(user_id: str, parameter_name: str = "", parameter_value: float = 0.0, unit: str = "", parameters_json: str = "") -> dict:
    """
    Store measured health data (e.g., blood sugar, blood pressure, cholesterol) for a specific user.
    To store a single reading, provide parameter_name, parameter_value, and unit.
    To store MULTIPLE readings AT ONCE, provide 'parameters_json' as a JSON string of a list of dictionaries:
    '[{"name": "blood_glucose", "value": 110.5, "unit": "mg/dL"}, {"name": "hba1c", "value": 5.8, "unit": "%"}]'
    ALWAYS use the 'parameters_json' argument to store multiple items in a single call to save time!
    """
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': 
        import health_agent.dynamo_client as dc; 
        return dc.store_health_parameter(user_id, parameter_name, parameter_value, unit, parameters_json)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    
    items = []
    if parameters_json:
        try:
            items = json.loads(parameters_json)
        except Exception:
            pass
            
    if not items:
        items = [{"name": parameter_name, "value": parameter_value, "unit": unit}]
    
    stored_count = 0
    for item in items:
        p_name = item.get("name", "")
        if not p_name: continue
        try:
            safe_val = float(item.get("value", 0.0))
        except (ValueError, TypeError):
            safe_val = 0.0
            
        cursor.execute('''
            INSERT INTO health_parameters (user_id, timestamp, parameter_name, parameter_value, unit)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, timestamp, p_name.lower(), safe_val, str(item.get("unit", ""))))
        stored_count += 1
        
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "action": "store_health_parameter",
        "message": f"Successfully stored {stored_count} parameter(s) for user {user_id}"
    }

def get_health_parameters(user_id: str, parameter_name: str) -> dict:
    """
    Query historical health parameters from the database for a specific user.
    Pass the exact parameter_name to get history for that specific parameter (e.g. 'fasting_blood_sugar').
    Pass 'all' or an empty string to get the most recent measurements for ALL parameters for that user.
    """
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.get_health_parameters(user_id, parameter_name)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if parameter_name:
        cursor.execute('''
            SELECT timestamp, parameter_name, parameter_value, unit 
            FROM health_parameters 
            WHERE user_id = ? AND parameter_name = ? 
            ORDER BY timestamp DESC
        ''', (user_id, parameter_name.lower()))
    else:
        cursor.execute('''
            SELECT timestamp, parameter_name, parameter_value, unit 
            FROM health_parameters 
            WHERE user_id = ?
            ORDER BY timestamp DESC
        ''', (user_id,))
        
    rows = cursor.fetchall()
    conn.close()
    
    results = [
        {"timestamp": row[0], "parameter": row[1], "value": row[2], "unit": row[3]}
        for row in rows
    ]
    
    return {
        "status": "success",
        "action": "get_health_parameters",
        "data": results,
    }

def store_health_memory(user_id: str, information: str) -> dict:
    """
    Store important context and non-parametric memory about the user into long-term vector storage (RAG).
    Use this tool to save conditions, allergies, upcoming travel, doctor advice, symptoms, etc. 
    so the assistant remembers for future conversations.
    """
    if memory_collection is None:
        return {"status": "error", "message": "Vector DB not initialized."}
        
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc_id = str(uuid.uuid4())
    memory_text = f"[{timestamp}] {information}"
    
    memory_collection.add(
        documents=[memory_text],
        metadatas=[{"user_id": user_id, "timestamp": timestamp}],
        ids=[doc_id]
    )
    
    return {
        "status": "success",
        "action": "store_health_memory",
        "message": f"Memory securely stored in Vector DB for {user_id}."
    }

def read_health_memory(user_id: str, query: str) -> dict:
    """
    Retrieve all non-parametric health memory, notes, and context gathered about the user using semantic search.
    Provide a 'query' to search for specific context (e.g. 'allergies', 'headaches', 'doctor recommendations').
    """
    if memory_collection is None:
        return {"status": "error", "message": "Vector DB not initialized."}
        
    # Strict metadata filtering by user_id ensuring data privacy
    try:
        results = memory_collection.query(
            query_texts=[query],
            n_results=5,
            where={"user_id": user_id}
        )
        
        documents = results.get("documents", [])
        if not documents or not documents[0]:
            return {
                "status": "success",
                "action": "read_health_memory",
                "memory": f"No previous health memory found for {user_id} related to '{query}'."
            }
            
        combined_memory = "\n".join(documents[0])
        return {
            "status": "success",
            "action": "read_health_memory",
            "memory": combined_memory
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_current_time() -> str:
    """
    Get the current local date and time down to the second.
    Use this tool to reason about time-based records (e.g. knowing if an allergy occurred exactly 2 days ago or 4 weeks ago based on stored DB timestamps).
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def set_reminder(user_id: str, reminder_message: str, trigger_time: str) -> dict:
    """
    Sets a reminder for the user (e.g. "Take Paracetamol 500mg").
    The 'trigger_time' can be any natural language date or time (e.g., 'in 5 minutes', 'tomorrow at 9am', 'Next Tuesday', '2026-03-05 15:00').
    """
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.set_reminder(user_id, reminder_message, trigger_time)
    import dateparser
    from datetime import datetime
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    dt = dateparser.parse(trigger_time, settings={'RELATIVE_BASE': datetime.now()})
    if not dt:
        return {"status": "error", "message": f"Could not parse the time '{trigger_time}'. Try again with a clear format!"}
        
    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('''
        INSERT INTO health_reminders (user_id, reminder_message, trigger_time, is_completed)
        VALUES (?, ?, ?, 0)
    ''', (user_id, reminder_message, formatted_time))
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "action": "set_reminder",
        "message": f"Successfully set reminder '{reminder_message}' for user {user_id} at {trigger_time}."
    }

def fetch_due_reminders(user_id: str) -> dict | None:
    """
    Internal function for backend UI to fetch any reminders that are past their trigger_time."""
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.fetch_due_reminders(user_id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        SELECT id, reminder_message, trigger_time 
        FROM health_reminders 
        WHERE user_id = ? AND is_completed = 0 AND trigger_time <= ?
        ORDER BY trigger_time ASC
        LIMIT 1
    ''', (user_id, now_str))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {"id": row[0], "message": row[1], "time": row[2]}
    return None

def mark_reminder_completed(reminder_id: int) -> bool:
    """
    Internal function to mark a reminder as completed when the user acknowledges it."""
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.mark_reminder_completed(reminder_id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE health_reminders SET is_completed = 1 WHERE id = ?', (reminder_id,))
    conn.commit()
    conn.close()
    return True


# ── Doctor Recommendation Tools ────────────────────────────────────────────────

def _load_doctors() -> list:
    """
    Load doctor profiles from doctors.json."""
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc._load_doctors()
    try:
        with open(DOCTORS_FILE, "r") as f:
            return json.load(f).get("doctors", [])
    except Exception as e:
        print(f"Error loading doctors.json: {e}")
        return []

def _get_doctor_ratings() -> dict:
    """
    Fetch average user-submitted ratings per doctor from SQLite."""
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc._get_doctor_ratings()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT doctor_id, AVG(rating), COUNT(*) FROM doctor_feedback GROUP BY doctor_id')
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: {"avg_rating": round(row[1], 2), "count": row[2]} for row in rows}

def find_doctors(city: str, specialty: str = "") -> dict:
    """
    Find doctor recommendations for the user based on city and specialty/condition.
    Call this when the user asks for doctor suggestions or their health condition warrants a specialist.
    'city' should be a major Indian city (e.g., 'Mumbai', 'Delhi', 'Bangalore').
    'specialty' can be a condition like 'diabetes', 'heart', 'ortho', or a specialty name. If unknown, pass empty string.
    """
    doctors = _load_doctors()
    ratings_override = _get_doctor_ratings()
    city_lower = city.strip().lower()
    spec_lower = specialty.strip().lower()

    SPECIALTY_MAP = {
        "diabetes": "diabetologist", "sugar": "diabetologist", "blood sugar": "diabetologist",
        "heart": "cardiologist", "cardiac": "cardiologist",
        "bone": "orthopedist", "joint": "orthopedist", "ortho": "orthopedist",
        "brain": "neurologist", "neuro": "neurologist",
        "skin": "dermatologist", "rash": "dermatologist",
        "thyroid": "endocrinologist", "hormone": "endocrinologist",
        "lung": "pulmonologist", "breathing": "pulmonologist", "asthma": "pulmonologist",
        "stomach": "gastroenterologist", "gastro": "gastroenterologist",
        "general": "general physician",
    }
    mapped_specialty = SPECIALTY_MAP.get(spec_lower, spec_lower)

    results = []
    for doc in doctors:
        if doc["city"].lower() != city_lower:
            continue
        if mapped_specialty and mapped_specialty not in doc["specialty"].lower():
            continue
        if doc["id"] in ratings_override:
            r = ratings_override[doc["id"]]
            dynamic_rating = round(
                (doc["rating"] * doc["feedback_count"] + r["avg_rating"] * r["count"]) /
                (doc["feedback_count"] + r["count"]), 2
            )
            total_ratings = doc["feedback_count"] + r["count"]
        else:
            dynamic_rating = doc["rating"]
            total_ratings = doc["feedback_count"]
        results.append({
            "doctor_id": doc["id"], "name": doc["name"], "specialty": doc["specialty"],
            "hospital": doc["hospital"], "city": doc["city"],
            "experience_years": doc["experience_years"], "equipment": doc["equipment"],
            "phone": doc["phone"], "rating": dynamic_rating, "total_ratings": total_ratings,
        })

    results.sort(key=lambda x: x["rating"], reverse=True)
    if not results:
        return {"status": "no_results", "message": f"No doctors found in {city}" + (f" for '{specialty}'" if specialty else "") + "."}
    return {"status": "success", "city": city, "specialty": specialty or "All", "doctors": results}


def submit_doctor_feedback(user_id: str, doctor_id: str, rating: float, feedback_text: str, visit_date: str) -> dict:
    """
    Submit a rating and feedback for a doctor the user has visited.
    Call this when the user provides feedback about their doctor visit experience.
    'rating' must be between 1.0 and 5.0.
    'doctor_id' is the ID like 'doc001' from the find_doctors results.
    'visit_date' can be a date string or natural language like 'today'.
    """
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true': import health_agent.dynamo_client as dc; return dc.submit_doctor_feedback(user_id, doctor_id, rating, feedback_text, visit_date)
    if not (1.0 <= rating <= 5.0):
        return {"status": "error", "message": "Rating must be between 1.0 and 5.0"}
    doctors = _load_doctors()
    doc_names = {d["id"]: d["name"] for d in doctors}
    if doctor_id not in doc_names:
        return {"status": "error", "message": f"Doctor ID '{doctor_id}' not found."}
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        'INSERT INTO doctor_feedback (doctor_id, user_id, rating, feedback_text, visit_date, submitted_at) VALUES (?, ?, ?, ?, ?, ?)',
        (doctor_id, user_id, rating, feedback_text, visit_date, submitted_at)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Recorded {rating}/5 for {doc_names[doctor_id]}. Thank you!", "doctor": doc_names[doctor_id]}


def get_user_city_from_ip(ip_address: str = "") -> dict:
    """
    Detect the user's approximate city from their IP address for location-based doctor recommendations.
    Call this automatically when the user asks for nearby doctors without specifying a city.
    """
    try:
        import requests as req
        
        url = f"http://ip-api.com/json/{ip_address}" if ip_address else "http://ip-api.com/json/"
        resp = req.get(url, timeout=5)
        data = resp.json()
        
        # If the IP is a private Docker network IP (e.g. 172.17.x.x), ip-api will fail. 
        # Fall back to asking the container's own public IP.
        if data.get("status") != "success" and ip_address:
            resp = req.get("http://ip-api.com/json/", timeout=5)
            data = resp.json()
            
        if data.get("status") == "success":
            return {"status": "success", "city": data.get("city", "Unknown"), "region": data.get("regionName", ""), "country": data.get("country", "")}
        return {"status": "error", "message": f"Could not detect location. Reason: {data.get('message', 'Unknown')}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Health Summary PDF Tools ───────────────────────────────────────────────────

def get_health_summary_data(user_id: str) -> dict:
    """
    Retrieve ALL stored health data for a user with FULL TIMELINE CONTEXT.
    Each reading includes: days_ago (how old it is), recency label, and per-parameter trends
    so you can reason about how values changed over time.
    Call this first when the user asks for a health summary or to prepare a doctor visit report.
    After calling this, write your time-aware analysis and call create_health_report_pdf.
    """
    now = datetime.now()
    rows = []
    if os.getenv('USE_DYNAMODB', 'false').lower() == 'true':
        import health_agent.dynamo_client as dc
        from boto3.dynamodb.conditions import Key
        table = dc._get_table('HA_Params')
        resp = table.query(KeyConditionExpression=Key('user_id').eq(user_id))
        items = resp.get('Items', [])
        items.sort(key=lambda x: x['timestamp'], reverse=True)
        for i in items:
            rows.append((i.get('parameter_name', ''), float(i.get('parameter_value', 0)), i.get('unit', ''), i.get('timestamp', '')))
    else:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT parameter_name, parameter_value, unit, timestamp
            FROM health_parameters
            WHERE user_id = ?
            ORDER BY timestamp DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        conn.close()

    def _recency(days: float) -> str:
        if days < 1:    return "today"
        if days < 2:    return "yesterday"
        if days < 7:    return f"{int(days)} days ago"
        if days < 30:   return f"{int(days // 7)} week(s) ago"
        if days < 365:  return f"{int(days // 30)} month(s) ago"
        return f"{int(days // 365)} year(s) ago"

    params = []
    for parameter_name, parameter_value, unit, timestamp in rows:
        days_ago = None
        recency = "unknown"
        if timestamp:
            try:
                ts = datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
                diff = now - ts
                days_ago = round(diff.total_seconds() / 86400, 2)
                recency = _recency(days_ago)
            except Exception:
                pass
        params.append({
            "parameter": parameter_name,
            "value": parameter_value,
            "unit": unit,
            "timestamp": timestamp,
            "days_ago": days_ago,
            "recency": recency,
        })

    # Per-parameter trend analysis (oldest → newest delta)
    from collections import defaultdict
    grouped = defaultdict(list)
    for p in params:
        grouped[p["parameter"]].append(p)

    parameter_trends = {}
    for param_name, readings in grouped.items():
        chrono = list(reversed(readings))  # oldest first
        if len(chrono) >= 2:
            oldest_val = chrono[0]["value"]
            newest_val = chrono[-1]["value"]
            try:
                delta = newest_val - oldest_val
                pct = (delta / oldest_val * 100) if oldest_val else 0
                direction = "stable" if abs(delta) < 0.01 else ("increased" if delta > 0 else "decreased")
                parameter_trends[param_name] = {
                    "readings_count": len(chrono),
                    "oldest_value": oldest_val,
                    "oldest_recorded": chrono[0]["recency"],
                    "oldest_days_ago": chrono[0]["days_ago"],
                    "latest_value": newest_val,
                    "latest_unit": chrono[-1]["unit"],
                    "latest_recorded": chrono[-1]["recency"],
                    "latest_days_ago": chrono[-1]["days_ago"],
                    "delta": round(delta, 3),
                    "pct_change": round(pct, 1),
                    "direction": direction,
                    "trend_summary": (
                        f"{param_name} {direction} by {abs(round(delta, 2))} "
                        f"({abs(round(pct, 1))}%) from {chrono[0]['recency']} to {chrono[-1]['recency']}"
                    )
                }
            except (TypeError, ZeroDivisionError):
                parameter_trends[param_name] = {"readings_count": len(chrono), "direction": "unknown"}
        elif len(chrono) == 1:
            parameter_trends[param_name] = {
                "readings_count": 1,
                "latest_value": chrono[0]["value"],
                "latest_unit": chrono[0]["unit"],
                "latest_recorded": chrono[0]["recency"],
                "latest_days_ago": chrono[0]["days_ago"],
                "direction": "single_reading",
                "trend_summary": f"Only one reading, recorded {chrono[0]['recency']}."
            }

    memories = []
    if memory_collection:
        try:
            results = memory_collection.query(
                query_texts=["health symptoms medications conditions allergies lifestyle"],
                n_results=20,
                where={"user_id": user_id} if user_id else None
            )
            if results and results.get('documents') and results['documents'][0]:
                memories = results['documents'][0]
        except Exception as e:
            print(f"ChromaDB query error: {e}")

    return {
        "status": "success",
        "user_id": user_id,
        "report_generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "parameters": params,
        "parameter_trends": parameter_trends,
        "memories": memories,
        "parameter_count": len(params),
        "unique_parameters": len(grouped),
        "memory_count": len(memories),
        "analysis_guidance": (
            "Use 'parameter_trends' to understand how each metric changed over time. "
            "Recency matters: a reading from 6+ months ago may not reflect current state. "
            "Look for correlated trends (e.g. HbA1c rising alongside fasting glucose). "
            "Flag stale critical values (BP, sugar >30 days old). "
            "Always mention time elapsed since each key reading in your analysis."
        ),
    }



def create_health_report_pdf(user_id: str, ai_analysis: str) -> dict:
    """
    Generate a professional downloadable PDF health report for doctor visits.
    Call this AFTER get_health_summary_data and after writing your analysis.
    'ai_analysis' should be your comprehensive written analysis of the user's health.
    The PDF will include all health data, timestamps, your analysis, and medical insights.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    # Gather data
    data = get_health_summary_data(user_id)
    params = data["parameters"]
    memories = data["memories"]
    generated_at = data["report_generated_at"]
    
    if not ai_analysis or len(ai_analysis) < 10:
        try:
            from litellm import completion
            import json
            prompt = f"""
Analyze this health data (from {generated_at}) for a doctor. Keep it under 150 words.
Make it a bolded 3-point clinical narrative using EXACTLY this format and headers:

1. Recent Events & Memory:
<Correlate recent symptomatic memories and extract exact timestamps here>

2. Simulated Condition Summary:
<What these metrics indicate right now here>

3. Diagnostic Starting Point:
<Next steps for the doctor here>

DATA TO ANALYZE (Including exact memory timestamps and DB trends):
{json.dumps(data, default=str)}
"""
            resp = completion(
                model="nvidia_nim/meta/llama-3.1-70b-instruct",
                messages=[{"role": "user", "content": prompt}]
            )
            ai_analysis = resp.choices[0].message.content
        except Exception as e:
            ai_analysis = f"*(Auto-analysis failed: {e})*\n\nPlease see the raw data tables below."

    pdf_path = os.path.join(REPORTS_DIR, f"health_report_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm
    )

    styles = getSampleStyleSheet()
    TEAL = colors.HexColor('#007B8A')
    LIGHT_TEAL = colors.HexColor('#E8F7F9')
    DARK = colors.HexColor('#1A1A2E')
    GRAY = colors.HexColor('#666666')

    # Custom styles
    title_style = ParagraphStyle('Title', parent=styles['Title'],
        fontSize=22, textColor=TEAL, alignment=TA_CENTER, spaceAfter=4)
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'],
        fontSize=11, textColor=GRAY, alignment=TA_CENTER, spaceAfter=2)
    section_style = ParagraphStyle('Section', parent=styles['Heading2'],
        fontSize=13, textColor=TEAL, spaceBefore=10, spaceAfter=4,
        borderPad=4)
    body_style = ParagraphStyle('Body', parent=styles['Normal'],
        fontSize=10, textColor=DARK, leading=15)
    bullet_style = ParagraphStyle('Bullet', parent=styles['Normal'],
        fontSize=10, textColor=DARK, leading=14, leftIndent=12, bulletIndent=4)
    footer_style = ParagraphStyle('Footer', parent=styles['Normal'],
        fontSize=8, textColor=GRAY, alignment=TA_CENTER)

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Personal Health Report", title_style))
    story.append(Paragraph("AI-Powered Health Companion  |  Confidential Medical Summary", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=TEAL, spaceAfter=8))

    # ── Patient Info ──────────────────────────────────────────────────────────
    story.append(Paragraph("Patient Information", section_style))
    info_data = [
        ["Patient ID", user_id, "Report Date", generated_at],
        ["Total Readings", str(len(params)), "Health Notes", str(len(memories))],
    ]
    info_table = Table(info_data, colWidths=[40*mm, 60*mm, 40*mm, 50*mm])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_TEAL),
        ('TEXTCOLOR', (0, 0), (0, -1), TEAL),
        ('TEXTCOLOR', (2, 0), (2, -1), TEAL),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.white),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 8*mm))

    # ── Health Parameters ─────────────────────────────────────────────────────
    story.append(Paragraph("Health Parameters & Lab Results", section_style))
    if params:
        story.append(Paragraph(
            f"All {len(params)} recorded health measurements are listed below, ordered from most recent:",
            body_style
        ))
        story.append(Spacer(1, 3*mm))

        # Group by parameter name, get latest
        param_latest = {}
        for p in params:
            if p["parameter"] not in param_latest:
                param_latest[p["parameter"]] = p

        # Table: Parameter | Value | Unit | Recorded | Age
        table_data = [["Parameter", "Value", "Unit", "Recorded At", "Age"]]
        for p in params:
            recency = p.get("recency", "—")
            days_ago = p.get("days_ago")
            # Flag stale readings (>30 days) in red
            age_label = recency
            table_data.append([
                p["parameter"].replace("_", " ").title(),
                str(p["value"]),
                p["unit"] or "—",
                p["timestamp"][:10] if p["timestamp"] else "—",
                age_label,
            ])

        param_table = Table(table_data, colWidths=[45*mm, 28*mm, 22*mm, 35*mm, 30*mm])
        stale_rows = [
            i+1 for i, p in enumerate(params)
            if p.get("days_ago") and p["days_ago"] > 30
        ]
        table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), TEAL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_TEAL]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#DDDDDD')),
            ('PADDING', (0, 0), (-1, -1), 4),
            ('ALIGN', (1, 0), (3, -1), 'CENTER'),
        ]
        # Highlight stale rows in light orange
        for row_idx in stale_rows:
            table_style.append(('BACKGROUND', (4, row_idx), (4, row_idx), colors.HexColor('#FFF3CD')))
            table_style.append(('TEXTCOLOR', (4, row_idx), (4, row_idx), colors.HexColor('#856404')))
        param_table.setStyle(TableStyle(table_style))
        story.append(param_table)
        story.append(Spacer(1, 2*mm))
        story.append(Paragraph(
            "Note: Yellow-highlighted Age cells indicate readings older than 30 days. Consider re-testing.",
            ParagraphStyle('note', parent=body_style, fontSize=8, textColor=colors.HexColor('#856404'))
        ))
    else:
        story.append(Paragraph("No parametric health data recorded yet.", body_style))
    story.append(Spacer(1, 8*mm))

    # ── Health Notes ──────────────────────────────────────────────────────────
    story.append(Paragraph("Health Notes & Medical History", section_style))
    if memories:
        for i, memo in enumerate(memories, 1):
            story.append(Paragraph(f"• {memo}", bullet_style))
            story.append(Spacer(1, 2*mm))
    else:
        story.append(Paragraph("No health notes recorded yet.", body_style))
    story.append(Spacer(1, 8*mm))

    # ── AI Analysis ───────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=6))
    story.append(Paragraph("AI Health Analysis & Insights", section_style))
    story.append(Paragraph(
        "The following analysis was generated by the Personal AI Health Assistant based on all recorded health data:",
        ParagraphStyle('italic', parent=body_style, textColor=GRAY, fontSize=9)
    ))
    story.append(Spacer(1, 3*mm))

    import re
    # Clean up model-generated characters before rendering
    clean_analysis = (ai_analysis
        .replace('\u25a0', '\u2022')    # ■ → bullet •
        .replace('\ufffd', '-')          # replacement char → dash
        .replace('\u200b', '')           # zero-width space
        .strip())

    # Convert markdown bold to ReportLab XML tags
    clean_analysis = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_analysis)

    # Custom style for inline subheadings
    subhead_style = ParagraphStyle(
        'Subhead', parent=body_style, fontSize=11, textColor=TEAL, spaceBefore=4, spaceAfter=2
    )

    # Split analysis into paragraphs
    for para in clean_analysis.split('\n'):
        para = para.strip()
        if not para:
            continue
            
        if para.startswith(('• ', '- ', '* ')):
            # Render as bullet point
            clean_para = para[2:].strip()
            story.append(Paragraph(f"• {clean_para}", bullet_style))
        elif para.startswith('<b>') and para.endswith('</b>'):
            # Render standalone bold lines as subheadings
            story.append(Paragraph(para, subhead_style))
        else:
            # Render normal text
            story.append(Paragraph(para, body_style))
            
        story.append(Spacer(1, 2*mm))
    story.append(Spacer(1, 8*mm))

    # ── Disclaimer ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=GRAY, spaceAfter=4))
    story.append(Paragraph(
        "MEDICAL DISCLAIMER: This report is generated by an AI assistant and is intended to supplement, "
        "not replace, professional medical advice. Please consult a qualified healthcare provider for diagnosis "
        "and treatment decisions. Data accuracy depends on what was recorded in the system.",
        footer_style
    ))
    story.append(Paragraph(
        f"Generated by AI Health Assistant | {generated_at} | For: {user_id}",
        footer_style
    ))

    doc.build(story)
    PENDING_REPORTS[user_id] = pdf_path
    LAST_REPORTS[user_id] = pdf_path

    return {
        "status": "success",
        "message": f"Health report PDF has been generated successfully! The user can now download it using the 📥 Download Health Report button that appeared below the chat. The PDF contains all {len(params)} health readings, {len(memories)} health notes, and your AI analysis.",
        "pdf_path": pdf_path,
        "parameter_count": len(params),
        "memory_count": len(memories),
    }


def query_medical_guidelines(query: str) -> str:
    """
    Queries the AWS Bedrock Knowledge Base (containing guideline-170-en.txt and other medical literature).
    Use this to get authoritative primary insights and guidelines for diagnosing symptoms.
    """
    import boto3
    import os
    from dotenv import load_dotenv
    # Force override from .env in case the terminal session has old variables cached
    load_dotenv(override=True)
    
    kb_id = os.getenv("BEDROCK_KNOWLEDGE_BASE_ID")
    if not kb_id:
        return "Error: BEDROCK_KNOWLEDGE_BASE_ID is not configured in environment variables."
        
    try:
        client = boto3.client('bedrock-agent-runtime', region_name=os.getenv("AWS_REGION_NAME", "ap-south-1"))
        response = client.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={'text': query},
            retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 3}}
        )
        
        results = [snippet.get("content", {}).get("text", "") for snippet in response.get("retrievalResults", [])]
        
        if not results or not any(results):
            return "No relevant medical guidelines found in the knowledge base for this query."
            
        return "Medical Guideline Insights:\n\n" + "\n\n---\n\n".join(results)
    except Exception as e:
        return f"Error querying Bedrock Knowledge Base: {str(e)}"

# ── Patch: Strip docstrings for Bedrock (LiteLLM bug workaround) ─────────────
for func in [store_health_parameter, get_health_parameters, store_health_memory, read_health_memory, get_current_time, set_reminder, find_doctors, submit_doctor_feedback, get_user_city_from_ip, get_health_summary_data, create_health_report_pdf, query_medical_guidelines]:
    if func.__doc__:
        func.__doc__ = func.__doc__.strip()
