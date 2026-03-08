import asyncio
import os
from dotenv import load_dotenv

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService

from health_agent.agent import health_assistant_agent
from utils import call_agent_async

load_dotenv()

# We use SQLite for conversational/agent state as well.
db_url = "sqlite:///./health_session_state.db"
session_service = DatabaseSessionService(db_url=db_url)

async def main_async():
    APP_NAME = "Personal_Health_Assistant"
    
    print("\n==============================================")
    print("Welcome to your Secure Multi-User AI Health Assistant!")
    print("All memory and parametric queries are securely siloed by User ID.")
    print("==============================================\n")
    
    USER_ID = input("Please enter your unique User ID (e.g., 'bharat_123'): ").strip()
    if not USER_ID:
        USER_ID = "guest"
        
    print(f"\n[AI Health Assistant System Starting for {USER_ID}...]")

    # Identify if session exists
    existing_sessions = session_service.list_sessions(
        app_name=APP_NAME,
        user_id=USER_ID,
    )

    if existing_sessions and len(existing_sessions.sessions) > 0:
        SESSION_ID = existing_sessions.sessions[0].id
        print(f"Continuing existing session from DB: {SESSION_ID}")
    else:
        initial_state = {
            "user_name": USER_ID,
            "user_id": USER_ID,
        }
        new_session = session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            state=initial_state,
        )
        SESSION_ID = new_session.id
        print(f"Created new unique session: {SESSION_ID}")

    # Set up the runner with the new/existing session.
    runner = Runner(
        agent=health_assistant_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    print("\nHello! I'm ready to learn about your health securely.")
    print("Type 'exit' to quit at any time.\n")

    while True:
        try:
            user_input = input(f"{USER_ID}: ")
            
            if user_input.lower() in ["exit", "quit", "q"]:
                print("Ending health session. Take care!")
                break
                
            if not user_input.strip():
                continue

            # Dynamically inject the user_id context string into every prompt
            prompt_with_context = f"[CONTEXT: Focus strictly on user_id '{USER_ID}']\n{user_input}"
            
            await call_agent_async(runner, USER_ID, SESSION_ID, prompt_with_context)
            
        except (KeyboardInterrupt, EOFError):
            print("\nEnding health session. Goodbye!")
            break

if __name__ == "__main__":
    asyncio.run(main_async())
