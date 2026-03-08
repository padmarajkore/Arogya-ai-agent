import re
import os

with open("app.py", "r") as f:
    code = f.read()

# Replace HTML Audio natively returning gr.HTML text back natively to gr.Audio element
# Gradio 6 natively loops audio with gr.Audio JS events if handled properly, but we can also use gr.HTML dynamically.
code = re.sub(r'hidden_audio = gr\.HTML.*?hidden-audio"\]\)', 'hidden_audio = gr.HTML(visible=True, elem_classes=["hidden-audio"])', code)

# Fix the return list elements logic and HTML component
audio_fix = """                import os
                AUDIO_PATH = os.path.abspath("sounds/mixkit-classic-alarm-995.wav")
                # Ensure the autoplay doesn't fail by relying on standard play calls without JS errors
                audio_html = f'''
                <audio id="sys-alarm" loop src="/file={AUDIO_PATH}"></audio>
                <img src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" onload="setTimeout(()=>{var a = document.getElementById('sys-alarm'); if(a) a.play().catch(e=>console.log(e));}, 100);" />
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

# Replace in check_reminders
code = re.sub(r'                import os\n\s+AUDIO_PATH.*?return gr\.update\(visible=False\).*?gr\.skip\(\)\n', audio_fix, code, flags=re.DOTALL)

with open("app.py", "w") as f:
    f.write(code)

print("Patch applied")
