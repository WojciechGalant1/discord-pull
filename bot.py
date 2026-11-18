import os
import json
import time
import random
import requests

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

def send_post(webhook_url, post_data):
    title = post_data.get("title", "")
    post_url = post_data.get("url", f"https://www.reddit.com{post_data.get('permalink', '')}")
    payload = {
        "content": f"**{title}**\n{post_url}"
    }
    requests.post(webhook_url, json=payload)

for sub_name, webhook_url in CHANNELS.items():
    if "TUTAJ_LINK" in webhook_url:
        continue
    try:
        # KROK 1: Sprawdź 5 najnowszych postów (NEW)
        url_new = f"https://www.reddit.com/r/{sub_name}/new.json?limit=5"
        res = requests.get(url_new, headers=HEADERS, timeout=15)
        
        if res.status_code != 200:
            print(f"Błąd HTTP {res.status_code} dla r/{sub_name}")
            continue
            
        new_posts = res.json().get("data", {}).get("children", [])
        sent_in_this_run = 0
        
        for item in reversed(new_posts):
            post = item["data"]
            if post["id"] not in posted_ids:
                send_post(webhook_url, post)
                posted_ids.add(post["id"])
                sent_in_this_run += 1
                time.sleep(1)

        # KROK 2: Jeśli NIE BYŁO żadnych nowych postów, pobierz 1 starszy z TOP (z ostatniego roku)
        if sent_in_this_run == 0:
            time.sleep(1)
            url_old = f"https://www.reddit.com/r/{sub_name}/top.json?t=year&limit=50"
            res_old = requests.get(url_old, headers=HEADERS, timeout=15)
            
            if res_old.status_code == 200:
                old_posts = res_old.json().get("data", {}).get("children", [])
                # Odfiltruj te, które już kiedyś były wysłane
                unseen_old = [item["data"] for item in old_posts if item["data"]["id"] not in posted_ids]
                
                if unseen_old:
                    chosen_post = random.choice(unseen_old)
                    send_post(webhook_url, chosen_post)
                    posted_ids.add(chosen_post["id"])
                    print(f"Wysłano starszy post (fallback) dla r/{sub_name}")

        time.sleep(2)
    except Exception as e:
        print(f"Błąd dla r/{sub_name}: {e}")

# Zwiększamy pamięć historii do 3000 ID, żeby starsze posty się nie powtarzały
with open(STATE_FILE, "w") as f:
    json.dump(list(posted_ids)[-3000:], f)
