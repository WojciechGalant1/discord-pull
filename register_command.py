"""Jednorazowo rejestruje komendę /when w Discordzie (uruchamiane przez workflow Register command)."""
import os

import requests

API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {os.environ['DISCORD_TOKEN'].strip()}"}

COMMANDS = [
    {
        "name": "when",
        "description": "Za ile pojawią się nowe treści na kanale",
        "type": 1,
        "contexts": [0],
        "integration_types": [0],
        "options": [
            {
                "type": 7,
                "name": "kanal",
                "description": "Kanał do sprawdzenia (domyślnie bieżący)",
                "required": False,
                "channel_types": [0, 5],
            }
        ],
    }
]

app = requests.get(f"{API}/applications/@me", headers=HEADERS, timeout=15)
app.raise_for_status()
res = requests.put(f"{API}/applications/{app.json()['id']}/commands", headers=HEADERS, json=COMMANDS, timeout=15)
res.raise_for_status()
print("Zarejestrowano komendy:", ", ".join("/" + c["name"] for c in res.json()))
