import sys

with open("app.py", "r") as f:
    content = f.read()

# Remove the get_user_city_from_ip from tools imports, we'll redefine it conceptually, but the tool is in tools.py

js_snippet = """
function getDeviceIP() {
    return fetch('https://api.ipify.org?format=json')
        .then(response => response.json())
        .then(data => data.ip)
        .catch(err => { console.error('Error fetching IP:', err); return ''; });
}
"""

