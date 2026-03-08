import re
import os

with open("app.py", "r") as f:
    code = f.read()

# Restore Timer
code = re.sub(
    r'# Timer removed.*?dummy_btn = gr.Button\(visible=False\)',
    'timer = gr.Timer(5)',
    code, flags=re.DOTALL
)

# Restore timer.tick block
code = re.sub(
    r'# Background polling task attached to initialization.*?demo\.load.*?elem_id="dummy-btn"\)',
    '''# Connect timer here since history is instantiated
        timer.tick(
            fn=check_reminders, 
            inputs=[user_input_id, current_reminder_id, history], 
            outputs=[reminder_alert, ack_btn, hidden_audio, current_reminder_id, history]
        )''',
    code, flags=re.DOTALL
)

AUDIO_PATH = os.path.abspath("sounds/mixkit-classic-alarm-995.wav")
audio_html_block = f'''
                import os
                AUDIO_PATH = os.path.abspath("sounds/mixkit-classic-alarm-995.wav")
                # Using a 1px transparent GIF onload to force autoplay since React strips script tags
                audio_html = f\'\'\'
                <audio id="sys-alarm" loop src="/file={{AUDIO_PATH}}"></audio>
                <img src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" onload="var a = document.getElementById(\\\'sys-alarm\\\'); if(a) a.play().catch(e=>console.log(e));" />
                \'\'\'
                msg_text = f"ATTENTION: {{due['message']}} (Scheduled for {{due['time']}})"
'''

code = re.sub(
    r'import os\n.*?AUDIO_PATH = .*?\n.*?audio_html = .*?\n.*?msg_text = .*?\n',
    audio_html_block,
    code, flags=re.DOTALL
)

with open("app.py", "w") as f:
    f.write(code)

print("App restored to use Timer and aggressive audio trigger!")
