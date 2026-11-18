# discord-pull

Bot wysyła nowe posty (filmy i obrazki) z wybranych subredditów na webhooki Discorda przez GitHub Actions. Co 10 minut obsługiwany jest jeden kanał, potem kolejny, w pętli. Przy czterech kanałach każdy dostaje treść co 40 minut.

## GitHub Secrets

Wymagany jest co najmniej jeden z dwóch sekretów (oba trafiają do wspólnej rotacji):
- `DISCORD_CHANNELS` – JSON mapujący nazwy subredditów na webhooki Discorda, np.:
  ```json
  {
    "PhatAssFemboys": "https://discord.com/api/webhooks/...",
    "OnlyIfShesPackin": "https://discord.com/api/webhooks/..."
  }
  ```
- `REDGIFS_CHANNELS` – JSON mapujący nisze RedGifs na webhooki Discorda. Kluczem jest nazwa niszy albo pełny adres niszy; filmy są pobierane w kolejności `?order=hot`, np.:
  ```json
  {
    "sissy-hypnosis": "https://discord.com/api/webhooks/...",
    "https://www.redgifs.com/niches/femboys": "https://discord.com/api/webhooks/..."
  }
  ```

Opcjonalne sekrety (do bezpośredniego odpytywania `api.reddit.com` przed fallbackiem do Arctic Shift):
- `REDDIT_COOKIE` – wartość ciasteczka `reddit_session` (lub cały nagłówek `Cookie` z przeglądarki)
- `PROXY_URL` – adres residential proxy (np. `http://user:pass@host:port`), jeśli chcesz łączyć się bezpośrednio z Redditem z ominięciem blokady IP GitHub Actions
