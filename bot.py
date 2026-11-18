import html
import io
import json
import os
import random
import re
import time
from urllib.parse import urlparse

import requests

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

STATE_FILE = "posted.json"
MAX_HISTORY = 3000
TOP_DAY_LIMIT = 5  # Ile najlepszych postów z TOP dnia (top/?t=day) sprawdzać w każdym przebiegu
MAX_UPLOAD_BYTES = int(9.5 * 1024 * 1024)  # Bezpieczny limit pod darmowy webhook Discorda (10 MiB)
ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"

RAW_CHANNELS = os.environ.get("DISCORD_CHANNELS", "").strip()
if not RAW_CHANNELS:
    raise RuntimeError("Brak zmiennej środowiskowej DISCORD_CHANNELS.")

CHANNELS = json.loads(RAW_CHANNELS)
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
RAW_COOKIE = os.environ.get("REDDIT_COOKIE", "").strip()

_REDGIFS_TOKEN = None


def build_reddit_cookie(raw_cookie: str) -> str:
    if not raw_cookie:
        return ""
    cookie = raw_cookie if "=" in raw_cookie else f"reddit_session={raw_cookie}"
    if "over18=" not in cookie:
        cookie = f"{cookie.rstrip('; ')}; over18=1"
    return cookie


REDDIT_COOKIE = build_reddit_cookie(RAW_COOKIE)


def load_posted_history():
    if not os.path.exists(STATE_FILE):
        return [], set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            items = list(dict.fromkeys(json.load(f)))
        return items, set(items)
    except Exception as e:
        print(f"Ostrzeżenie: nie udało się wczytać {STATE_FILE}: {e}")
        return [], set()


def save_posted_history(history_list):
    trimmed = history_list[-MAX_HISTORY:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


def fetch_via_reddit_direct(sub_name: str, listing: str, extra_params=None):
    """Próbuje pobrać posty bezpośrednio z api.reddit.com przez curl_cffi (Chrome TLS)."""
    if cffi_requests is None or (not REDDIT_COOKIE and not PROXY_URL):
        return None

    params = {"limit": 50, "raw_json": 1}
    if extra_params:
        params.update(extra_params)

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if REDDIT_COOKIE:
        headers["Cookie"] = REDDIT_COOKIE

    kwargs = {
        "headers": headers,
        "params": params,
        "impersonate": "chrome",
        "timeout": 15,
    }
    if PROXY_URL:
        kwargs["proxy"] = PROXY_URL

    for base_url in ("https://api.reddit.com", "https://old.reddit.com"):
        suffix = "" if base_url == "https://api.reddit.com" else ".json"
        url = f"{base_url}/r/{sub_name}/{listing}{suffix}"
        try:
            res = cffi_requests.get(url, **kwargs)
            if res.status_code == 200:
                children = res.json().get("data", {}).get("children", [])
                return [
                    child["data"]
                    for child in children
                    if isinstance(child, dict) and isinstance(child.get("data"), dict)
                ]
            print(f"Info: {base_url} zwrócił HTTP {res.status_code} dla r/{sub_name}, przełączam dalej...")
        except Exception as e:
            print(f"Info: błąd połączenia z {base_url} dla r/{sub_name}: {e}")

    return None


def fetch_via_arctic_shift(sub_name: str, mode: str = "top_day", before_days: int | None = None):
    """Fallback niewymagający proxy ani cookies (działa bezpośrednio z IP GitHub Actions)."""
    if mode == "top_day":
        seen_posts = {}
        params = {
            "subreddit": sub_name,
            "after": "24h",
            "sort": "desc",
            "limit": 100,
        }
        # Pobierz posty z ostatnich 24h (maks. 5 stron po 100) i posortuj malejąco po score (odpowiednik top/?t=day)
        for page in range(5):
            res = requests.get(ARCTIC_SHIFT_URL, params=params, timeout=20)
            if res.status_code != 200:
                if page == 0:
                    print(f"Błąd Arctic Shift HTTP {res.status_code} dla r/{sub_name}")
                    return None
                break

            batch = [p for p in res.json().get("data", []) if isinstance(p, dict) and p.get("id")]
            for post in batch:
                seen_posts[post["id"]] = post

            if len(batch) < 100 or not batch[-1].get("created_utc"):
                break
            params["before"] = batch[-1]["created_utc"]

        return sorted(
            seen_posts.values(),
            key=lambda p: (p.get("score") or 0, p.get("created_utc") or 0),
            reverse=True,
        )

    params = {
        "subreddit": sub_name,
        "sort": "desc",
        "limit": 100,
    }
    if before_days is not None:
        params["before"] = f"{before_days}d"

    res = requests.get(ARCTIC_SHIFT_URL, params=params, timeout=20)
    if res.status_code != 200:
        print(f"Błąd Arctic Shift HTTP {res.status_code} dla r/{sub_name}")
        return None

    data = res.json().get("data", [])
    return [post for post in data if isinstance(post, dict)]


def fetch_top_day_posts(sub_name: str):
    posts = fetch_via_reddit_direct(sub_name, "top", {"t": "day", "limit": 20})
    if posts is not None:
        return posts
    return fetch_via_arctic_shift(sub_name, mode="top_day")


def fetch_older_posts(sub_name: str, posted_ids: set):
    posts = fetch_via_reddit_direct(sub_name, "top", {"t": "year"})
    if posts:
        unseen = [p for p in posts if p.get("id") not in posted_ids and is_valid_media_post(p)]
        if unseen:
            return unseen

    # Najpierw sprawdź starsze posty sprzed >2 dni, a jeśli wszystkie były wysłane — losowe okno z ostatniego roku
    for before_days in (2, random.randint(14, 300)):
        posts = fetch_via_arctic_shift(sub_name, mode="old", before_days=before_days)
        if not posts:
            continue
        unseen = [p for p in posts if p.get("id") not in posted_ids and is_valid_media_post(p)]
        if unseen:
            return unseen
    return []


def unwrap_post_source(post_data: dict) -> dict:
    crossposts = post_data.get("crosspost_parent_list")
    if isinstance(crossposts, list) and crossposts and isinstance(crossposts[0], dict):
        return crossposts[0]
    return post_data


def is_valid_media_post(post_data: dict) -> bool:
    if not post_data or not post_data.get("id"):
        return False
    if post_data.get("removed_by_category"):
        return False

    title = (post_data.get("title") or "").strip()
    if title.lower() in ("[ removed by moderator ]", "[removed]", "[deleted]"):
        return False

    source = unwrap_post_source(post_data)
    if source.get("removed_by_category") or source.get("is_self"):
        return False

    url = (source.get("url_overridden_by_dest") or source.get("url") or "").strip()
    if not url or "/removed_by_moderator/" in url:
        return False

    if source.get("is_gallery") or source.get("media_metadata") or "reddit.com/gallery/" in url:
        return bool(extract_gallery_urls(source))

    if source.get("is_video") or "v.redd.it/" in url or "redgifs.com/" in url:
        return True

    # Odrzuć zwykłe linki do komentarzy na Reddicie bez obrazu/wideo
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if "reddit.com" in parsed.netloc.lower() and "/comments/" in path_lower:
        return False

    return True


def extract_gallery_urls(source: dict) -> list[str]:
    metadata = source.get("media_metadata")
    if not isinstance(metadata, dict) or not metadata:
        return []

    gallery_items = (source.get("gallery_data") or {}).get("items") or []
    ordered_ids = [
        item["media_id"]
        for item in gallery_items
        if isinstance(item, dict) and not item.get("is_deleted") and item.get("media_id") in metadata
    ]
    if not ordered_ids:
        ordered_ids = list(metadata.keys())

    urls = []
    for media_id in ordered_ids:
        meta = metadata.get(media_id) or {}
        if meta.get("status") != "valid":
            continue
        best = meta.get("s") or {}
        media_url = best.get("gif") or best.get("mp4") or best.get("u")
        if media_url:
            urls.append(html.unescape(media_url))
    return urls


def get_redgifs_token() -> str | None:
    global _REDGIFS_TOKEN
    if _REDGIFS_TOKEN:
        return _REDGIFS_TOKEN
    try:
        res = requests.get("https://api.redgifs.com/v2/auth/temporary", timeout=10)
        if res.status_code == 200:
            _REDGIFS_TOKEN = res.json().get("token")
    except Exception:
        pass
    return _REDGIFS_TOKEN


def download_file_within_limit(url: str, headers: dict | None = None) -> bytes | None:
    req_headers = headers or {}
    try:
        head = requests.head(url, headers=req_headers, allow_redirects=True, timeout=10)
        if head.status_code == 200:
            cl = head.headers.get("Content-Length")
            if cl and int(cl) > MAX_UPLOAD_BYTES:
                return None

        with requests.get(url, headers=req_headers, stream=True, timeout=25) as r:
            if r.status_code != 200:
                return None
            buf = io.BytesIO()
            total = 0
            for chunk in r.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    return None
                buf.write(chunk)
            return buf.getvalue()
    except Exception:
        return None


def resolve_redgifs_mp4(gif_id: str) -> bytes | None:
    global _REDGIFS_TOKEN
    token = get_redgifs_token()
    if not token:
        return None

    api_url = f"https://api.redgifs.com/v2/gifs/{gif_id}"
    res = requests.get(api_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if res.status_code == 401:
        _REDGIFS_TOKEN = None
        token = get_redgifs_token()
        if not token:
            return None
        res = requests.get(api_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

    if res.status_code != 200:
        return None

    urls = (res.json().get("gif") or {}).get("urls") or {}
    dl_headers = {"Referer": "https://www.redgifs.com/"}
    for quality in ("hd", "sd"):
        mp4_url = urls.get(quality)
        if mp4_url:
            video_bytes = download_file_within_limit(mp4_url, headers=dl_headers)
            if video_bytes:
                return video_bytes
    return None


def post_to_discord(webhook_url: str, content: str, file_tuple: tuple | None = None) -> bool:
    for attempt in range(3):
        if file_tuple is not None:
            filename, file_bytes, mime = file_tuple
            r = requests.post(
                webhook_url,
                data={"payload_json": json.dumps({"content": content})},
                files={"file": (filename, file_bytes, mime)},
                timeout=30,
            )
        else:
            r = requests.post(webhook_url, json={"content": content}, timeout=15)

        if r.status_code in (200, 204):
            return True

        if r.status_code == 429 and attempt < 2:
            retry_after = 2.0
            try:
                retry_after = float(r.json().get("retry_after", 2.0))
            except Exception:
                pass
            time.sleep(retry_after + 0.5)
            continue

        print(f"Błąd wysyłki na Discorda: HTTP {r.status_code} - {r.text[:200]}")
        return False
    return False


def send_post(webhook_url: str, post_data: dict) -> bool:
    title = html.unescape((post_data.get("title") or "").strip())
    if len(title) > 250:
        title = title[:247] + "..."
    header = f"**{title}**" if title else ""

    source = unwrap_post_source(post_data)
    raw_url = html.unescape((source.get("url_overridden_by_dest") or source.get("url") or "").strip())
    path_lower = urlparse(raw_url).path.lower()

    # 1. Galeria Reddita -> bezpośrednie zdjęcia z i.redd.it (w paczkach po 4 na wiadomość)
    if source.get("is_gallery") or source.get("media_metadata") or "reddit.com/gallery/" in raw_url:
        gallery_urls = extract_gallery_urls(source)
        if not gallery_urls:
            return False
        ok = False
        for idx in range(0, len(gallery_urls), 4):
            chunk = "\n".join(gallery_urls[idx : idx + 4])
            msg = f"{header}\n{chunk}".strip() if idx == 0 else chunk
            ok = post_to_discord(webhook_url, msg) or ok
            if idx + 4 < len(gallery_urls):
                time.sleep(1)
        return ok

    # 2. Bezpośredni obrazek / GIF (i.redd.it, imgur itp.)
    if path_lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        msg = f"{header}\n{raw_url}".strip()
        return post_to_discord(webhook_url, msg)

    if path_lower.endswith(".gifv"):
        mp4_link = raw_url[:-5] + ".mp4"
        return post_to_discord(webhook_url, f"{header}\n{mp4_link}".strip())

    # 3. RedGifs -> spróbuj wrzucić natywny plik .mp4 (z dźwiękiem), fallback do czystego linku watch
    if "redgifs.com/" in raw_url.lower():
        match = re.search(r"redgifs\.com/(?:watch|ifr)/([A-Za-z0-9_-]+)", raw_url, re.IGNORECASE)
        gif_id = (
            match.group(1).lower()
            if match
            else urlparse(raw_url).path.rstrip("/").split("/")[-1].split(".")[0].lower()
        )
        watch_url = f"https://www.redgifs.com/watch/{gif_id}"
        video_bytes = resolve_redgifs_mp4(gif_id)
        if video_bytes:
            return post_to_discord(webhook_url, header, file_tuple=(f"{gif_id}.mp4", video_bytes, "video/mp4"))
        return post_to_discord(webhook_url, f"{header}\n{watch_url}".strip())

    # 4. Natywne wideo Reddita (v.redd.it)
    if source.get("is_video") or "v.redd.it/" in raw_url.lower():
        rv = ((source.get("secure_media") or source.get("media") or {}).get("reddit_video")) or {}
        fallback_url = html.unescape(rv.get("fallback_url") or "")
        if fallback_url:
            video_bytes = download_file_within_limit(fallback_url)
            if video_bytes:
                post_id = post_data.get("id", "video")
                return post_to_discord(webhook_url, header, file_tuple=(f"{post_id}.mp4", video_bytes, "video/mp4"))
            return post_to_discord(webhook_url, f"{header}\n{fallback_url}".strip())

    # 5. Pozostałe zewnętrzne linki z mediami
    return post_to_discord(webhook_url, f"{header}\n{raw_url}".strip())


def main():
    posted_history, posted_ids = load_posted_history()

    for raw_sub_name, webhook_url in CHANNELS.items():
        sub_name = raw_sub_name.strip().removeprefix("/r/").removeprefix("r/").strip("/")
        if not sub_name or not webhook_url or "TUTAJ_LINK" in webhook_url:
            continue

        try:
            top_day_posts = fetch_top_day_posts(sub_name)
            if top_day_posts is None:
                print(f"Nie udało się pobrać postów dla r/{sub_name}")
                continue

            # Sprawdź TOP_DAY_LIMIT najlepszych postów z dzisiaj (top/?t=day) zawierających media
            media_top_day = [p for p in top_day_posts if is_valid_media_post(p)][:TOP_DAY_LIMIT]
            sent_in_this_run = 0

            for post in reversed(media_top_day):
                post_id = post["id"]
                if post_id not in posted_ids:
                    if send_post(webhook_url, post):
                        posted_ids.add(post_id)
                        posted_history.append(post_id)
                        sent_in_this_run += 1
                        print(f"Wysłano nowy post z TOP dnia ({post_id}, score={post.get('score', '?')}) z r/{sub_name}")
                        time.sleep(1.5)

            # Fallback: jeśli w TOP dnia nie pojawił się żaden nowy (niewysłany) post, wyślij 1 starszy post z roku
            if sent_in_this_run == 0:
                time.sleep(1)
                unseen_old = fetch_older_posts(sub_name, posted_ids)
                if unseen_old:
                    chosen_post = random.choice(unseen_old)
                    chosen_id = chosen_post["id"]
                    if send_post(webhook_url, chosen_post):
                        posted_ids.add(chosen_id)
                        posted_history.append(chosen_id)
                        print(f"Wysłano starszy post (fallback: {chosen_id}) z r/{sub_name}")
                else:
                    print(f"Brak nowych i starszych postów dla r/{sub_name} (sprawdź nazwę subreddita)")

            time.sleep(1.5)
        except Exception as e:
            print(f"Błąd dla r/{sub_name}: {e}")

    save_posted_history(posted_history)


if __name__ == "__main__":
    main()
