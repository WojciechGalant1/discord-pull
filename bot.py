import html
import io
import json
import os
import random
import re
import time
from urllib.parse import urlparse

import requests

STATE_FILE = "posted.json"
ROTATION_FILE = "rotation.json"
MAX_HISTORY = 3000
TOP_DAY_LIMIT = 5  # Ile najlepszych postów z TOP dnia (top/?t=day) sprawdzać w każdym przebiegu
MAX_UPLOAD_BYTES = int(9.5 * 1024 * 1024)  # Bezpieczny limit pod darmowy webhook Discorda (10 MiB)
ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
MAX_FILES_PER_MESSAGE = 10  # Limit załączników w jednej wiadomości Discorda
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
}
ROLE_ID = "1572719490435653813"  # Rola oznaczana w każdym poście; pusty tekst wyłącza oznaczanie

WEBHOOK_PATTERN = re.compile(r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()


def parse_channel_source(token: str):
    """Rozpoznaje źródło kanału: r/nazwa, redgifs/nazwa albo pełny adres subreddita / niszy RedGifs."""
    token = token.strip()
    niche = re.search(r"redgifs\.com/niches/([A-Za-z0-9_-]+)", token, re.IGNORECASE)
    if niche:
        return "redgifs", niche.group(1).lower()
    subreddit = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", token, re.IGNORECASE)
    if subreddit:
        return "reddit", subreddit.group(1)
    short = re.fullmatch(r"(r|redgifs)[/:]([A-Za-z0-9_-]+)/?", token.lstrip("/"), re.IGNORECASE)
    if short:
        if short.group(1).lower() == "redgifs":
            return "redgifs", short.group(2).lower()
        return "reddit", short.group(2)
    return None


def load_line_channels(name: str) -> list:
    raw = os.environ.get(name, "").strip()
    channels = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(\S+?)\s*=?\s*(https?://\S+)", line)
        if not match:
            print(f"Błąd w sekrecie {name}, linijka {line_no}: oczekiwano 'r/nazwa ADRES_WEBHOOKA' albo 'redgifs/nazwa ADRES_WEBHOOKA'. Pomijam.")
            continue
        source = parse_channel_source(match.group(1))
        if source is None:
            print(f"Błąd w sekrecie {name}, linijka {line_no}: nie rozpoznano źródła '{match.group(1)}' (użyj r/nazwa albo redgifs/nazwa). Pomijam.")
            continue
        channels.append((*source, match.group(2)))
    return channels


def load_json_channels(name: str, source: str, parse) -> list:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Błąd: sekret {name} nie jest poprawnym JSON-em ({e}). Pomijam go.")
        return []
    if not isinstance(value, dict):
        print(f"Błąd: sekret {name} musi być obiektem JSON {{\"nazwa\": \"webhook\"}}. Pomijam go.")
        return []
    return [(source, parse(key), webhook_url) for key, webhook_url in value.items()]


def parse_subreddit(raw_key: str) -> str:
    return raw_key.strip().removeprefix("/r/").removeprefix("r/").strip("/")


def parse_redgifs_niche(raw_key: str) -> str:
    key = raw_key.strip()
    niche_match = re.search(r"redgifs\.com/niches/([A-Za-z0-9_-]+)", key, re.IGNORECASE)
    if niche_match:
        return niche_match.group(1).lower()
    return key.strip("/").lower()


def load_channels() -> list:
    configured = (
        load_line_channels("CHANNELS")
        + load_json_channels("DISCORD_CHANNELS", "reddit", parse_subreddit)
        + load_json_channels("REDGIFS_CHANNELS", "redgifs", parse_redgifs_niche)
    )
    channels = []
    for source, name, webhook_url in configured:
        webhook_url = str(webhook_url or "").strip()
        if not name or "TUTAJ_LINK" in webhook_url:
            continue
        if not WEBHOOK_PATTERN.match(webhook_url):
            print(f"Błąd: kanał {source}/{name} nie ma poprawnego adresu webhooka Discorda. Pomijam go.")
            continue
        if (source, name, webhook_url) not in channels:
            channels.append((source, name, webhook_url))
    return channels


CHANNELS = load_channels()
if not CHANNELS:
    raise RuntimeError(
        "Brak skonfigurowanych kanałów. Dodaj sekret CHANNELS, np.:\n"
        "r/NazwaSubreddita https://discord.com/api/webhooks/...\n"
        "redgifs/nazwa-niszy https://discord.com/api/webhooks/..."
    )

_REDGIFS_TOKEN = None


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


def state_is_from_other_repo() -> bool:
    """Stan skopiowany razem z szablonem należy do innego repozytorium i nie może blokować wysyłki."""
    if not REPOSITORY or not os.path.exists(ROTATION_FILE):
        return False
    try:
        with open(ROTATION_FILE, "r", encoding="utf-8") as f:
            owner = json.load(f).get("repository")
    except Exception:
        return False
    return bool(owner) and owner != REPOSITORY


def load_rotation_index():
    if not os.path.exists(ROTATION_FILE):
        return 0
    try:
        with open(ROTATION_FILE, "r", encoding="utf-8") as f:
            value = json.load(f).get("next_index", 0)
        return value if isinstance(value, int) and value >= 0 else 0
    except Exception as e:
        print(f"Ostrzeżenie: nie udało się wczytać {ROTATION_FILE}: {e}")
        return 0


def save_rotation_index(index):
    state = {"next_index": index}
    if REPOSITORY:
        state["repository"] = REPOSITORY
    with open(ROTATION_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def fetch_via_arctic_shift(sub_name: str, mode: str = "top_day", before_days: int | None = None):
    """Reddit blokuje adresy IP GitHub Actions, więc posty są pobierane z publicznego archiwum Arctic Shift."""
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
    return fetch_via_arctic_shift(sub_name, mode="top_day")


def fetch_older_posts(sub_name: str, posted_ids: set):
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


def redgifs_api_get(path: str, params: dict | None = None) -> dict | None:
    global _REDGIFS_TOKEN
    for attempt in range(2):
        token = get_redgifs_token()
        if not token:
            return None
        res = requests.get(
            f"https://api.redgifs.com/v2/{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        if res.status_code == 401 and attempt == 0:
            _REDGIFS_TOKEN = None
            continue
        if res.status_code != 200:
            print(f"Błąd RedGifs HTTP {res.status_code} dla {path}")
            return None
        return res.json()
    return None


def fetch_redgifs_niche(niche: str):
    data = redgifs_api_get(f"niches/{niche}/gifs", {"order": "hot", "count": 40})
    if data is None:
        return None
    return [
        gif
        for gif in data.get("gifs", [])
        if isinstance(gif, dict) and gif.get("id") and not gif.get("promoted") and gif.get("published", True)
    ]


def resolve_redgifs_mp4(gif_id: str, urls: dict | None = None) -> bytes | None:
    if urls is None:
        data = redgifs_api_get(f"gifs/{gif_id}")
        if data is None:
            return None
        urls = (data.get("gif") or {}).get("urls") or {}

    dl_headers = {"Referer": "https://www.redgifs.com/"}
    for quality in ("hd", "sd"):
        mp4_url = urls.get(quality)
        if mp4_url:
            video_bytes = download_file_within_limit(mp4_url, headers=dl_headers)
            if video_bytes:
                return video_bytes
    return None


def message_header(title: str) -> str:
    parts = []
    if ROLE_ID:
        parts.append(f"<@&{ROLE_ID}>")
    if title:
        parts.append(f"**{title}**")
    return " ".join(parts)


def post_to_discord(webhook_url: str, content: str, files: list[tuple] | None = None) -> bool:
    # Tytuły postów pochodzą od obcych osób, więc pingować wolno wyłącznie skonfigurowaną rolę (bez @everyone itp.)
    payload = {
        "content": content,
        "allowed_mentions": {"parse": [], "roles": [ROLE_ID] if ROLE_ID else []},
    }
    for attempt in range(3):
        if files:
            r = requests.post(
                webhook_url,
                data={"payload_json": json.dumps(payload)},
                files={f"files[{i}]": file for i, file in enumerate(files)},
                timeout=60,
            )
        else:
            r = requests.post(webhook_url, json=payload, timeout=15)

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


def media_file(url: str, data: bytes, fallback_name: str) -> tuple:
    name = os.path.basename(urlparse(url).path) or fallback_name
    ext = os.path.splitext(name)[1].lower()
    if ext not in MEDIA_TYPES:
        ext = ".mp4" if "format=mp4" in url else ".jpg"
        name = f"{fallback_name}{ext}"
    return name, data, MEDIA_TYPES[ext]


def post_with_links(webhook_url: str, header: str, links: list[str]) -> bool:
    # Discord ukrywa adres tylko wtedy, gdy wiadomość zawiera sam link, więc tytuł idzie osobno
    ok = post_to_discord(webhook_url, header) if header else False
    for link in links:
        ok = post_to_discord(webhook_url, link) or ok
    return ok


def send_media(webhook_url: str, header: str, urls: list[str], name_prefix: str) -> bool:
    """Wysyła media jako załączniki (bez widocznych linków); pliki, których nie da się pobrać, idą jako same linki."""
    attachments, links = [], []
    for i, url in enumerate(urls):
        data = download_file_within_limit(url)
        if data:
            attachments.append(media_file(url, data, f"{name_prefix}_{i + 1}"))
        else:
            links.append(url)

    batches, current, size = [], [], 0
    for file in attachments:
        if current and (len(current) == MAX_FILES_PER_MESSAGE or size + len(file[1]) > MAX_UPLOAD_BYTES):
            batches.append(current)
            current, size = [], 0
        current.append(file)
        size += len(file[1])
    if current:
        batches.append(current)

    if not batches:
        return post_with_links(webhook_url, header, links)

    ok = False
    for idx, batch in enumerate(batches):
        ok = post_to_discord(webhook_url, header if idx == 0 else "", files=batch) or ok
        time.sleep(1)
    return post_with_links(webhook_url, "", links) or ok


def send_redgifs(webhook_url: str, gif_id: str, header: str, urls: dict | None = None) -> bool:
    video_bytes = resolve_redgifs_mp4(gif_id, urls)
    if video_bytes:
        return post_to_discord(webhook_url, header, files=[(f"{gif_id}.mp4", video_bytes, "video/mp4")])
    return post_with_links(webhook_url, header, [f"https://www.redgifs.com/watch/{gif_id}"])


def send_post(webhook_url: str, post_data: dict) -> bool:
    title = html.unescape((post_data.get("title") or "").strip())
    if len(title) > 250:
        title = title[:247] + "..."
    header = message_header(title)

    source = unwrap_post_source(post_data)
    raw_url = html.unescape((source.get("url_overridden_by_dest") or source.get("url") or "").strip())
    path_lower = urlparse(raw_url).path.lower()

    post_id = post_data.get("id", "post")

    # 1. Galeria Reddita -> zdjęcia z i.redd.it jako załączniki
    if source.get("is_gallery") or source.get("media_metadata") or "reddit.com/gallery/" in raw_url:
        gallery_urls = extract_gallery_urls(source)
        if not gallery_urls:
            return False
        return send_media(webhook_url, header, gallery_urls, post_id)

    # 2. Bezpośredni obrazek / GIF (i.redd.it, imgur itp.)
    if path_lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return send_media(webhook_url, header, [raw_url], post_id)

    if path_lower.endswith(".gifv"):
        return send_media(webhook_url, header, [raw_url[:-5] + ".mp4"], post_id)

    # 3. RedGifs -> spróbuj wrzucić natywny plik .mp4 (z dźwiękiem), fallback do czystego linku watch
    if "redgifs.com/" in raw_url.lower():
        match = re.search(r"redgifs\.com/(?:watch|ifr)/([A-Za-z0-9_-]+)", raw_url, re.IGNORECASE)
        gif_id = (
            match.group(1).lower()
            if match
            else urlparse(raw_url).path.rstrip("/").split("/")[-1].split(".")[0].lower()
        )
        return send_redgifs(webhook_url, gif_id, header)

    # 4. Natywne wideo Reddita (v.redd.it)
    if source.get("is_video") or "v.redd.it/" in raw_url.lower():
        rv = ((source.get("secure_media") or source.get("media") or {}).get("reddit_video")) or {}
        fallback_url = html.unescape(rv.get("fallback_url") or "")
        if fallback_url:
            return send_media(webhook_url, header, [fallback_url], post_id)

    # 5. Pozostałe zewnętrzne linki z mediami
    return post_with_links(webhook_url, header, [raw_url])


def mark_posted(post_id: str, posted_ids: set, posted_history: list):
    posted_ids.add(post_id)
    posted_history.append(post_id)


def process_subreddit(sub_name: str, webhook_url: str, posted_ids: set, posted_history: list):
    top_day_posts = fetch_top_day_posts(sub_name)
    if top_day_posts is None:
        print(f"Nie udało się pobrać postów dla r/{sub_name}")
        return

    # Sprawdź TOP_DAY_LIMIT najlepszych postów z dzisiaj (top/?t=day) zawierających media
    media_top_day = [p for p in top_day_posts if is_valid_media_post(p)][:TOP_DAY_LIMIT]
    sent_in_this_run = 0

    for post in reversed(media_top_day):
        post_id = post["id"]
        if post_id not in posted_ids and send_post(webhook_url, post):
            mark_posted(post_id, posted_ids, posted_history)
            sent_in_this_run += 1
            print(f"Wysłano nowy post z TOP dnia ({post_id}, score={post.get('score', '?')}) z r/{sub_name}")
            time.sleep(1.5)

    # Fallback: jeśli w TOP dnia nie pojawił się żaden nowy (niewysłany) post, wyślij 1 starszy post z roku
    if sent_in_this_run == 0:
        time.sleep(1)
        unseen_old = fetch_older_posts(sub_name, posted_ids)
        if not unseen_old:
            print(f"Brak nowych i starszych postów dla r/{sub_name} (sprawdź nazwę subreddita)")
            return
        chosen_post = random.choice(unseen_old)
        if send_post(webhook_url, chosen_post):
            mark_posted(chosen_post["id"], posted_ids, posted_history)
            print(f"Wysłano starszy post (fallback: {chosen_post['id']}) z r/{sub_name}")


def process_redgifs_niche(niche: str, webhook_url: str, posted_ids: set, posted_history: list):
    gifs = fetch_redgifs_niche(niche)
    if gifs is None:
        print(f"Nie udało się pobrać filmów z niszy RedGifs {niche}")
        return

    # ID z RedGifs są zapisywane z prefiksem, żeby nie mieszały się z ID postów z Reddita
    unseen = [g for g in gifs if f"redgifs:{g['id']}" not in posted_ids]
    if not unseen:
        print(f"Brak nowych filmów w niszy RedGifs {niche}")
        return

    # Najpierw najwyżej w "hot"; gdy w czołówce wszystko wysłane, losowy niewysłany film z dalszej części listy
    hot = [g for g in gifs[:TOP_DAY_LIMIT] if f"redgifs:{g['id']}" not in posted_ids]
    to_send = list(reversed(hot)) or [random.choice(unseen)]

    for gif in to_send:
        header = message_header(html.unescape((gif.get("description") or "").strip())[:250])
        if send_redgifs(webhook_url, gif["id"], header, gif.get("urls")):
            mark_posted(f"redgifs:{gif['id']}", posted_ids, posted_history)
            print(f"Wysłano film {gif['id']} z niszy RedGifs {niche}")
            time.sleep(1.5)


def main():
    channels = CHANNELS

    if state_is_from_other_repo():
        print("Pliki stanu pochodzą z innego repozytorium (kopia z szablonu). Zaczynam od zera.")
        posted_history, posted_ids = [], set()
        index = 0
    else:
        posted_history, posted_ids = load_posted_history()
        index = load_rotation_index() % len(channels)
    source, name, webhook_url = channels[index]
    label = f"RedGifs/{name}" if source == "redgifs" else f"r/{name}"
    print(f"Kolejka: {label} ({index + 1}/{len(channels)})")

    try:
        if source == "redgifs":
            process_redgifs_niche(name, webhook_url, posted_ids, posted_history)
        else:
            process_subreddit(name, webhook_url, posted_ids, posted_history)
    except Exception as e:
        print(f"Błąd dla {label}: {e}")

    save_posted_history(posted_history)
    save_rotation_index((index + 1) % len(channels))


if __name__ == "__main__":
    main()
