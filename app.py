import json
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import streamlit as st
from google import genai
from google.genai import types
from audio_recorder_streamlit import audio_recorder

from prompts import SYSTEM_PROMPT, WELCOME_MESSAGE_TEMPLATE, EMAIL_DIGEST_PROMPT

# Gemini 2.5 Flash model
MODEL_NAME = "gemini-2.5-flash"

st.set_page_config(
    page_title="SyncLecture AI — Section Tracker",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Dark Theme High-Contrast Styling
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1.8rem;
            padding-bottom: 2.5rem;
        }
        .main-header {
            font-size: 2.1rem;
            font-weight: 800;
            color: #F8FAFC;
            margin-bottom: 2px;
        }
        .prof-badge {
            background-color: #1E293B;
            color: #93C5FD;
            padding: 5px 12px;
            border-radius: 9999px;
            font-size: 0.84rem;
            font-weight: 600;
            border: 1px solid #3B82F6;
            display: inline-block;
            margin-bottom: 14px;
        }
        .section-card {
            background-color: #1E293B;
            color: #F8FAFC;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 14px;
            margin-bottom: 14px;
        }
        .section-card h4 {
            color: #60A5FA;
            margin-top: 0px;
            margin-bottom: 6px;
        }
        .section-card small {
            color: #94A3B8;
        }
        progress {
            accent-color: #3B82F6;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Load Credentials Safely
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    GMAIL_ADDRESS = st.secrets["GMAIL_ADDRESS"]
    GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]
except Exception:
    st.error("⚠️ Missing credentials in `.streamlit/secrets.toml`. Check your keys.")
    st.stop()


@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=GEMINI_API_KEY)


gemini_client = get_gemini_client()


def ask_gemini(parts: list, max_retries: int = 3) -> str:
    """Sends prompt parts to Gemini with retry logic for heavy traffic."""
    for attempt in range(max_retries):
        try:
            response = st.session_state.chat.send_message(parts)
            return response.text
        except Exception as error:
            error_str = str(error)
            if any(k in error_str.lower() for k in ["429", "503", "exhausted", "heavy", "quota", "overloaded"]):
                time.sleep(2 * (attempt + 1))
                continue
            return f"⚠️ Error processing lecture notes: {error_str}"
    return "⚠️ Server is currently under heavy load. Please wait 10 seconds and try again."


def send_email(to_address: str, lecturer_name: str, course_name: str, body_content: str):
    """Sends formatted email briefing via SMTP."""
    try:
        msg = MIMEMultipart()
        msg["From"] = f"SyncLecture AI <{GMAIL_ADDRESS}>"
        msg["To"] = to_address
        msg["Subject"] = f"🎓 Next-Class Briefing: {course_name}"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #1E293B; line-height: 1.6; padding: 20px;">
            <h2 style="color: #4338CA; margin-bottom: 4px;">SyncLecture — Pre-Flight Briefing</h2>
            <p style="color: #64748B; font-size: 14px; margin-top: 0px;">Prepared for Prof. {lecturer_name} | {course_name}</p>
            <hr style="border: 0; border-top: 1px solid #E2E8F0; margin: 16px 0;" />
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px; white-space: pre-wrap;">
{body_content}
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True, "Email sent successfully!"
    except Exception as error:
        return False, str(error)


# Initialize Clean Session State (No hardcoded topics)
if "sections" not in st.session_state:
    st.session_state.sections = {}

if "active_section" not in st.session_state:
    st.session_state.active_section = None


# STEP 1: ONBOARDING VIEW
if "onboarded" not in st.session_state:
    st.markdown('<h1 class="main-header">🎓 SyncLecture AI</h1>', unsafe_allow_html=True)
    st.caption("Cross-section syllabus synchronizer & post-lecture voice handoff assistant.")

    with st.container(border=True):
        st.subheader("Professor Profile Setup")
        st.write("Configure your teaching workspace once to start tracking your sections.")

        with st.form("onboarding_form"):
            name = st.text_input("Lecturer Name", placeholder="e.g. Prof. Sharma")
            course_name = st.text_input("Course Name & Code", placeholder="e.g. CS201 - Data Structures")
            email_address = st.text_input(
                "Recipient Email Address",
                placeholder="professor@university.edu",
                help="Your next-class briefings will be emailed here.",
            )
            submitted = st.form_submit_button("Enter Classroom Hub 🚀", use_container_width=True)

            if submitted:
                if not name.strip() or not course_name.strip() or not email_address.strip():
                    st.warning("⚠️ Please fill in all fields to continue.")
                else:
                    st.session_state.name = name.strip()
                    st.session_state.course_name = course_name.strip()
                    st.session_state.email_address = email_address.strip()
                    st.session_state.chat = gemini_client.chats.create(
                        model=MODEL_NAME,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            temperature=0.3,
                        ),
                    )
                    st.session_state.messages = []
                    st.session_state.onboarded = True
                    st.rerun()
    st.stop()


# STEP 2: SIDEBAR - SECTION SELECTOR & VOICE RECORDER
with st.sidebar:
    st.title("📊 Section Selector")
    st.caption(f"Course: **{st.session_state.course_name}**")

    # Dynamic Section Selector
    if st.session_state.sections:
        section_options = list(st.session_state.sections.keys())
        selected = st.selectbox(
            "Focus on Section:",
            options=section_options,
            index=section_options.index(st.session_state.active_section) if st.session_state.active_section in section_options else 0,
        )
        st.session_state.active_section = selected

        sec_data = st.session_state.sections[selected]
        st.markdown(
            f"""
            <div class="section-card">
                <h4>{selected}</h4>
                <small>🕒 Schedule: {sec_data['schedule']}</small><br>
                <small>📍 Last: {sec_data['last_topic']}</small><br>
                <progress value="{sec_data['progress']}" max="100" style="width:100%"></progress>
                <small>{sec_data['progress']}% Complete</small>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.info("👋 No sections created yet. Use the form below to add your first section.")

    # Manage Sections Accordion
    with st.expander("⚙️ Add / Manage Sections", expanded=(len(st.session_state.sections) == 0)):
        new_sec_name = st.text_input("New Section Name", placeholder="e.g. Section A")
        new_sec_sched = st.text_input("Schedule", placeholder="e.g. Mon/Wed 10:00 AM")
        if st.button("➕ Add Section", use_container_width=True):
            if new_sec_name.strip():
                sec_name = new_sec_name.strip()
                st.session_state.sections[sec_name] = {
                    "schedule": new_sec_sched.strip() or "Not specified",
                    "progress": 0,
                    "last_topic": "No topics logged yet",
                    "doubts": "None reported",
                    "next_hook": "Ready for initial class",
                    "assignment": "None assigned",
                }
                st.session_state.active_section = sec_name
                st.rerun()

        if st.session_state.active_section:
            st.divider()
            edit_prog = st.slider(
                f"Adjust {st.session_state.active_section} Progress (%)",
                0,
                100,
                st.session_state.sections[st.session_state.active_section]["progress"],
            )
            if edit_prog != st.session_state.sections[st.session_state.active_section]["progress"]:
                st.session_state.sections[st.session_state.active_section]["progress"] = edit_prog
                st.rerun()

    st.divider()
    st.subheader("🎙️ Instant Voice Handoff")
    if st.session_state.active_section:
        st.caption(f"Logging for: **{st.session_state.active_section}**")
        audio_bytes = audio_recorder(
            text="",
            recording_color="#e11d48",
            neutral_color="#4f46e5",
            icon_name="microphone",
            icon_size="2x",
        )
    else:
        st.caption("Please add a section first before recording notes.")
        audio_bytes = None


# STEP 3: MAIN VIEW - DOSSIER & ACTIONS
header_col, action_col = st.columns([5, 3], vertical_alignment="center")

with header_col:
    st.markdown('<h1 class="main-header">🎓 SyncLecture Workspace</h1>', unsafe_allow_html=True)
    focused_label = st.session_state.active_section if st.session_state.active_section else "No Section Selected"
    st.markdown(
        f'<div class="prof-badge">👤 {st.session_state.name} &nbsp;|&nbsp; 📚 {st.session_state.course_name} &nbsp;|&nbsp; 🎯 Focused: {focused_label}</div>',
        unsafe_allow_html=True,
    )

with action_col:
    can_send = len(st.session_state.messages) > 0 and len(st.session_state.sections) > 0
    if st.button("✉️ Send Briefing to Email", disabled=not can_send, use_container_width=True, type="primary"):
        with st.spinner("Compiling briefing and sending email..."):
            briefing_text = ask_gemini([EMAIL_DIGEST_PROMPT])
            ok, err_or_msg = send_email(
                st.session_state.email_address,
                st.session_state.name,
                st.session_state.course_name,
                briefing_text,
            )
            if ok:
                st.toast("✅ Briefing delivered to your inbox!", icon="✉️")
                st.success(f"Briefing sent to {st.session_state.email_address}!")
            else:
                st.error(f"Failed to send email: {err_or_msg}")

# Display Active Section Dossier Card
if st.session_state.active_section and st.session_state.active_section in st.session_state.sections:
    cur = st.session_state.sections[st.session_state.active_section]
    with st.container(border=True):
        col1, col2, col3 = st.columns([3, 3, 3])
        with col1:
            st.markdown(f"**📍 Last Finished Topic:**\n\n{cur['last_topic']}")
            st.markdown(f"**📋 Assigned Homework:**\n\n{cur['assignment']}")
        with col2:
            st.markdown(f"**⚠️ Student Doubts & Friction:**\n\n{cur['doubts']}")
            st.markdown(f"**🕒 Schedule:**\n\n{cur['schedule']}")
        with col3:
            st.markdown(f"**⚡ Next-Class Warmup Hook:**\n\n{cur['next_hook']}")
            st.progress(cur["progress"] / 100, text=f"Syllabus: {cur['progress']}%")
else:
    st.info("👈 Use the **'Add / Manage Sections'** panel in the sidebar to add your class sections.")

st.divider()


# Message Rendering Function
def render_message(message: dict):
    with st.chat_message(message["role"], avatar="🎓" if message["role"] == "assistant" else "👤"):
        if message["kind"] == "text":
            st.markdown(message["content"])
        elif message["kind"] == "audio":
            st.audio(message["content"], format="audio/wav")
            st.caption(f"🎙️ Recorded Voice Memo for {message.get('section', 'General')}")


# Initialize Welcome Message Once
if not st.session_state.messages:
    welcome_text = WELCOME_MESSAGE_TEMPLATE.format(
        name=st.session_state.name,
        course_name=st.session_state.course_name,
    )
    st.session_state.messages.append({"role": "assistant", "kind": "text", "content": welcome_text})

for msg in st.session_state.messages:
    render_message(msg)


# Handle Voice Input
if audio_bytes and ("last_audio" not in st.session_state or st.session_state.last_audio != audio_bytes):
    st.session_state.last_audio = audio_bytes
    st.session_state.messages.append({
        "role": "user",
        "kind": "audio",
        "content": audio_bytes,
        "section": st.session_state.active_section,
    })
    render_message(st.session_state.messages[-1])

    with st.spinner(f"Analyzing audio log for {st.session_state.active_section}..."):
        parts = [
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
            f"This voice memo is for {st.session_state.active_section} of {st.session_state.course_name}. "
            "Extract: Topics covered, exact stop point, student doubts, assignments, and a 5-minute warmup hook for next class.",
        ]
        response_text = ask_gemini(parts)
        st.session_state.messages.append({"role": "assistant", "kind": "text", "content": response_text})
        render_message(st.session_state.messages[-1])


# Handle Text Input
user_text = st.chat_input(
    f"Log notes for {st.session_state.active_section if st.session_state.active_section else 'your class'} (e.g. 'Covered slide 14, gave Quiz 1')..."
)
if user_text:
    section_prefix = f"[{st.session_state.active_section}] " if st.session_state.active_section else ""
    formatted_input = f"{section_prefix}{user_text}"
    st.session_state.messages.append({"role": "user", "kind": "text", "content": formatted_input})
    render_message(st.session_state.messages[-1])
    with st.spinner("Updating section progress..."):
        ans = ask_gemini([formatted_input])
        st.session_state.messages.append({"role": "assistant", "kind": "text", "content": ans})
        render_message(st.session_state.messages[-1])