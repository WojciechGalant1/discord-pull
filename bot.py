import os
import json
import praw
import requests

# Mapowanie: "nazwa_subreddita_bez_r": "link_do_webhooka_discord"
CHANNELS = {
    "StraightToSissy": "https://discord.com/api/webhooks/TUTAJ_LINK_1",
    "drugi_subreddit": "https://discord.com/api/webhooks/TUTAJ_LINK_2",
    "trzeci_subreddit": "https://discord.com/api/webhooks/TUTAJ_LINK_3",
    # dopisz kolejne aż do 9...
}

STATE_FILE = "posted.json"

# Wczytanie historii wysłanych postów, żeby nie wysyłać duplikatów
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        posted_ids = set(json.load(f))
else:
    posted_ids = set()

reddit = praw.Reddit(
    client_id=os.environ["REDDIT_CLIENT_ID"],
    client_secret=os.environ["REDDIT_CLIENT_SECRET"],
    username=os.environ["REDDIT_USERNAME"],
    password=os.environ["REDDIT_PASSWORD"],
    user_agent="discord_webhook_feed/1.0"
)

for sub_name, webhook_url in CHANNELS.items():
    if "TUTAJ_LINK" in webhook_url:
        continue
    try:
        subreddit = reddit.subreddit(sub_name)
        # Pobiera 5 najnowszych postów z danego subreddita
        for post in reversed(list(subreddit.new(limit=5))):
            if post.id not in posted_ids:
                payload = {
                    "content": f"**{post.title}**\n{post.url}"
                }
                requests.post(webhook_url, json=payload)
                posted_ids.add(post.id)
    except Exception as e:
        print(f"Błąd dla r/{sub_name}: {e}")

# Zapisanie ID wysłanych postów (trzymamy ostatnie 1000)
with open(STATE_FILE, "w") as f:
    json.dump(list(posted_ids)[-1000:], f)