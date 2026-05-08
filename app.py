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

load_dotenv()

st.title("Streamlit Chatbot Interface")

USER_AVATAR = "👤"
BOT_AVATAR = "🤖"

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Function to get available models
@st.cache_resource
def get_available_models():
    try:
        models = [m.name.replace('models/', '') for m in genai.list_models() 
                  if 'generateContent' in m.supported_generation_methods]
        # Prioritize 1.5-flash as it usually has better free tier limits
        preferred = ["gemini-1.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
        sorted_models = sorted(models, key=lambda x: (x not in preferred, preferred.index(x) if x in preferred else x))
        return sorted_models
    except Exception:
        return ["gemini-1.5-flash", "gemini-2.0-flash"] # Fallback

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

# Load chat history from shelve file
def load_chat_history():
    with shelve.open("chat_history") as db:
        return db.get("messages", [])

# Save chat history to shelve file
def save_chat_history(messages):
    with shelve.open("chat_history") as db:
        db["messages"] = messages

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = load_chat_history()

if "analyze_resume" not in st.session_state:
    st.session_state.analyze_resume = None

# Sidebar
with st.sidebar:
    st.header("Settings")
    selected_model = st.selectbox("Select Gemini Model", available_models, index=0)
    
    st.divider()
    st.header("Resume Analysis")
    uploaded_file = st.file_uploader("Upload your resume (PDF or DOCX)", type=["pdf", "docx"])
    
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
                    Please analyze the following resume. Provide:
                    1. A brief summary of the candidate's profile.
                    2. Key skills and strengths identified.
                    3. Areas for improvement or missing information.
                    4. Suggestions for tailoring this resume for a modern tech role.
                    
                    Resume Content:
                    {resume_text}
                    """
                    
                    # Store a flag to trigger analysis in the next run
                    st.session_state.analyze_resume = analysis_prompt
                    st.rerun()
                except Exception as e:
                    st.error(f"Error processing resume: {e}")

    st.divider()
    if st.button("Delete Chat History"):
        st.session_state.messages = []
        save_chat_history([])
        st.rerun()

# Initialize Model
model = genai.GenerativeModel(selected_model)

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

    # Store user message
    # If it's a resume analysis, we want the full prompt in history for context
    # but the display will be handled by the loop above
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

    # Save assistant response
    st.session_state.messages.append({
        "role": "assistant",
        "content": full_response
    })

    save_chat_history(st.session_state.messages)