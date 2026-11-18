# discord-pull

Bot wysyła nowe posty z wybranych subredditów na webhooki Discorda co 15 minut.

## GitHub Secrets

- `DISCORD_CHANNELS` - JSON mapujący nazwy subredditów na webhooki, np.
  `{"python":"https://discord.com/api/webhooks/...", "pics":"https://discord.com/api/webhooks/..."}`
- `REDDIT_CLIENT_ID` - ID aplikacji Reddit typu "script"
- `REDDIT_CLIENT_SECRET` - secret aplikacji Reddit

Stwórz aplikację w [Reddit Preferences -> Applications](https://www.reddit.com/prefs/apps),
następnie dodaj jej dane jako repozytoryjne GitHub Secrets.