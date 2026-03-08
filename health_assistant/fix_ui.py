import re
import os

with open("app.py", "r") as f:
    code = f.read()

# Replace the HTML audio with a proper Gradio Audio component that accepts file paths dynamically
audio_component = r'hidden_audio = gr.Audio(visible=True, autoplay=True, label="Alarm", elem_classes=["hidden-audio"])'

code = re.sub(r'hidden_audio = gr\.HTML.*?\]\)', audio_component, code)


audio_logic = '''
                import os
                AUDIO_PATH = os.path.abspath("sounds/mixkit-classic-alarm-995.wav")
                msg_text = f"ATTENTION: {due['message']} (Scheduled for {due['time']})"
                
                # Append a dedicated blank divider so it doesn't merge into the previous Chatbot message visually
                alert_chat = {"role": "assistant", "content": f"\\n\\n---\\n### 🚨 ACTIVE SYSTEM REMINDER\\n**{due['message']}**\\n*(Scheduled for {due['time']})*"}
                new_hist = current_history + [alert_chat]
                
                return gr.update(value=msg_text, visible=True), gr.update(visible=True), gr.update(value=AUDIO_PATH, autoplay=True), due["id"], new_hist
'''

# Replace the audio_html block and return logic
code = re.sub(r'import os.*?return gr\.update.*?new_hist\n', audio_logic, code, flags=re.DOTALL)

# In clear_alert_ui, reset the audio properly
code = re.sub(
    r'def clear_alert_ui\(\):\n\s+return gr\.update.*?-1\n', 
    'def clear_alert_ui():\n            return gr.update(value="", visible=False), gr.update(visible=False), gr.update(value=None), -1\n',
    code
)


with open("app.py", "w") as f:
    f.write(code)

print("UI Fixed.")
