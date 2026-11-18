import os
import json
import time
import requests

# Mapowanie: "nazwa_subreddita_bez_r": "link_do_webhooka_discord"
CHANNELS = {
    "PhatAssFemboys": "https://discord.com/api/webhooks/TUTAJ_LINK",
    "OnlyIfShesPackin": "https://discord.com/api/webhooks/TUTAJ_LINK",
    # dopisz kolejne aż do 9...
}

STATE_FILE = "posted.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": os.environ["REDDIT_COOKIE"]
}

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        posted_ids = set(json.load(f))
else:
    posted_ids = set()

for sub_name, webhook_url in CHANNELS.items():
    if "TUTAJ_LINK" in webhook_url:
        continue
    try:
        # Pobieranie 5 najnowszych postów w formacie JSON jako zalogowana sesja
        url = f"https://www.reddit.com/r/{sub_name}/new.json?limit=5"
        res = requests.get(url, headers=HEADERS, timeout=15)
        
        if res.status_code != 200:
            print(f"Błąd HTTP {res.status_code} dla r/{sub_name}")
            continue
            
        posts = res.json().get("data", {}).get("children", [])
        
        for item in reversed(posts):
            post = item["data"]
            post_id = post["id"]
            
            if post_id not in posted_ids:
                title = post.get("title", "")
                post_url = post.get("url", f"https://www.reddit.com{post.get('permalink', '')}")
                
                payload = {
                    "content": f"**{title}**\n{post_url}"
                }
                requests.post(webhook_url, json=payload)
                posted_ids.add(post_id)
                time.sleep(1) # Krótka pauza dla limitów Discorda
                
        time.sleep(2) # Krótka pauza między subredditami
    except Exception as e:
        print(f"Błąd dla r/{sub_name}: {e}")

with open(STATE_FILE, "w") as f:
    json.dump(list(posted_ids)[-1000:], f)
