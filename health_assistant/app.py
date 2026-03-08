import gradio as gr
import asyncio
import os
import sqlite3
import json
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
from google.genai import types
import plotly.graph_objects as go

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from health_agent.agent import health_assistant_agent, analyze_health_report_image
from health_agent.tools import (
    fetch_due_reminders, mark_reminder_completed,
    PENDING_REPORTS, LAST_REPORTS,
    login_user, register_user, store_health_parameter,
    DB_FILE,
)

load_dotenv()

APP_NAME  = "Personal_Health_Assistant"
os.makedirs('./data', exist_ok=True)
db_url    = "sqlite:///./data/health_session_state.db"
session_service = DatabaseSessionService(db_url=db_url)
AUDIO_PATH = os.path.abspath("sounds/mixkit-classic-alarm-995.wav")


# ── Agent helpers ──────────────────────────────────────────────────────────────

async def process_agent_response(event):
    if event.is_final_response():
        if (event.content and event.content.parts
                and hasattr(event.content.parts[0], "text")
                and event.content.parts[0].text):
            import re
            raw = event.content.parts[0].text

            # Extract <think>...</think> blocks
            think_blocks = re.findall(r'<think>(.*?)</think>', raw, flags=re.DOTALL)

            # Remove think + tools tags from the visible response
            clean = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
            clean = re.sub(r'<tools>.*?</tools>',  '', clean, flags=re.DOTALL).strip()

            if not clean:
                clean = "Action completed successfully."

            # Prepend collapsible reasoning block if model produced thinking
            if think_blocks:
                thinking_text = "\n\n---\n\n".join(t.strip() for t in think_blocks)
                collapsible = (
                    '<details style="margin-bottom:10px;background:rgba(0,180,216,0.07);'
                    'border:1px solid rgba(0,180,216,0.25);border-radius:8px;padding:8px 14px;">'
                    '<summary style="cursor:pointer;color:#00b4d8;font-weight:600;font-size:0.9em;">'
                    '🧠 View AI Reasoning</summary>'
                    f'<pre style="white-space:pre-wrap;font-size:0.82em;color:#9ec5d4;'
                    f'margin-top:8px;line-height:1.5">{thinking_text}</pre>'
                    '</details>\n\n'
                )
                return collapsible + clean

            return clean
    return None


async def call_agent(user_id, message_dict, history, request: gr.Request = None):
    ip_address = request.client.host if request else ''
    text_input = message_dict.get("text", "")
    files      = message_dict.get("files", [])

    if not text_input and not files:
        yield {"text": "", "files": []}, history, gr.update(visible=False)
        return

    user_id = (user_id or "guest").strip()

    # ── Step 1: Show user message immediately + clear input ───────────────────
    interim_history = list(history)
    for fp in files:
        interim_history.append({"role": "user", "content": gr.FileData(path=fp)})
    if text_input:
        interim_history.append({"role": "user", "content": text_input})
    # Append a thinking placeholder for the assistant
    if files:
        interim_history.append({"role": "assistant", "content": "⏳ Processing and extracting data from report (this may take up to a minute)..."})
    else:
        interim_history.append({"role": "assistant", "content": "⏳ Thinking…"})

    yield {"text": "", "files": []}, interim_history, gr.update(visible=False)

    # ── Step 2: Run agent ──────────────────────────────────────────────────────
    existing = session_service.list_sessions(app_name=APP_NAME, user_id=user_id)
    if existing and len(existing.sessions) > 0:
        session_id = existing.sessions[0].id
    else:
        sess = session_service.create_session(
            app_name=APP_NAME, user_id=user_id,
            state={"user_name": user_id, "user_id": user_id},
        )
        session_id = sess.id

    runner = Runner(agent=health_assistant_agent, app_name=APP_NAME,
                    session_service=session_service)
    prompt = f"[CONTEXT: Focus strictly on user_id '{user_id}'. USER_NETWORK_IP: {ip_address}]\n{text_input}"

    if files:
        extractions = []
        for fp in files:
            print(f"Vision analysis: {fp}")
            # Run the heavy vision API call in a background thread so the event loop is not blocked 
            # and the UI can immediately display the "Processing..." state.
            ext = await asyncio.to_thread(analyze_health_report_image, fp)
            extractions.append(ext)
                
        prompt += "\n\n[HEALTH REPORT IMAGE ANALYSIS — You must call store_health_parameter using the 'parameters_json' stringified JSON array argument for ALL extracted numeric values tightly batched into a single API call to save latency]:\n" + "\n\n".join(extractions)
        
        # Update UI to show extraction is done, now agent is thinking
        interim_history[-1] = {"role": "assistant", "content": "⏳ Analyzing extracted data..."}
        yield {"text": "", "files": []}, interim_history, gr.update(visible=False)

    content   = types.Content(role="user", parts=[types.Part(text=prompt)])
    final_txt = "Sorry, I couldn't generate a response."
    try:
        # Cross-region inference mapping for Amazon Nova Lite (requires us-east-1 endpoint)
        is_amazon = "amazon" in getattr(health_assistant_agent.model, 'model', '')
        old_region = os.environ.get("AWS_REGION_NAME")
        if is_amazon:
            os.environ["AWS_REGION_NAME"] = "us-east-1"
            
        async for event in runner.run_async(user_id=user_id, session_id=session_id,
                                             new_message=content):
            r = await process_agent_response(event)
            if r:
                final_txt = r
                
    except Exception as e:
        final_txt = f"Error: {str(e)}"
    finally:
        if is_amazon:
            if old_region: os.environ["AWS_REGION_NAME"] = old_region
            else: os.environ.pop("AWS_REGION_NAME", None)

    # ── Step 3: Replace thinking placeholder with real response ───────────────
    final_history = interim_history[:-1]           # drop the "⏳ Thinking…" bubble
    final_history.append({"role": "assistant", "content": final_txt})

    new_pdf      = PENDING_REPORTS.pop(user_id, None)
    existing_pdf = LAST_REPORTS.get(user_id)
    pdf_to_show  = new_pdf or existing_pdf
    if pdf_to_show and os.path.exists(pdf_to_show):
        yield {"text": "", "files": []}, final_history, gr.update(value=pdf_to_show, visible=True)
    else:
        yield {"text": "", "files": []}, final_history, gr.update(visible=False)



# ── Dashboard chart builder ────────────────────────────────────────────────────

def build_health_charts(user_id: str):
    """Return Plotly figures for health parameter trends grouped by category."""
    if not user_id:
        return None, None, None

    from health_agent.tools import get_health_parameters
    res = get_health_parameters(user_id, 'all')
    rows = [(i['parameter'], i['value'], i['unit'], i['timestamp']) for i in reversed(res['data'])]

    if not rows:
        return None, None, None

    # Group by parameter
    grouped = defaultdict(list)
    for name, value, unit, ts in rows:
        try:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            dt = datetime.now()
        grouped[name].append((dt, value, unit))

    def _keys(*fragments):
        return [k for k in grouped if any(f in k.lower() for f in fragments)]

    GLUCOSE_KEYS     = _keys("sugar", "glucose", "hba1c", "insulin", "c_peptide", "homa")
    BP_CARDIAC_KEYS  = _keys("pressure", "systolic", "diastolic", "heart_rate",
                              "troponin", "ck_mb", "bnp", "nt_pro", "d_dimer",
                              "fibrinogen", "crp", "homocysteine", "temperature",
                              "spo2", "respiratory", "peak_flow", "fvc", "fev")
    LIPID_KEYS       = _keys("cholesterol", "triglyceride", "ldl", "hdl", "vldl")
    CBC_KEYS         = _keys("hemoglobin", "rbc", "wbc", "platelet", "hematocrit",
                              "mcv", "mch", "mchc", "rdw", "neutrophil", "lympho",
                              "monocyte", "eosinophil", "basophil", "ferritin",
                              "serum_iron", "tibc", "transferrin", "esr",
                              "pt_", "inr", "aptt", "bleeding", "clotting")
    ORGAN_KEYS       = _keys("sgot", "sgpt", "ast", "alt", "alkaline", "bilirubin",
                              "ggt", "ldh", "albumin", "globulin", "protein",
                              "creatinine", "urea", "bun", "uric_acid", "egfr",
                              "sodium", "potassium", "chloride", "bicarbonate",
                              "tsh", "free_t", "total_t", "thyro",
                              "testosterone", "fsh", "lh", "estradiol", "prolactin",
                              "cortisol", "dhea", "progesterone", "amh", "shbg",
                              "psa", "afp", "cea", "ca_1", "beta_hcg")
    VITALS_KEYS      = _keys("weight", "height", "bmi", "waist", "hip", "body_fat",
                              "lean", "muscle", "vitamin", "folic", "calcium",
                              "phosphorus", "magnesium", "zinc", "copper",
                              "urine", "microalbumin", "cystatin")

    placed = set()
    def unique(keys):
        result = []
        for k in keys:
            if k not in placed:
                placed.add(k)
                result.append(k)
        return result

    g1 = unique(GLUCOSE_KEYS)
    g2 = unique(BP_CARDIAC_KEYS)
    g3 = unique(LIPID_KEYS)
    g4 = unique(CBC_KEYS)
    g5 = unique(ORGAN_KEYS)
    g6 = unique(VITALS_KEYS) + [k for k in grouped if k not in placed]

    BLUE   = ["#00b4d8","#0077b6","#48cae4","#90e0ef","#023e8a","#0096c7"]
    RED    = ["#e63946","#ff4d6d","#c9184a","#ff758f","#a4133c","#ff85a1"]
    ORANGE = ["#f4a261","#e76f51","#e9c46a","#f3722c","#f8961e","#f9c74f"]
    GREEN  = ["#52b788","#40916c","#74c69d","#95d5b2","#2d6a4f","#b7e4c7"]
    PURPLE = ["#9b5de5","#7b2d8b","#c77dff","#e0aaff","#5a189a","#d0a9f5"]
    TEAL   = ["#2ec4b6","#cbf3f0","#ffbf69","#ffffff","#ff9f1c","#2ec4b6"]

    def make_fig(keys, title, yaxis_title, palette):
        if not keys:
            return None
        fig = go.Figure()
        for i, key in enumerate(keys):
            pts   = grouped[key]
            xs    = [p[0] for p in pts]
            ys    = [p[1] for p in pts]
            unit  = pts[0][2] or ""
            color = palette[i % len(palette)]
            label = key.replace("_", " ").title()
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines+markers",
                name=f"{label} ({unit})",
                line=dict(color=color, width=2.5),
                marker=dict(size=7, color=color),
                hovertemplate=(f"<b>{label}</b><br>Value: %{{y}} {unit}"
                               f"<br>Date: %{{x|%b %d, %Y}}<extra></extra>"),
            ))
        fig.update_layout(
            title=dict(text=title, font=dict(size=15, color="#00b4d8")),
            paper_bgcolor="#0f2027", plot_bgcolor="#1a2e3b",
            font=dict(color="#c9d6df"),
            xaxis=dict(gridcolor="#2a3f50", title="Date"),
            yaxis=dict(gridcolor="#2a3f50", title=yaxis_title),
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#c9d6df")),
            margin=dict(l=50, r=20, t=50, b=40),
            hovermode="x unified",
        )
        return fig

    f1 = make_fig(g1, "🩸 Blood Glucose & Diabetes",    "mg/dL / %",        BLUE)
    f2 = make_fig(g2, "❤️ Cardiovascular & Vitals",      "Value",            RED)
    f3 = make_fig(g3, "🫀 Lipid Panel",                  "mg/dL",            ORANGE)
    f4 = make_fig(g4, "🔬 CBC & Blood Counts",           "Value",            GREEN)
    f5 = make_fig(g5, "🏥 Organ Function & Hormones",    "Value",            PURPLE)
    f6 = make_fig(g6, "💊 Vitamins, Minerals & Others",  "Value",            TEAL)

    # Return non-None figures (up to first 3 for the 3 gr.Plot slots;
    # pack all into a list that the caller can distribute)
    figures = [f for f in [f1, f2, f3, f4, f5, f6] if f is not None]

    # Pad or trim to exactly 3 for the three chart slots
    while len(figures) < 3:
        figures.append(None)
    return figures[0], figures[1], figures[2]



# ── Login greeting ─────────────────────────────────────────────────────────────

def generate_login_greeting(user_id: str, display_name: str) -> list:
    """Returns initial chat history with a personalised health snapshot greeting."""
    from health_agent.tools import get_health_parameters
    res = get_health_parameters(user_id, 'all')
    rows = [(i['parameter'], i['value'], i['unit'], i['timestamp']) for i in res['data']]

    now  = datetime.now()
    seen = set()
    recent_params = []
    for name, val, unit, ts in rows:
        if name not in seen:
            seen.add(name)
            try:
                dt      = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                days    = (now - dt).total_seconds() / 86400
                age_lbl = ("today" if days < 1 else
                           "yesterday" if days < 2 else
                           f"{int(days)} days ago" if days < 30 else
                           f"{int(days//30)} month(s) ago")
            except Exception:
                age_lbl = "previously"
            recent_params.append((name, val, unit or "", age_lbl))

    greeting = f"👋 Welcome back, **{display_name}**! Great to see you.\n\n"

    if recent_params:
        greeting += "📊 **Your latest health snapshot:**\n"
        for name, val, unit, age in recent_params[:5]:
            label = name.replace("_", " ").title()
            greeting += f"• **{label}**: {val} {unit}  *(recorded {age})*\n"
        if len(recent_params) > 5:
            greeting += f"  *(+ {len(recent_params)-5} more readings stored)*\n"
        greeting += "\n💡 Ask me anything — check your readings, set reminders, find a doctor, or generate a health report for your visit!\n"
    else:
        greeting += (
            "📌 No health data recorded yet. Start by telling me your latest readings — "
            "blood pressure, blood sugar, cholesterol, or any symptoms you're experiencing.\n\n"
            "💡 You can also upload a lab report image and I'll extract the values automatically!"
        )

    return [{"role": "assistant", "content": greeting}]


# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
/* ── Reminder ─────────────────────────────── */
#reminder-box { transition: all 0.3s ease; }
@keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(255,68,68,0.7); }
    70%  { box-shadow: 0 0 0 10px rgba(255,68,68,0); }
    100% { box-shadow: 0 0 0 0 rgba(255,68,68,0); }
}
#alarm-audio { height: 2px !important; overflow: hidden !important; opacity: 0; }

/* ── Auth card ────────────────────────────── */
#auth-card {
    max-width: 560px;
    margin: 40px auto 0 auto;
    padding: 36px 40px;
    border-radius: 18px;
    background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
    box-shadow: 0 8px 40px rgba(0,0,0,0.45);
}
#auth-card label { color: #c9d6df !important; font-weight: 500; }
#auth-card input[type="text"], #auth-card input[type="password"] {
    background: rgba(255,255,255,0.07) !important;
    color: #fff !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    border-radius: 8px !important;
}
#auth-title {
    text-align: center; color: #00d4ff !important;
    font-size: 2em !important; font-weight: 700;
    margin-bottom: 4px !important;
}
#auth-subtitle {
    text-align: center; color: #9ec5d4 !important;
    font-size: 0.95em; margin-bottom: 24px !important;
}
#login-btn, #register-btn { border-radius: 8px !important; font-weight: 600 !important; }

/* ── Welcome bar ──────────────────────────── */
#welcome-bar {
    background: linear-gradient(90deg, #00b4d8, #0077b6);
    color: white !important; border-radius: 10px;
    padding: 8px 18px; font-weight: 600; font-size: 1em;
    margin-bottom: 8px; display: flex;
    align-items: center; justify-content: space-between;
}
#logout-btn {
    background: rgba(255,255,255,0.15) !important;
    border: 1px solid rgba(255,255,255,0.3) !important;
    color: white !important; border-radius: 6px !important;
    font-size: 0.85em !important;
    padding: 4px 14px !important;
    cursor: pointer; margin: 0 !important;
    width: auto !important; min-width: unset !important;
    max-width: 120px !important;
}
#logout-btn:hover { background: rgba(255,255,255,0.25) !important; }

/* ── Report download ──────────────────────── */
#report-download { border: 2px dashed #00b4d8 !important; border-radius: 10px !important; }

/* ── Dashboard ────────────────────────────── */
#dashboard-refresh { margin-bottom: 10px !important; }
"""


# ── UI ─────────────────────────────────────────────────────────────────────────

def create_gradio_interface():
    head_js = """
    <script>
        localStorage.setItem('theme', 'dark');
        document.documentElement.classList.add('dark');
    </script>
    """
    
    with gr.Blocks(title="🏥 Arogya AI Agent", head=head_js) as demo:

        logged_in_user = gr.State("")
        logged_in_name = gr.State("")

        # ══════════════════════════════════════════════════════════════════════
        # AUTH SCREEN  (shown by default)
        # ══════════════════════════════════════════════════════════════════════
        with gr.Column(elem_id="auth-card", visible=True) as auth_screen:
            gr.Markdown("🏥  Arogya AI Agent", elem_id="auth-title")
            gr.Markdown("Your personal AI-powered health companion", elem_id="auth-subtitle")

            with gr.Tabs():
                with gr.TabItem("🔐 Login"):
                    li_user      = gr.Textbox(label="Username", placeholder="Enter your username")
                    li_pass      = gr.Textbox(label="Password", placeholder="••••••••", type="password")
                    login_btn    = gr.Button("Login →", variant="primary", size="lg", elem_id="login-btn")
                    login_status = gr.HTML()

                with gr.TabItem("📝 Register"):
                    reg_name        = gr.Textbox(label="Full Name", placeholder="Raj Kumar")
                    reg_user        = gr.Textbox(label="Username (min 3 chars)", placeholder="raj55")
                    reg_pass        = gr.Textbox(label="Password (min 6 chars)", placeholder="••••••••", type="password")
                    reg_pass2       = gr.Textbox(label="Confirm Password",       placeholder="••••••••", type="password")
                    register_btn    = gr.Button("Create Account →", variant="primary", size="lg", elem_id="register-btn")
                    register_status = gr.HTML()

                with gr.TabItem("🩺 Doctor Onboarding"):
                    gr.Markdown("### Register as a Doctor\nFill in your details to appear in AI-powered patient recommendations.")
                    with gr.Row():
                        doc_name      = gr.Textbox(label="Full Name *", placeholder="Dr. Anjali Sharma")
                        doc_specialty = gr.Dropdown(label="Specialty *",
                            choices=["General Physician","Cardiologist","Diabetologist","Endocrinologist",
                                     "Orthopedist","Neurologist","Pulmonologist","Gastroenterologist",
                                     "Dermatologist","Psychiatrist","Ophthalmologist"])
                    with gr.Row():
                        doc_hospital = gr.Textbox(label="Hospital / Clinic *", placeholder="Apollo Hospital")
                        doc_city     = gr.Dropdown(label="City *",
                            choices=["Mumbai","Delhi","Bangalore","Chennai","Hyderabad",
                                     "Kolkata","Pune","Ahmedabad","Jaipur","Lucknow"])
                    with gr.Row():
                        doc_experience = gr.Slider(minimum=1, maximum=50, step=1, label="Years of Experience", value=5)
                        doc_phone      = gr.Textbox(label="Contact Phone *", placeholder="+91-XX-XXXX-XXXX")
                    doc_equipment = gr.CheckboxGroup(label="Available Equipment",
                        choices=["ECG","Echo Machine","Stress Test","Holter Monitor","Cardiac Catheter Lab",
                                 "HbA1c Analyzer","CGM Device","Foot Doppler","Retinal Scanner",
                                 "X-Ray","MRI","CT Scan","Arthroscope","Bone Density Scanner",
                                 "Spirometer","Bronchoscope","Pulse Oximeter","Sleep Study Lab",
                                 "EEG","EMG","Endoscope","Colonoscope","Fibroscan","Ultrasound",
                                 "Dermatoscope","Laser Unit","Phototherapy Unit","Blood Pressure Monitor",
                                 "Glucometer","Thyroid Analyzer","DEXA Scan"])
                    onboard_btn    = gr.Button("✅ Register Doctor Profile", variant="primary", size="lg")
                    onboard_status = gr.Textbox(label="Registration Status", interactive=False)

                    def register_doctor(name, specialty, hospital, city, experience, phone, equipment):
                        import json as _json, uuid as _uuid
                        doctors_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doctors.json")
                        if not all([name, specialty, hospital, city, phone]):
                            return "❌ Please fill in all required fields (marked with *)."
                        try:
                            with open(doctors_path, "r") as f:
                                data = _json.load(f)
                            new_doc = {
                                "id": "doc" + str(_uuid.uuid4())[:6], "name": name,
                                "specialty": specialty, "hospital": hospital, "city": city,
                                "experience_years": int(experience), "equipment": equipment or [],
                                "phone": phone, "rating": 0.0, "feedback_count": 0,
                            }
                            data["doctors"].append(new_doc)
                            with open(doctors_path, "w") as f:
                                _json.dump(data, f, indent=2)
                            return f"✅ Dr. {name} registered! ID: {new_doc['id']} — Now visible in {city} recommendations."
                        except Exception as e:
                            return f"❌ Error: {e}"

                    onboard_btn.click(fn=register_doctor,
                        inputs=[doc_name, doc_specialty, doc_hospital, doc_city,
                                doc_experience, doc_phone, doc_equipment],
                        outputs=[onboard_status])

        # ══════════════════════════════════════════════════════════════════════
        # MAIN APP  (hidden until login)
        # ══════════════════════════════════════════════════════════════════════
        with gr.Column(visible=False) as main_app:
            # ── Welcome bar with Logout ────────────────────────────────────────
            with gr.Row(equal_height=True):
                welcome_bar = gr.HTML("", scale=10)
                with gr.Column(scale=0, min_width=150):
                    logout_btn  = gr.Button("🚪 Logout", elem_id="logout-btn", size="sm")
                    model_switcher = gr.Dropdown(
                        choices=["Nemotron 30B", "Nova Lite"],
                        value="Nemotron 30B",
                        label="Active Model",
                        container=False
                    )

            user_input_id = gr.Textbox(visible=False)  # auto-filled on login

            with gr.Tabs():

                # ── Tab 1: Health Chat ─────────────────────────────────────────
                with gr.TabItem("💬 Health Chat"):
                    current_reminder_id = gr.State(-1)
                    is_alarm_active     = gr.State(False)

                    reminder_alert = gr.Textbox(value="", label="", interactive=False,
                                                container=False, visible=True,
                                                elem_id="reminder-box", show_label=False)
                    ack_btn = gr.Button("✅ Acknowledge and Dismiss Reminder",
                                        visible=True, variant="primary", size="lg", elem_id="ack-btn")
                    hidden_audio = gr.HTML(value="", visible=True, elem_id="alarm-audio")
                    dynamic_css = gr.HTML(
                        value="<style>#ack-btn { display: none !important; } "
                              "#reminder-box { display: none !important; }</style>")
                    timer = gr.Timer(5)

                    history = gr.Chatbot(elem_id="chatbot", label="Chat",
                                         show_label=False, height=500)
                    report_file = gr.File(label="📥 Download Health Report PDF",
                                          visible=False, elem_id="report-download")

                    SHOW_CSS = ("<style>#ack-btn { display: block !important; } "
                                "#reminder-box { display: block !important; }</style>")
                    HIDE_CSS = ("<style>#ack-btn { display: none !important; } "
                                "#reminder-box { display: none !important; }</style>")

                    def check_reminders(uid, current_id, active, current_history):
                        if not uid:
                            return (gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                                    current_id, active, gr.skip())
                        due = fetch_due_reminders(uid)
                        if due and due["id"] != current_id:
                            alert_chat = {"role": "assistant",
                                          "content": (f"---\n### 🚨 ACTIVE SYSTEM REMINDER\n"
                                                      f"**{due['message']}**\n*(Scheduled for {due['time']})*")}
                            # Use native HTML audio tag instead of gr.Audio for guaranteed loop & autoplay
                            # Base64 encode it so the browser ALWAYS plays it instantly, regardless of Docker port bindings or file paths
                            import base64
                            with open(AUDIO_PATH, "rb") as f:
                                b64_audio = base64.b64encode(f.read()).decode("utf-8")
                            audio_html = f'<audio autoplay loop><source src="data:audio/wav;base64,{b64_audio}" type="audio/wav"></audio>'
                            return (gr.update(value=f"🚨 REMINDER: {due['message']}\n⏰ Due: {due['time']}"),
                                    gr.update(), gr.update(value=audio_html), SHOW_CSS,
                                    due["id"], True, current_history + [alert_chat])
                        if due and due["id"] == current_id:
                            return (gr.skip(), gr.skip(), gr.skip(),
                                    gr.skip(), current_id, True, gr.skip())
                        return (gr.update(value=""), gr.update(),
                                gr.update(value=""), HIDE_CSS, -1, False, gr.skip())

                    timer.tick(fn=check_reminders,
                               inputs=[user_input_id, current_reminder_id, is_alarm_active, history],
                               outputs=[reminder_alert, ack_btn, hidden_audio, dynamic_css,
                                        current_reminder_id, is_alarm_active, history])

                    msg = gr.MultimodalTextbox(
                        placeholder="How can I help you today? Or attach a lab report!",
                        show_label=False, container=False, scale=7, file_types=["image"])
                    with gr.Row():
                        submit_btn  = gr.Button("Send", variant="primary", scale=3)
                        clear_btn   = gr.ClearButton([msg, history], value="Clear Chat", scale=1)

                    def format_ack_msg(alert_text, r_id):
                        if r_id != -1:
                            mark_reminder_completed(r_id)
                        return {"text": f"[System: User acknowledged: '{alert_text}'].", "files": []}

                    def clear_alert_ui():
                        return gr.update(value=""), gr.update(value=None), HIDE_CSS, -1, False

                    def disable_chat():
                        return gr.update(interactive=False), gr.update(interactive=False)
                    def enable_chat():
                        return gr.update(interactive=True), gr.update(interactive=True)

                    ack_btn.click(fn=format_ack_msg,
                                  inputs=[reminder_alert, current_reminder_id], outputs=[msg]
                    ).then(fn=clear_alert_ui,
                           outputs=[reminder_alert, hidden_audio, dynamic_css,
                                    current_reminder_id, is_alarm_active]
                    ).then(disable_chat, outputs=[msg, submit_btn]
                    ).then(fn=call_agent, inputs=[user_input_id, msg, history],
                           outputs=[msg, history, report_file]
                    ).then(enable_chat, outputs=[msg, submit_btn])

                    submit_btn.click(disable_chat, outputs=[msg, submit_btn]
                    ).then(call_agent, inputs=[user_input_id, msg, history],
                           outputs=[msg, history, report_file]
                    ).then(enable_chat, outputs=[msg, submit_btn])

                    msg.submit(disable_chat, outputs=[msg, submit_btn]
                    ).then(call_agent, inputs=[user_input_id, msg, history],
                           outputs=[msg, history, report_file]
                    ).then(enable_chat, outputs=[msg, submit_btn])


                # ── Tab 2: Health Dashboard ────────────────────────────────────
                with gr.TabItem("📊 Health Dashboard"):
                    gr.Markdown("### 📈 Your Health Trends\nVisual trends of your recorded health parameters over time.")
                    refresh_btn = gr.Button("🔄 Refresh Charts", variant="secondary",
                                            size="sm", elem_id="dashboard-refresh")

                    with gr.Row():
                        no_data_msg = gr.Markdown(
                            "*No health data yet. Chat with the assistant to log your readings!*",
                            visible=True
                        )

                    chart1 = gr.Plot(label="Blood Glucose & HbA1c", visible=False)
                    chart2 = gr.Plot(label="Blood Pressure",         visible=False)
                    chart3 = gr.Plot(label="Cholesterol & Others",   visible=False)

                    def refresh_charts(uid):
                        if not uid:
                            return (gr.update(visible=True),
                                    gr.update(visible=False),
                                    gr.update(visible=False),
                                    gr.update(visible=False))
                        f1, f2, f3 = build_health_charts(uid)
                        has_data = any([f1, f2, f3])
                        return (
                            gr.update(visible=not has_data),
                            gr.update(value=f1, visible=f1 is not None),
                            gr.update(value=f2, visible=f2 is not None),
                            gr.update(value=f3, visible=f3 is not None),
                        )

                    refresh_btn.click(fn=refresh_charts,
                                      inputs=[user_input_id],
                                      outputs=[no_data_msg, chart1, chart2, chart3])

        # ══════════════════════════════════════════════════════════════════════
        # AUTH HANDLERS
        # ══════════════════════════════════════════════════════════════════════

        def _switch_to_app(username, display_name, status_html=""):
            banner = (
                f'<div id="welcome-bar">'
                f'<span>👋 Welcome, <strong>{display_name}</strong>'
                f'&nbsp;&nbsp;·&nbsp;&nbsp;<code style="background:rgba(255,255,255,0.2);'
                f'padding:2px 6px;border-radius:4px">{username}</code>'
                f'&nbsp;&nbsp;<span style="opacity:0.65;font-size:0.82em">🏥 AI Health Assistant</span></span>'
                f'</div>'
            )
            greeting_history = generate_login_greeting(username, display_name)
            f1, f2, f3 = build_health_charts(username)
            has_data = any([f1, f2, f3])
            return (
                gr.update(visible=False),     # auth_screen
                gr.update(visible=True),      # main_app
                username,                     # logged_in_user state
                display_name,                 # logged_in_name state
                username,                     # user_input_id
                banner,                       # welcome_bar
                status_html,                  # login_status / register_status
                greeting_history,             # history (pre-filled with greeting)
                gr.update(visible=not has_data),  # no_data_msg
                gr.update(value=f1, visible=f1 is not None),  # chart1
                gr.update(value=f2, visible=f2 is not None),  # chart2
                gr.update(value=f3, visible=f3 is not None),  # chart3
            )

        def do_login(username, password):
            if not username or not password:
                return (gr.update(), gr.update(), "", "", "", "",
                        '<p style="color:#ff6b6b;margin:8px 0">⚠️ Please enter both fields.</p>',
                        gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
            result = login_user(username, password)
            if result["status"] == "success":
                return _switch_to_app(result["username"], result["full_name"])
            return (gr.update(), gr.update(), "", "", "", "",
                    f'<p style="color:#ff6b6b;margin:8px 0">❌ {result["message"]}</p>',
                    gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

        def do_register(full_name, username, password, confirm):
            if not all([full_name, username, password, confirm]):
                return (gr.update(), gr.update(), "", "", "", "",
                        '<p style="color:#ff6b6b;margin:8px 0">⚠️ All fields are required.</p>',
                        gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
            if password != confirm:
                return (gr.update(), gr.update(), "", "", "", "",
                        '<p style="color:#ff6b6b;margin:8px 0">❌ Passwords do not match.</p>',
                        gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
            result = register_user(username, password, full_name)
            if result["status"] == "success":
                return _switch_to_app(result["username"], full_name,
                                      '<p style="color:#51cf66;margin:8px 0">✅ Account created!</p>')
            return (gr.update(), gr.update(), "", "", "", "",
                    f'<p style="color:#ff6b6b;margin:8px 0">❌ {result["message"]}</p>',
                    gr.update(), gr.update(), gr.update(), gr.update(), gr.update())

        def do_logout():
            return (
                gr.update(visible=True),   # auth_screen
                gr.update(visible=False),  # main_app
                "", "",                    # clear state
                "",                        # clear user_input_id
                "",                        # clear welcome_bar
                "",                        # clear login_status
                [],                        # clear history
                gr.update(visible=True),   # no_data_msg
                gr.update(value=None, visible=False),   # chart1
                gr.update(value=None, visible=False),   # chart2
                gr.update(value=None, visible=False),   # chart3
            )

        _shared_outputs = [
            auth_screen, main_app,
            logged_in_user, logged_in_name,
            user_input_id, welcome_bar, login_status,
            history,
            no_data_msg, chart1, chart2, chart3,
        ]
        _reg_outputs = [
            auth_screen, main_app,
            logged_in_user, logged_in_name,
            user_input_id, welcome_bar, register_status,
            history,
            no_data_msg, chart1, chart2, chart3,
        ]
        _logout_outputs = [
            auth_screen, main_app,
            logged_in_user, logged_in_name,
            user_input_id, welcome_bar, login_status,
            history,
            no_data_msg, chart1, chart2, chart3,
        ]

        login_btn.click(fn=do_login,     inputs=[li_user, li_pass],                     outputs=_shared_outputs)
        li_pass.submit( fn=do_login,     inputs=[li_user, li_pass],                     outputs=_shared_outputs)
        register_btn.click(fn=do_register, inputs=[reg_name, reg_user, reg_pass, reg_pass2], outputs=_reg_outputs)
        reg_pass2.submit(  fn=do_register, inputs=[reg_name, reg_user, reg_pass, reg_pass2], outputs=_reg_outputs)
        logout_btn.click(fn=do_logout,   outputs=_logout_outputs)

        def handle_model_switch(choice):
            if "Nova" in choice:
                health_assistant_agent.model.model = "bedrock/us.amazon.nova-lite-v1:0"
            else:
                health_assistant_agent.model.model = "nvidia_nim/nvidia/nemotron-3-nano-30b-a3b"
            return gr.update()
            
        model_switcher.change(fn=handle_model_switch, inputs=[model_switcher], outputs=None)

    return demo


if __name__ == "__main__":
    app = create_gradio_interface()
    app.launch(
        server_name="0.0.0.0",
        server_port=7861,
        share=False,
        css=CSS,
        allowed_paths=[AUDIO_PATH],
    )
