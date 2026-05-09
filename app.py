import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
import os
import shelve
import time
import google.api_core.exceptions
import PyPDF2
import docx
import io
import json
import re
import pandas as pd

load_dotenv()

st.set_page_config(layout="wide", page_title="Streamlit Chatbot Interface")
st.title("Streamlit Chatbot Interface")

USER_AVATAR = "👤"
BOT_AVATAR = "🤖"

# Configure Gemini API
api_key = st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)

# Function to get available models
@st.cache_resource
def get_available_models():
    try:
        models = [m.name.replace('models/', '') for m in genai.list_models() 
                  if 'generateContent' in m.supported_generation_methods]
        preferred = ["gemini-1.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
        sorted_models = sorted(models, key=lambda x: (x not in preferred, preferred.index(x) if x in preferred else x))
        return sorted_models
    except Exception:
        return ["gemini-1.5-flash", "gemini-2.0-flash"]

available_models = get_available_models()


# Function to extract text from PDF
def extract_text_from_pdf(file):
    pdf_reader = PyPDF2.PdfReader(file)
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text()
    return text

# Function to extract text from DOCX
def extract_text_from_docx(file):
    doc = docx.Document(file)
    text = ""
    for para in doc.paragraphs:
        text += para.text + "\n"
    return text

def load_data(key, default):
    with shelve.open("chat_history") as db:
        return db.get(key, default)

def save_data(key, data):
    with shelve.open("chat_history") as db:
        db[key] = data

# Initialize states
if "messages" not in st.session_state:
    st.session_state.messages = load_data("messages", [])

if "candidates" not in st.session_state:
    st.session_state.candidates = load_data("candidates", {})

if "analyze_resume" not in st.session_state:
    st.session_state.analyze_resume = None

# Sidebar
with st.sidebar:
    st.header("Settings")
    selected_model = st.selectbox("Select Gemini Model", available_models, index=0)
    
    st.divider()
    st.header("Resume Analysis")
    uploaded_file = st.file_uploader("Upload your resume (PDF or DOCX)", type=["pdf", "docx"])
    
    scoring_rubric = st.text_area(
        "Job Description & Scoring Rubric",
        value="Evaluate the candidate out of 10 for: Python, Communication, and Cloud Architecture.",
        help="Define the criteria you want the AI to score the resume against."
    )
    
    if uploaded_file is not None:
        if st.button("Analyze Resume"):
            with st.spinner("Extracting text and analyzing..."):
                try:
                    if uploaded_file.type == "application/pdf":
                        resume_text = extract_text_from_pdf(io.BytesIO(uploaded_file.read()))
                    else:
                        resume_text = extract_text_from_docx(io.BytesIO(uploaded_file.read()))
                    
                    # Create analysis prompt
                    analysis_prompt = f"""
                    Please analyze the following resume based on the provided Job Description and Scoring Rubric.
                    
                    Job Description & Scoring Rubric:
                    {scoring_rubric}
                    
                    Provide your analysis in two parts:
                    1. A detailed analysis text discussing the candidate's strengths, weaknesses, and fit.
                    2. At the end of your response, provide a structured JSON object enclosed in ```json ... ``` with the following keys:
                       - "candidate_name": string (extract from resume or use "Unknown" if not found)
                       - "scores": dictionary mapping string criteria to integer score out of 10
                       - "total_score": integer (sum of all scores)
                    
                    Resume Content:
                    {resume_text}
                    """
                    
                    st.session_state.analyze_resume = analysis_prompt
                    st.rerun()
                except Exception as e:
                    st.error(f"Error processing resume: {e}")

    st.divider()
    if st.button("Clear Chat History"):
        st.session_state.messages = []
        save_data("messages", [])
        st.rerun()
    if st.button("Clear Candidates Data"):
        st.session_state.candidates = {}
        save_data("candidates", {})
        st.rerun()

# Initialize Model
model = genai.GenerativeModel(selected_model)

tab1, tab2 = st.tabs(["💬 Chat & Analysis", "📊 Ranking Dashboard"])

with tab1:
    # Display previous messages
    for message in st.session_state.messages:
        avatar = USER_AVATAR if message["role"] == "user" else BOT_AVATAR

        with st.chat_message(message["role"], avatar=avatar):
            if "Resume Content:" in message["content"]:
                st.markdown("📄 **Resume Uploaded for Analysis**")
                with st.expander("View Resume Text"):
                    st.markdown(message["content"])
            else:
                st.markdown(message["content"])

    # User input
    prompt = st.chat_input("How can I help?")

    # Check if we have a resume analysis pending
    if st.session_state.analyze_resume:
        prompt = st.session_state.analyze_resume
        st.session_state.analyze_resume = None

    if prompt:
        st.session_state.messages.append({
            "role": "user",
            "content": prompt
        })

        with st.chat_message("user", avatar=USER_AVATAR):
            if "Resume Content:" in prompt:
                st.markdown("📄 **Resume Uploaded for Analysis**")
                with st.expander("View Resume Text"):
                    st.markdown(prompt)
            else:
                st.markdown(prompt)

        # Generate assistant response
        with st.chat_message("assistant", avatar=BOT_AVATAR):
            message_placeholder = st.empty()

            # Convert history into Gemini format
            chat_history = []
            for msg in st.session_state.messages:
                role = "user" if msg["role"] == "user" else "model"
                chat_history.append({
                    "role": role,
                    "parts": [msg["content"]]
                })

            try:
                # Simple retry mechanism for 429 errors
                max_retries = 3
                retry_delay = 2
                
                for attempt in range(max_retries):
                    try:
                        response = model.generate_content(chat_history)
                        full_response = response.text
                        break
                    except google.api_core.exceptions.ResourceExhausted as e:
                        if attempt < max_retries - 1:
                            st.warning(f"Quota exceeded. Retrying in {retry_delay}s... (Attempt {attempt + 1}/{max_retries})")
                            time.sleep(retry_delay)
                            retry_delay *= 2 # Exponential backoff
                        else:
                            raise e
                    except Exception as e:
                        raise e
                        
            except google.api_core.exceptions.ResourceExhausted:
                st.error("API Error: 429 Quota Exceeded. Please check your plan or try switching to a different model (e.g., Gemini 1.5 Flash) in the sidebar.")
                full_response = "I'm sorry, I've exceeded my message quota for this model. Please try again in a few minutes or switch to another model in the settings."
            except Exception as e:
                st.error(f"API Error: {str(e)}")
                full_response = "I'm sorry, I'm having trouble connecting to the AI. Please check your connection or API key."

            message_placeholder.markdown(full_response)
            
            # Check for JSON block indicating resume analysis scores
            json_match = re.search(r'```json\n(.*?)\n```', full_response, re.DOTALL)
            if json_match:
                try:
                    candidate_data = json.loads(json_match.group(1))
                    candidate_name = candidate_data.get("candidate_name", "Unknown Candidate")
                    
                    st.session_state.candidates[candidate_name] = {
                        "scores": candidate_data.get("scores", {}),
                        "total_score": candidate_data.get("total_score", 0),
                        "notes": ""
                    }
                    save_data("candidates", st.session_state.candidates)
                    st.success(f"Candidate {candidate_name} added to the Ranking Dashboard!")
                except json.JSONDecodeError:
                    st.warning("Could not parse candidate JSON data from the model's response.")

        # Save assistant response
        st.session_state.messages.append({
            "role": "assistant",
            "content": full_response
        })

        save_data("messages", st.session_state.messages)

with tab2:
    st.header("Ranking Dashboard")
    
    if not st.session_state.candidates:
        st.info("No candidates evaluated yet. Upload and analyze a resume in the sidebar to get started.")
    else:
        # Prepare dataframe
        data = []
        for name, info in st.session_state.candidates.items():
            row = {"Name": name, "Total Score": info.get("total_score", 0)}
            row.update(info.get("scores", {}))
            data.append(row)
            
        df = pd.DataFrame(data)
        # Move 'Total Score' column to be right after 'Name'
        cols = ['Name', 'Total Score'] + [col for col in df.columns if col not in ['Name', 'Total Score']]
        df = df[cols]
        
        st.dataframe(
            df.sort_values(by="Total Score", ascending=False), 
            use_container_width=True,
            hide_index=True
        )
        
        st.divider()
        st.subheader("Reviewer Notes")
        
        selected_candidate = st.selectbox("Select Candidate to add notes:", list(st.session_state.candidates.keys()))
        
        if selected_candidate:
            current_notes = st.session_state.candidates[selected_candidate].get("notes", "")
            notes = st.text_area("Notes", value=current_notes, height=150, key=f"notes_{selected_candidate}")
            
            if st.button("Save Notes"):
                st.session_state.candidates[selected_candidate]["notes"] = notes
                save_data("candidates", st.session_state.candidates)
                st.success("Notes saved successfully!")