import os
import json
import time
import random
import requests

CHANNELS = json.loads(os.environ["DISCORD_CHANNELS"])

STATE_FILE = "posted.json"
USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "discord-pull/1.0")
HEADERS = {"User-Agent": USER_AGENT}
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
REDDIT_ACCESS_TOKEN = None


def get_reddit_access_token():
    global REDDIT_ACCESS_TOKEN

    if REDDIT_ACCESS_TOKEN:
        return REDDIT_ACCESS_TOKEN
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None

    response = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers=HEADERS,
        timeout=15,
    )
    if response.status_code != 200:
        print(
            f"Błąd autoryzacji Reddita: HTTP {response.status_code} "
            f"- sprawdź REDDIT_CLIENT_ID i REDDIT_CLIENT_SECRET"
        )
        return None

    REDDIT_ACCESS_TOKEN = response.json().get("access_token")
    if not REDDIT_ACCESS_TOKEN:
        print("Błąd autoryzacji Reddita: odpowiedź nie zawierała access_token")
        return None
    return REDDIT_ACCESS_TOKEN


def fetch_reddit_json(sub_name, listing, extra_params=None):
    params = {"limit": 5 if listing == "new" else 50, "raw_json": 1}
    if extra_params:
        params.update(extra_params)

    access_token = get_reddit_access_token()
    if access_token:
        headers = {**HEADERS, "Authorization": f"bearer {access_token}"}
        urls = [f"https://oauth.reddit.com/r/{sub_name}/{listing}.json"]
    else:
        headers = HEADERS
        urls = [
            f"https://www.reddit.com/r/{sub_name}/{listing}.json",
            f"https://old.reddit.com/r/{sub_name}/{listing}.json",
        ]

    last_status = None
    for url in urls:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        last_status = response.status_code
        if response.status_code == 200:
            return response.json()
        if response.status_code not in (403, 429):
            print(f"Błąd HTTP {response.status_code} dla r/{sub_name}: {url}")
            continue

        detail = "Reddit zablokował dostęp" if response.status_code == 403 else "Reddit zlimitował żądania"
        print(f"{detail} dla r/{sub_name} (HTTP {response.status_code})")

    if last_status in (403, 429):
        return None
    return None

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        posted_ids = set(json.load(f))
else:
    posted_ids = set()

def send_post(webhook_url, post_data):
    title = post_data.get("title", "")
    permalink = f"https://www.reddit.com{post_data.get('permalink', '')}"
    post_url = post_data.get("url") or permalink
    
    # Dla galerii Reddita link z permalinkiem lepiej generuje podgląd na Discordzie
    if "reddit.com/gallery/" in post_url:
        post_url = permalink

    payload = {
        "content": f"**{title}**\n{post_url}"
    }
    r = requests.post(webhook_url, json=payload, timeout=15)
    if r.status_code not in (200, 204):
        print(f"Błąd wysyłki na Discorda: {r.status_code} - {r.text}")

for sub_name, webhook_url in CHANNELS.items():
    if "TUTAJ_LINK" in webhook_url:
        continue
    try:
        # KROK 1: Sprawdź 5 najnowszych postów (NEW)
        res = fetch_reddit_json(sub_name, "new")
        if res is None:
            continue
            
        new_posts = res.get("data", {}).get("children", [])
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
            res_old = fetch_reddit_json(sub_name, "top", {"t": "year"})

            if res_old is not None:
                old_posts = res_old.get("data", {}).get("children", [])
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
