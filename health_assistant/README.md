# Arogya AI Agent

A proactive, privacy-first agentic health companion designed to bridge the gap between complex medical data and human-friendly actionable health tracking. Built using the Google Agent Development Kit (ADK), LiteLLM, AWS DynamoDB, ChromaDB, and Amazon Bedrock, Arogya AI extracts dense parameters from OCR lab charts and pairs them with unstructured symptom conversations into an exact chronological medical timeline.

## Core Capabilities

- **Multi-Modal Vision Extraction**: Instant AI ingestion of raw lab report images—seamlessly extracting biochemical metrics into a structured temporal datastore.
- **Privacy-First Semantic Memory**: Operates a lightweight, heavily isolated local Vector DB (`ChromaDB`) to silently absorb and retain human conversational cues (allergies, symptoms, incidents) directly mapped to exact timestamps.
- **AWS DynamoDB Telemetry**: Uses powerful cloud NoSQL infrastructure to securely store and process medical measurement trends over time for instant tracking dashboards.
- **Hallucination-Free Clinical Grounding**: Routes complex medical querying against authorized clinical guidelines embedded securely via **Amazon Bedrock & OpenSearch**, enforcing factual accuracy across custom diets and travel plans.
- **Automated Natural Language Reminders**: Parses conversational triggers (e.g., *"remind me to take paracetamol in 3 hours"*) to dynamically set and chron-manage health alert alarms.
- **Location-Aware Physician Discovery**: Dynamically resolves user coordinates via IP addressing to recommend and rank regional physicians based actively on symptom relevance.
- **One-Click Clinical Summary PDFs**: Generates comprehensive PDF reports on-demand for consulting physicians, instantly constructing a bulleted narrative of: *Recent Memories, Simulated Condition Summaries, and Diagnostic Next Steps*.

## Project Structure

- `app.py`: Main entry point containing the Gradio UI, progressive streaming chat loop, and session state.
- `health_agent/agent.py`: Agent configurations, prompt engineering, ADK tool integration, and multimodal image extraction logic.
- `health_agent/tools.py`: Local tooling implementation (`sqlite3` DB schema, `ChromaDB` logic, PDF generation with `reportlab`, etc.).
- `doctors.json`: Seed file providing mock doctor data for the recommendation engine.
- `.env`: Environment file containing API configurations.

## Setup Instructions

### 1. Prerequisites
Ensure you have Python 3.10+ installed.

### 2. Create and Activate a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Required Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configuration
Create a `.env` file in the `health_assistant` root containing the API keys for the models you are using in your `agent.py`. By default, it expects:
```env
NVIDIA_API_KEY="your_nvidia_api_key_here"
```
*(If you switch the agent's LLM to Gemini, OpenAI, or Anthropic inside `agent.py`, provide `GEMINI_API_KEY`, `OPENAI_API_KEY`, etc. respectively.)*

### 5. Run the Application
Start the Chatbot and UI Server:
```bash
python3 app.py
```
This will launch a local web server (typically on `http://0.0.0.0:7861`). Open the URL in your browser to access the AI Health Assistant.

## Database & Local Storage Usage
The project creates databases to store the system data locally upon first startup:
- `health_data.db` (`sqlite3`): Tabular records for users, passwords (SHA-256 hashed), measurements, and scheduled reminders.
- `chroma_db/`: Local vector directory used by `ChromaDB` for the agent's semantic memory.
- `health_reports/`: Local directory holding the PDF snapshots generated when creating consultation summaries.


docker build --no-cache --platform linux/x86_64 -t arogya-ai-agent .