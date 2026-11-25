"""Jednorazowo rejestruje komendę /when w Discordzie (uruchamiane przez workflow Register command)."""
import os

import requests

API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {os.environ['DISCORD_TOKEN'].strip()}"}

COMMANDS = [
    {
        "name": "when",
        "description": "When new content will be posted in a channel",
        "type": 1,
        "contexts": [0],
        "integration_types": [0],
        "options": [
            {
                "type": 7,
                "name": "channel",
                "description": "Channel to check (defaults to the current one)",
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
