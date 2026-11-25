"""Rejestruje komendy bota w Discordzie (uruchamiane przez workflow Register command)."""
import os

import requests

API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {os.environ['DISCORD_TOKEN'].strip()}"}
MANAGE_CHANNELS = "16"  # Domyślnie tylko dla osób z tym uprawnieniem; zmienisz to w Ustawieniach serwera → Integracje
TEXT_CHANNELS = [0, 5]


def command(name, description, options=(), manage=False):
    return {
        "name": name,
        "description": description,
        "type": 1,
        "contexts": [0],
        "integration_types": [0],
        "default_member_permissions": MANAGE_CHANNELS if manage else None,
        "options": list(options),
    }


def channel_option(description):
    return {"type": 7, "name": "channel", "description": description, "required": False, "channel_types": TEXT_CHANNELS}


def source_option(description, autocomplete):
    return {"type": 3, "name": "source", "description": description, "required": True, "autocomplete": autocomplete}


COMMANDS = [
    command("when", "When new content will be posted in a channel",
            [channel_option("Channel to check (defaults to the current one)")]),
    command("list", "List all sources and their channels"),
    command("add", "Add a source: r/subreddit, u/user or redgifs/niche",
            [source_option("e.g. r/FemBoys, u/username, redgifs/femboys or a full link", False),
             channel_option("Channel to post in (defaults to the current one)")], manage=True),
    command("remove", "Remove a source", [source_option("Source to remove", True)], manage=True),
    command("post", "Post from a source right now, without waiting for its turn",
            [source_option("Source to post from", True)], manage=True),
]

app = requests.get(f"{API}/applications/@me", headers=HEADERS, timeout=15)
app.raise_for_status()
res = requests.put(f"{API}/applications/{app.json()['id']}/commands", headers=HEADERS, json=COMMANDS, timeout=15)
res.raise_for_status()
print("Zarejestrowano komendy:", ", ".join("/" + c["name"] for c in res.json()))
