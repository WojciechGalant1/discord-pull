# discord-pull

Bot wysyła nowe posty (filmy i obrazki) z wybranych subredditów na webhooki Discorda co 30 minut przez GitHub Actions.

## GitHub Secrets

Wymagany sekret:
- `DISCORD_CHANNELS` – JSON mapujący nazwy subredditów na webhooki Discorda, np.:
  ```json
  {
    "PhatAssFemboys": "https://discord.com/api/webhooks/...",
    "OnlyIfShesPackin": "https://discord.com/api/webhooks/..."
  }
  ```

Opcjonalne sekrety (do bezpośredniego odpytywania `api.reddit.com` przed fallbackiem do Arctic Shift):
- `REDDIT_COOKIE` – wartość ciasteczka `reddit_session` (lub cały nagłówek `Cookie` z przeglądarki)
- `PROXY_URL` – adres residential proxy (np. `http://user:pass@host:port`), jeśli chcesz łączyć się bezpośrednio z Redditem z ominięciem blokady IP GitHub Actions
