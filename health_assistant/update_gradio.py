import re

with open("app.py", "r") as f:
    code = f.read()

# Replace timer tick with gr.Button visible clock
code = re.sub(
r'''timer = gr.Timer\(5\) # Poll every 5 seconds''',
r'''# Timer removed, replaced with Gradio load daemon
        dummy_btn = gr.Button(visible=False)''',
code)

code = re.sub(
r'''        # Connect timer here since history is instantiated
        timer\.tick.*?\)''',
r'''        # Background polling task attached to initialization
        dummy_btn.click(
            fn=check_reminders, 
            inputs=[user_input_id, current_reminder_id, history], 
            outputs=[reminder_alert, ack_btn, hidden_audio, current_reminder_id, history],
            every=5  # Runs automatically every 5 seconds securely
        )
        demo.load(fn=lambda: None, outputs=[], js="(res) => { document.querySelector('#dummy-btn').click(); }", elem_id="dummy-btn")  # Just hook into the every loop''',
code, flags=re.DOTALL)
    
print("Replaced!")
