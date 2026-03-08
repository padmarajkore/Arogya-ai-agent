import re

with open("app.py", "r") as f:
    code = f.read()

# Make sure we use an interval that ensures the DOM remains active
# Replace the timer tick entirely

poll_logic = """        # --- Reminder Alert System ---
        current_reminder_id = gr.State(-1)
        hidden_audio = gr.HTML(visible=True, elem_classes=["hidden-audio"]) 
        timer = gr.Timer(5) # Poll every 5 seconds
        
        # We put them directly on the root Block so they stack cleanly
        reminder_alert = gr.Textbox(visible=False, label="🚨 ACTIVE REMINDER 🚨", interactive=False, container=True)
        ack_btn = gr.Button("✅ Acknowledge and Dismiss Reminder", visible=False, variant="primary", size="lg")
            
        def check_reminders(uid, current_id, current_history):
            uid = uid.strip() if uid else "guest"
            due = fetch_due_reminders(uid)
            if due and due["id"] != current_id:
                # new reminder found!
                import os
                AUDIO_PATH = os.path.abspath("sounds/mixkit-classic-alarm-995.wav")
                # Using a 1px transparent GIF onload to force autoplay since React strips script tags
                audio_html = f'''
                <audio id="sys-alarm" loop src="/file={AUDIO_PATH}"></audio>
                <img src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" onload="var a = document.getElementById('sys-alarm'); if(a) a.play().catch(e=>console.log(e));" />
                '''
                msg_text = f"ATTENTION: {due['message']} (Scheduled for {due['time']})"
                
                # Append a dedicated blank divider so it doesn't merge into the previous Chatbot message visually
                alert_chat = {"role": "assistant", "content": f"\\n\\n---\\n### 🚨 ACTIVE SYSTEM REMINDER\\n**{due['message']}**\\n*(Scheduled for {due['time']})*"}
                new_hist = current_history + [alert_chat]
                
                return gr.update(value=msg_text, visible=True), gr.update(visible=True), audio_html, due["id"], new_hist
            if due and due["id"] == current_id:
                return gr.skip(), gr.skip(), gr.skip(), current_id, gr.skip()
            return gr.update(visible=False), gr.update(visible=False), "", current_id, gr.skip()
"""

# Completely repair the UI Block 
code = re.sub(r'        # --- Reminder Alert System ---.*?return gr\.update\(visible=False\), gr\.update\(visible=False\), gr\.update\(value=None\), current_id, gr\.skip\(\)', poll_logic, code, flags=re.DOTALL)


with open("app.py", "w") as f:
    f.write(code)

print("Audio Loop and Clean UI Restored!")
