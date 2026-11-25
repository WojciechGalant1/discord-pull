import html
import io
import json
import os
import random
import re
import time
from urllib.parse import quote, urlparse

import requests

STATE_FILE = "posted.json"
ROTATION_FILE = "rotation.json"
SCHEDULE_FILE = "schedule.json"
MAX_HISTORY = 3000
SOURCES_PER_RUN = 5  # Ile kolejnych źródeł z kolejki obsłużyć w jednym przebiegu
TOP_DAY_LIMIT = 5  # Ile najlepszych postów z TOP dnia (top/?t=day) sprawdzać w każdym przebiegu
MAX_UPLOAD_BYTES = int(9.5 * 1024 * 1024)  # Bezpieczny limit załącznika na serwerze bez boostów (10 MiB)
ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
REDGIFS_TOP_MAX_PAGE = 10  # Z ilu pierwszych stron (po 40 filmów) najpopularniejszych losować fallback
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

DISCORD_API = "https://discord.com/api/v10"
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "User-Agent": "DiscordBot (https://github.com/WojciechGalant1/discord-pull, 1.0)",
}
# Nazwa albo ID kanału, na którym użytkownicy serwera dopisują źródła
CONFIG_CHANNEL = os.environ.get("CONFIG_CHANNEL", "").strip().lstrip("#") or "bot-config"
POSTABLE_CHANNEL_TYPES = (0, 5)  # Kanał tekstowy i kanał ogłoszeń
OK_EMOJI, ERROR_EMOJI = "✅", "❌"
# Co ile minut uruchamia się bot (harmonogram w run.yml albo cron-job.org); służy tylko do szacowania czasów w /when
RUN_INTERVAL_MINUTES = int(os.environ.get("RUN_INTERVAL_MINUTES") or 10)
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


def find_config_channels() -> list:
    """Zwraca pary (kanał konfiguracyjny, wszystkie kanały jego serwera)."""
    if CONFIG_CHANNEL.isdigit():
        r = discord_api("GET", f"/channels/{CONFIG_CHANNEL}")
        if r.status_code != 200:
            print(f"Bot nie widzi kanału konfiguracyjnego o ID {CONFIG_CHANNEL} (HTTP {r.status_code}).")
            return []
        guild_ids = [r.json().get("guild_id")]
    else:
        r = discord_api("GET", "/users/@me/guilds")
        if r.status_code != 200:
            print(f"Nie udało się zalogować bota (HTTP {r.status_code}). Sprawdź sekret DISCORD_TOKEN.")
            return []
        guild_ids = [guild["id"] for guild in r.json()]

    found = []
    for guild_id in guild_ids:
        r = discord_api("GET", f"/guilds/{guild_id}/channels")
        if r.status_code != 200:
            continue
        guild_channels = r.json()
        for channel in guild_channels:
            if channel.get("type") in POSTABLE_CHANNEL_TYPES and CONFIG_CHANNEL in (channel.get("id"), channel.get("name")):
                found.append((channel, guild_channels))
    return found


def fetch_config_messages(channel_id: str) -> list | None:
    """Wiadomości z kanału konfiguracyjnego od najstarszej."""
    messages, before = [], None
    for _ in range(10):
        params = {"limit": 100}
        if before:
            params["before"] = before
        r = discord_api("GET", f"/channels/{channel_id}/messages", params=params)
        if r.status_code != 200:
            print(f"Nie można odczytać kanału konfiguracyjnego (HTTP {r.status_code}). Bot potrzebuje uprawnień "
                  "Wyświetlanie kanału i Czytanie historii wiadomości.")
            return None
        batch = r.json()
        messages.extend(batch)
        if len(batch) < 100:
            break
        before = batch[-1]["id"]
    return list(reversed(messages))


def parse_config_message(content: str, guild_channels: list):
    """Zwraca (kanały, błędy) z linijek w formacie 'źródło #kanał'."""
    by_id = {c["id"]: c for c in guild_channels if c.get("type") in POSTABLE_CHANNEL_TYPES}
    by_name = {c["name"].lower(): c["id"] for c in by_id.values()}
    channels, errors = [], []
    for line in content.replace("`", "").splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 2:
            errors.append(f"`{line.strip()[:80]}` – oczekiwano `źródło #kanał`, np. `r/nazwa #kanał`")
            continue
        source = parse_channel_source(parts[0])
        if source is None:
            errors.append(f"`{parts[0]}` – nieznane źródło (użyj `r/nazwa` albo `redgifs/nazwa`)")
            continue
        mention = re.fullmatch(r"<#(\d+)>|(\d+)", parts[1])
        channel_id = (mention.group(1) or mention.group(2)) if mention else by_name.get(parts[1].lstrip("#").lower())
        if channel_id not in by_id:
            errors.append(f"`{parts[1]}` – nie ma takiego kanału tekstowego na tym serwerze")
            continue
        channels.append((*source, channel_id))
    return channels, errors


def set_status_reaction(channel_id: str, message: dict, ok: bool) -> bool:
    """Ustawia ✅ albo ❌ pod wiadomością; zwraca True, jeśli reakcja się zmieniła."""
    wanted, other = (OK_EMOJI, ERROR_EMOJI) if ok else (ERROR_EMOJI, OK_EMOJI)
    mine = {r["emoji"].get("name") for r in message.get("reactions", []) if r.get("me")}
    base = f"/channels/{channel_id}/messages/{message['id']}/reactions"
    if other in mine:
        discord_api("DELETE", f"{base}/{quote(other)}/@me")
    if wanted in mine:
        return False
    discord_api("PUT", f"{base}/{quote(wanted)}/@me")
    return True


def reply_to(channel_id: str, message_id: str, text: str):
    discord_api("POST", f"/channels/{channel_id}/messages", json={
        "content": text[:2000],
        "message_reference": {"message_id": message_id, "fail_if_not_exists": False},
        "allowed_mentions": {"parse": [], "replied_user": False},
    })


def load_channels() -> list:
    configs = find_config_channels()
    if not configs:
        print(f"Nie znaleziono kanału #{CONFIG_CHANNEL}. Utwórz go na serwerze i daj botowi do niego dostęp.")
        return []

    channels = []
    for config_channel, guild_channels in configs:
        messages = fetch_config_messages(config_channel["id"])
        if messages is None:
            continue
        user_messages = [m for m in messages if not m["author"].get("bot") and m.get("type") in (0, 19)]
        if user_messages and not any(m.get("content") for m in user_messages):
            print("Bot widzi wiadomości na kanale konfiguracyjnym jako puste. Włącz Message Content Intent "
                  "w Discord Developer Portal (Bot → Privileged Gateway Intents).")
            continue
        for message in user_messages:
            if not message.get("content"):
                continue
            found, errors = parse_config_message(message["content"], guild_channels)
            if set_status_reaction(config_channel["id"], message, not errors) and errors:
                reply_to(config_channel["id"], message["id"], "Pominięto:\n" + "\n".join(errors))
            for channel in found:
                if channel not in channels:
                    channels.append(channel)
    return channels


def save_schedule(channels: list, next_index: int):
    """Plik, z którego komenda /when (Cloudflare Worker) wylicza czas następnej wysyłki każdego źródła."""
    schedule = {
        "last_run": int(time.time()),
        "interval_minutes": RUN_INTERVAL_MINUTES,
        "sources": [
            {
                "source": f"redgifs/{name}" if source == "redgifs" else f"r/{name}",
                "channel_id": channel_id,
                "runs_ahead": ((i - next_index) % len(channels)) // SOURCES_PER_RUN,
            }
            for i, (source, name, channel_id) in enumerate(channels)
        ],
    }
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f)


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
    if r.status_code == 403:
        print(f"Bot nie ma uprawnień do pisania na kanale {channel_id} (Wysyłanie wiadomości, Załączanie plików).")
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
    raw_url = html.unescape((source.get("url_overridden_by_dest") or source.get("url") or "").strip())
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
        match = re.search(r"redgifs\.com/(?:watch|ifr)/([A-Za-z0-9_-]+)", raw_url, re.IGNORECASE)
        gif_id = (
            match.group(1).lower()
            if match
            else urlparse(raw_url).path.rstrip("/").split("/")[-1].split(".")[0].lower()
        )
        return send_redgifs(channel_id, gif_id, header)

    # 4. Natywne wideo Reddita (v.redd.it)
    if source.get("is_video") or "v.redd.it/" in raw_url.lower():
        rv = ((source.get("secure_media") or source.get("media") or {}).get("reddit_video")) or {}
        fallback_url = html.unescape(rv.get("fallback_url") or "")
        if fallback_url:
            return send_media(channel_id, header, [fallback_url], post_id)

    # 5. Pozostałe zewnętrzne linki z mediami
    return post_with_links(channel_id, header, [raw_url])


def mark_posted(post_id: str, posted_ids: set, posted_history: list):
    posted_ids.add(post_id)
    posted_history.append(post_id)


def process_subreddit(sub_name: str, channel_id: str, posted_ids: set, posted_history: list):
    top_day_posts = fetch_top_day_posts(sub_name)
    if top_day_posts is None:
        print(f"Nie udało się pobrać postów dla r/{sub_name}")
        return

    # Sprawdź TOP_DAY_LIMIT najlepszych postów z dzisiaj (top/?t=day) zawierających media
    media_top_day = [p for p in top_day_posts if is_valid_media_post(p)][:TOP_DAY_LIMIT]
    sent_in_this_run = 0

    for post in reversed(media_top_day):
        post_id = post["id"]
        if post_id not in posted_ids and send_post(channel_id, post):
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
        if send_post(channel_id, chosen_post):
            mark_posted(chosen_post["id"], posted_ids, posted_history)
            print(f"Wysłano starszy post (fallback: {chosen_post['id']}) z r/{sub_name}")


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
            mark_posted(f"redgifs:{gif['id']}", posted_ids, posted_history)
            print(f"Wysłano film {gif['id']} z niszy RedGifs {niche}")
            time.sleep(1.5)


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Brak sekretu DISCORD_TOKEN z tokenem bota Discorda.")
    channels = load_channels()
    if not channels:
        print(f"Brak źródeł. Napisz na kanale #{CONFIG_CHANNEL} np.: r/NazwaSubreddita #kanał")
        return

    if state_is_from_other_repo():
        print("Pliki stanu pochodzą z innego repozytorium (kopia z szablonu). Zaczynam od zera.")
        posted_history, posted_ids = [], set()
        index = 0
    else:
        posted_history, posted_ids = load_posted_history()
        index = load_rotation_index() % len(channels)

    for _ in range(min(SOURCES_PER_RUN, len(channels))):
        source, name, channel_id = channels[index]
        label = f"RedGifs/{name}" if source == "redgifs" else f"r/{name}"
        print(f"Kolejka: {label} ({index + 1}/{len(channels)})")
        try:
            if source == "redgifs":
                process_redgifs_niche(name, channel_id, posted_ids, posted_history)
            else:
                process_subreddit(name, channel_id, posted_ids, posted_history)
        except Exception as e:
            print(f"Błąd dla {label}: {e}")

        # Zapis po każdym źródle, żeby przerwany przebieg nie zgubił tego, co już wysłano
        index = (index + 1) % len(channels)
        save_posted_history(posted_history)
        save_rotation_index(index)

    save_schedule(channels, index)


if __name__ == "__main__":
    main()
