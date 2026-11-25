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
SCHEDULE_FILE = "schedule.json"
SOURCES_FILE = "sources.json"
MAX_HISTORY = 6000  # Każdy wysłany post zajmuje 1–3 wpisy (ID posta, ID oryginału, adres mediów)
SOURCES_PER_RUN = 5  # Ile kolejnych źródeł z kolejki obsłużyć w jednym przebiegu
TOP_DAY_LIMIT = 5  # Ile najlepszych postów z TOP dnia (top/?t=day) sprawdzać w każdym przebiegu
MAX_UPLOAD_BYTES = int(9.5 * 1024 * 1024)  # Bezpieczny limit załącznika na serwerze bez boostów (10 MiB)
ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
REDGIFS_TOP_MAX_PAGE = 10  # Z ilu pierwszych stron (po 40 filmów) najpopularniejszych losować fallback
USER_HISTORY_PAGES = 5  # Ile stron (po 100 postów) historii użytkownika Reddita przeszukać w fallbacku
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
SOURCE_PREFIXES = {"reddit": "r/", "user": "u/", "redgifs": "redgifs/"}

DISCORD_API = "https://discord.com/api/v10"
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "User-Agent": "DiscordBot (https://github.com/WojciechGalant1/discord-pull, 1.0)",
}
# Przekazywane przez Cloudflare Worker: co ile minut uruchamia bota oraz źródło wybrane w /post
RUN_INTERVAL_MINUTES = os.environ.get("RUN_INTERVAL_MINUTES", "").strip()
ONLY_SOURCE = os.environ.get("ONLY_SOURCE", "").strip()
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()

_REDGIFS_TOKEN = None


def source_label(kind: str, name: str) -> str:
    return SOURCE_PREFIXES[kind] + name


def discord_api(method: str, path: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 30)
    for attempt in range(3):
        r = requests.request(method, DISCORD_API + path, headers=DISCORD_HEADERS, **kwargs)
        if r.status_code != 429 or attempt == 2:
            return r
        retry_after = 2.0
        try:
            retry_after = float(r.json().get("retry_after", 2.0))
        except Exception:
            pass
        time.sleep(retry_after + 0.5)
    return r


def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"Ostrzeżenie: nie udało się wczytać {path}: {e}")
        return default


def load_sources() -> list:
    """Lista źródeł zapisywana przez komendy /add i /remove (Cloudflare Worker)."""
    data = read_json(SOURCES_FILE, {})
    owner = data.get("repository")
    if REPOSITORY and owner and owner != REPOSITORY:
        print("Lista źródeł pochodzi z innego repozytorium (kopia z szablonu). Dodaj własne źródła komendą /add.")
        return []
    sources = []
    for item in data.get("sources", []):
        if not isinstance(item, dict) or item.get("type") not in SOURCE_PREFIXES:
            continue
        source = (item["type"], str(item.get("name") or ""), str(item.get("channel_id") or ""))
        if all(source) and source not in sources:
            sources.append(source)
    return sources


def save_schedule(sources: list, next_index: int):
    """Plik, z którego komenda /when wylicza czas następnej wysyłki każdego źródła."""
    previous = read_json(SCHEDULE_FILE, {}).get("interval_minutes")
    interval = int(RUN_INTERVAL_MINUTES) if RUN_INTERVAL_MINUTES.isdigit() else previous or 10
    schedule = {
        "last_run": int(time.time()),
        "interval_minutes": interval,
        "sources": [
            {
                "source": source_label(kind, name),
                "channel_id": channel_id,
                "runs_ahead": ((i - next_index) % len(sources)) // SOURCES_PER_RUN,
            }
            for i, (kind, name, channel_id) in enumerate(sources)
        ],
    }
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f)


def load_posted_history():
    items = list(dict.fromkeys(read_json(STATE_FILE, [])))
    return items, set(items)


def save_posted_history(history_list):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(history_list[-MAX_HISTORY:], f)


def state_is_from_other_repo() -> bool:
    """Stan skopiowany razem z szablonem należy do innego repozytorium i nie może blokować wysyłki."""
    if not REPOSITORY:
        return False
    owner = read_json(ROTATION_FILE, {}).get("repository")
    return bool(owner) and owner != REPOSITORY


def load_rotation_index():
    value = read_json(ROTATION_FILE, {}).get("next_index", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def save_rotation_index(index):
    state = {"next_index": index}
    if REPOSITORY:
        state["repository"] = REPOSITORY
    with open(ROTATION_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def arctic_shift_search(params: dict, label: str) -> list | None:
    """Reddit blokuje adresy IP GitHub Actions, więc posty są pobierane z publicznego archiwum Arctic Shift."""
    for attempt in range(2):
        res = requests.get(ARCTIC_SHIFT_URL, params=params, timeout=30)
        # 422 to zwykle chwilowy limit czasu po stronie archiwum ("Timeout. Maybe slow down a bit")
        if res.status_code == 422 and attempt == 0:
            time.sleep(3)
            continue
        if res.status_code != 200:
            print(f"Błąd Arctic Shift HTTP {res.status_code} dla {label}")
            return None
        return [p for p in res.json().get("data", []) if isinstance(p, dict) and p.get("id")]
    return None


def fetch_top_day_posts(sub_name: str):
    """Posty z ostatnich 24h posortowane po score (odpowiednik top/?t=day); Arctic Shift nie sortuje po score."""
    seen_posts = {}
    params = {"subreddit": sub_name, "after": "24h", "sort": "desc", "limit": 100}
    for page in range(5):
        batch = arctic_shift_search(params, f"r/{sub_name}")
        if batch is None:
            if page == 0:
                return None
            break
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


def fetch_older_posts(sub_name: str, posted_ids: set):
    # Najpierw sprawdź starsze posty sprzed >2 dni, a jeśli wszystkie były wysłane — losowe okno z ostatniego roku
    for before_days in (2, random.randint(14, 300)):
        params = {"subreddit": sub_name, "sort": "desc", "limit": 100, "before": f"{before_days}d"}
        posts = arctic_shift_search(params, f"r/{sub_name}") or []
        unseen = [p for p in posts if is_valid_media_post(p) and not is_posted(p, posted_ids)]
        if unseen:
            return unseen
    return []


def unwrap_post_source(post_data: dict) -> dict:
    crossposts = post_data.get("crosspost_parent_list")
    if isinstance(crossposts, list) and crossposts and isinstance(crossposts[0], dict):
        return crossposts[0]
    return post_data


def post_url(source: dict) -> str:
    return html.unescape((source.get("url_overridden_by_dest") or source.get("url") or "").strip())


def redgifs_id_from_url(url: str) -> str:
    match = re.search(r"redgifs\.com/(?:watch|ifr)/([A-Za-z0-9_-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return urlparse(url).path.rstrip("/").split("/")[-1].split(".")[0].lower()


def post_keys(post_data: dict) -> list[str]:
    """Klucze tego samego materiału: ID posta, ID oryginału crossposta i adres mediów (ten sam plik w kilku subredditach)."""
    keys = [post_data["id"]]
    parent = post_data.get("crosspost_parent") or ""
    if parent.startswith("t3_"):
        keys.append(parent[3:])
    source = unwrap_post_source(post_data)
    if source.get("id"):
        keys.append(source["id"])

    url = post_url(source)
    host = urlparse(url).netloc.lower()
    if "redgifs.com" in host:
        # Ten sam prefiks co w niszach RedGifs, więc film z Reddita nie wróci też z niszy
        keys.append(f"redgifs:{redgifs_id_from_url(url)}")
    elif host in ("i.redd.it", "i.imgur.com"):
        keys.append(f"url:{host}{urlparse(url).path}")
    return list(dict.fromkeys(keys))


def is_posted(post_data: dict, posted_ids: set) -> bool:
    return any(key in posted_ids for key in post_keys(post_data))


def mark_posted(keys: list[str], posted_ids: set, posted_history: list):
    for key in keys:
        if key not in posted_ids:
            posted_ids.add(key)
            posted_history.append(key)


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

    url = post_url(source)
    if not url or "/removed_by_moderator/" in url:
        return False

    if source.get("is_gallery") or source.get("media_metadata") or "reddit.com/gallery/" in url:
        return bool(extract_gallery_urls(source))

    if source.get("is_video") or "v.redd.it/" in url or "redgifs.com/" in url:
        return True

    # Odrzuć zwykłe linki do komentarzy na Reddicie bez obrazu/wideo
    parsed = urlparse(url)
    if "reddit.com" in parsed.netloc.lower() and "/comments/" in parsed.path.lower():
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


def fetch_redgifs_niche(niche: str, order: str = "hot", page: int = 1):
    """Zwraca (filmy, liczba_stron) albo (None, 0) przy błędzie."""
    data = redgifs_api_get(f"niches/{niche}/gifs", {"order": order, "count": 40, "page": page})
    if data is None:
        return None, 0
    gifs = [
        gif
        for gif in data.get("gifs", [])
        if isinstance(gif, dict) and gif.get("id") and not gif.get("promoted") and gif.get("published", True)
    ]
    return gifs, data.get("pages") or 1


def fetch_redgifs_top_unseen(niche: str, posted_ids: set) -> list:
    # Pierwsza strona najpopularniejszych (?order=top), a gdy wszystko z niej było, losowa dalsza strona
    gifs, pages = fetch_redgifs_niche(niche, "top", 1)
    if not gifs:
        return []
    unseen = [g for g in gifs if f"redgifs:{g['id']}" not in posted_ids]
    if unseen or pages < 2:
        return unseen
    gifs, _ = fetch_redgifs_niche(niche, "top", random.randint(2, min(pages, REDGIFS_TOP_MAX_PAGE)))
    return [g for g in gifs or [] if f"redgifs:{g['id']}" not in posted_ids]


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


def post_to_discord(channel_id: str, content: str, files: list[tuple] | None = None) -> bool:
    # Tytuły postów pochodzą od obcych osób, więc pingować wolno wyłącznie skonfigurowaną rolę (bez @everyone itp.)
    payload = {
        "content": content,
        "allowed_mentions": {"parse": [], "roles": [ROLE_ID] if ROLE_ID else []},
    }
    path = f"/channels/{channel_id}/messages"
    if files:
        r = discord_api(
            "POST",
            path,
            data={"payload_json": json.dumps(payload)},
            files={f"files[{i}]": file for i, file in enumerate(files)},
            timeout=60,
        )
    else:
        r = discord_api("POST", path, json=payload)

    if r.status_code == 200:
        return True
    if r.status_code in (403, 404):
        print(f"Bot nie może pisać na kanale {channel_id} (HTTP {r.status_code}): sprawdź, czy kanał istnieje "
              "i czy bot ma uprawnienia Wysyłanie wiadomości i Załączanie plików.")
    else:
        print(f"Błąd wysyłki na Discorda: HTTP {r.status_code} - {r.text[:200]}")
    return False


def media_file(url: str, data: bytes, fallback_name: str) -> tuple:
    name = os.path.basename(urlparse(url).path) or fallback_name
    ext = os.path.splitext(name)[1].lower()
    if ext not in MEDIA_TYPES:
        ext = ".mp4" if "format=mp4" in url else ".jpg"
        name = f"{fallback_name}{ext}"
    return name, data, MEDIA_TYPES[ext]


def post_with_links(channel_id: str, header: str, links: list[str]) -> bool:
    # Discord ukrywa adres tylko wtedy, gdy wiadomość zawiera sam link, więc tytuł idzie osobno
    ok = post_to_discord(channel_id, header) if header else False
    for link in links:
        ok = post_to_discord(channel_id, link) or ok
    return ok


def send_media(channel_id: str, header: str, urls: list[str], name_prefix: str) -> bool:
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
        return post_with_links(channel_id, header, links)

    ok = False
    for idx, batch in enumerate(batches):
        ok = post_to_discord(channel_id, header if idx == 0 else "", files=batch) or ok
        time.sleep(1)
    return post_with_links(channel_id, "", links) or ok


def send_redgifs(channel_id: str, gif_id: str, header: str, urls: dict | None = None) -> bool:
    video_bytes = resolve_redgifs_mp4(gif_id, urls)
    if video_bytes:
        return post_to_discord(channel_id, header, files=[(f"{gif_id}.mp4", video_bytes, "video/mp4")])
    return post_with_links(channel_id, header, [f"https://www.redgifs.com/watch/{gif_id}"])


def send_post(channel_id: str, post_data: dict) -> bool:
    title = html.unescape((post_data.get("title") or "").strip())
    if len(title) > 250:
        title = title[:247] + "..."
    header = message_header(title)

    source = unwrap_post_source(post_data)
    raw_url = post_url(source)
    path_lower = urlparse(raw_url).path.lower()

    post_id = post_data.get("id", "post")

    # 1. Galeria Reddita -> zdjęcia z i.redd.it jako załączniki
    if source.get("is_gallery") or source.get("media_metadata") or "reddit.com/gallery/" in raw_url:
        gallery_urls = extract_gallery_urls(source)
        if not gallery_urls:
            return False
        return send_media(channel_id, header, gallery_urls, post_id)

    # 2. Bezpośredni obrazek / GIF (i.redd.it, imgur itp.)
    if path_lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return send_media(channel_id, header, [raw_url], post_id)

    if path_lower.endswith(".gifv"):
        return send_media(channel_id, header, [raw_url[:-5] + ".mp4"], post_id)

    # 3. RedGifs -> spróbuj wrzucić natywny plik .mp4 (z dźwiękiem), fallback do czystego linku watch
    if "redgifs.com/" in raw_url.lower():
        return send_redgifs(channel_id, redgifs_id_from_url(raw_url), header)

    # 4. Natywne wideo Reddita (v.redd.it)
    if source.get("is_video") or "v.redd.it/" in raw_url.lower():
        rv = ((source.get("secure_media") or source.get("media") or {}).get("reddit_video")) or {}
        fallback_url = html.unescape(rv.get("fallback_url") or "")
        if fallback_url:
            return send_media(channel_id, header, [fallback_url], post_id)

    # 5. Pozostałe zewnętrzne linki z mediami
    return post_with_links(channel_id, header, [raw_url])


def send_reddit_posts(channel_id: str, posts: list, posted_ids: set, posted_history: list, label: str, kind: str) -> int:
    sent = 0
    for post in posts:
        # Ten sam materiał mógł już pójść w tym przebiegu z innego posta (crosspost, ten sam plik)
        if is_posted(post, posted_ids):
            continue
        if send_post(channel_id, post):
            mark_posted(post_keys(post), posted_ids, posted_history)
            sent += 1
            print(f"Wysłano {kind} ({post['id']}, score={post.get('score', '?')}) z {label}")
            time.sleep(1.5)
    return sent


def process_subreddit(sub_name: str, channel_id: str, posted_ids: set, posted_history: list):
    label = f"r/{sub_name}"
    top_day_posts = fetch_top_day_posts(sub_name)
    if top_day_posts is None:
        print(f"Nie udało się pobrać postów dla {label}")
        return

    # Sprawdź TOP_DAY_LIMIT najlepszych postów z dzisiaj (top/?t=day) zawierających media
    media_top_day = [p for p in top_day_posts if is_valid_media_post(p)][:TOP_DAY_LIMIT]
    if send_reddit_posts(channel_id, list(reversed(media_top_day)), posted_ids, posted_history, label, "post z TOP dnia"):
        return

    # Fallback: jeśli w TOP dnia nie pojawił się żaden nowy (niewysłany) post, wyślij 1 starszy post z roku
    time.sleep(1)
    unseen_old = fetch_older_posts(sub_name, posted_ids)
    if not unseen_old:
        print(f"Brak nowych i starszych postów dla {label} (sprawdź nazwę subreddita)")
        return
    send_reddit_posts(channel_id, [random.choice(unseen_old)], posted_ids, posted_history, label, "starszy post (fallback)")


def process_user(author: str, channel_id: str, posted_ids: set, posted_history: list):
    label = f"u/{author}"
    params = {"author": author, "sort": "desc", "limit": 100}
    posts = arctic_shift_search(params, label)
    if posts is None:
        print(f"Nie udało się pobrać postów użytkownika {label}")
        return

    # Najpierw posty z ostatnich 24h, od najstarszego
    cutoff = time.time() - 24 * 3600
    fresh = [p for p in posts if (p.get("created_utc") or 0) >= cutoff and is_valid_media_post(p)][:TOP_DAY_LIMIT]
    if send_reddit_posts(channel_id, list(reversed(fresh)), posted_ids, posted_history, label, "nowy post"):
        return

    # Fallback: najnowszy jeszcze niewysłany post, szukając coraz dalej w historii użytkownika
    for _ in range(USER_HISTORY_PAGES):
        unseen = [p for p in posts if is_valid_media_post(p) and not is_posted(p, posted_ids)]
        if unseen:
            send_reddit_posts(channel_id, unseen[:1], posted_ids, posted_history, label, "starszy post (fallback)")
            return
        if len(posts) < 100 or not posts[-1].get("created_utc"):
            break
        params["before"] = posts[-1]["created_utc"]
        posts = arctic_shift_search(params, label)
        if not posts:
            break
    print(f"Brak niewysłanych postów z mediami dla {label} (sprawdź nazwę użytkownika)")


def process_redgifs_niche(niche: str, channel_id: str, posted_ids: set, posted_history: list):
    gifs, _ = fetch_redgifs_niche(niche)
    if gifs is None:
        print(f"Nie udało się pobrać filmów z niszy RedGifs {niche}")
        return

    # ID z RedGifs są zapisywane z prefiksem, żeby nie mieszały się z ID postów z Reddita
    unseen = [g for g in gifs if f"redgifs:{g['id']}" not in posted_ids]

    # Najpierw najwyżej w "hot"; potem losowy niewysłany z dalszej części "hot"; na końcu fallback z najpopularniejszych
    hot = [g for g in gifs[:TOP_DAY_LIMIT] if f"redgifs:{g['id']}" not in posted_ids]
    if hot:
        to_send = list(reversed(hot))
    elif unseen:
        to_send = [random.choice(unseen)]
    else:
        top_unseen = fetch_redgifs_top_unseen(niche, posted_ids)
        if not top_unseen:
            print(f"Brak nowych filmów w niszy RedGifs {niche}, także wśród najpopularniejszych")
            return
        to_send = [random.choice(top_unseen)]
        print(f"Brak nowych filmów w \"hot\" – wysyłam film z najpopularniejszych (fallback) w niszy {niche}")

    for gif in to_send:
        header = message_header(html.unescape((gif.get("description") or "").strip())[:250])
        if send_redgifs(channel_id, gif["id"], header, gif.get("urls")):
            mark_posted([f"redgifs:{gif['id']}"], posted_ids, posted_history)
            print(f"Wysłano film {gif['id']} z niszy RedGifs {niche}")
            time.sleep(1.5)


PROCESSORS = {"reddit": process_subreddit, "user": process_user, "redgifs": process_redgifs_niche}


def process_source(source: tuple, posted_ids: set, posted_history: list):
    kind, name, channel_id = source
    try:
        PROCESSORS[kind](name, channel_id, posted_ids, posted_history)
    except Exception as e:
        print(f"Błąd dla {source_label(kind, name)}: {e}")


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Brak sekretu DISCORD_TOKEN z tokenem bota Discorda.")
    sources = load_sources()
    if not sources:
        print("Brak źródeł. Dodaj je na Discordzie komendą /add, np. /add source:r/NazwaSubreddita")
        return

    fresh_state = state_is_from_other_repo()
    if fresh_state:
        print("Pliki stanu pochodzą z innego repozytorium (kopia z szablonu). Zaczynam od zera.")
        posted_history, posted_ids = [], set()
    else:
        posted_history, posted_ids = load_posted_history()

    # /post: tylko wybrane źródło, bez przesuwania kolejki
    if ONLY_SOURCE:
        chosen = [s for s in sources if source_label(s[0], s[1]).lower() == ONLY_SOURCE.lower()]
        if not chosen:
            print(f"Źródła {ONLY_SOURCE} nie ma na liście.")
        for source in chosen:
            print(f"Na żądanie (/post): {ONLY_SOURCE} → kanał {source[2]}")
            process_source(source, posted_ids, posted_history)
        save_posted_history(posted_history)
        return

    index = 0 if fresh_state else load_rotation_index() % len(sources)
    for _ in range(min(SOURCES_PER_RUN, len(sources))):
        print(f"Kolejka: {source_label(*sources[index][:2])} ({index + 1}/{len(sources)})")
        process_source(sources[index], posted_ids, posted_history)

        # Zapis po każdym źródle, żeby przerwany przebieg nie zgubił tego, co już wysłano
        index = (index + 1) % len(sources)
        save_posted_history(posted_history)
        save_rotation_index(index)

    save_schedule(sources, index)


if __name__ == "__main__":
    main()
