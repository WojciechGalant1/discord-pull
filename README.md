# discord-pull

Bot wysyła filmy i obrazki z wybranych subredditów oraz nisz RedGifs na kanały Discorda przez webhooki. Działa w GitHub Actions, bez własnego serwera.

Każde uruchomienie obsługuje jeden kanał, a następne bierze kolejny, w pętli. Z subredditów brane są najlepsze posty dnia (`top/?t=day`), z RedGifs filmy z niszy w kolejności `?order=hot`. Gdy nie ma nic nowego, bot wysyła jeden starszy, jeszcze niewysłany post. Wysłane ID są zapisywane w `posted.json`, więc nic się nie powtarza.

## Instrukcja krok po kroku

### 1. Utwórz własne repozytorium na GitHubie

1. Zaloguj się na [github.com](https://github.com), otwórz stronę tego repozytorium i kliknij zielony przycisk **Use this template → Create a new repository**. Dostaniesz własną kopię bez historii commitów. Historia wysłanych postów z oryginału zostanie zignorowana przy pierwszym uruchomieniu, więc Twój bot wyśle wszystko od nowa.
2. Wybierz widoczność repozytorium:
   - **Private** – nikt nie widzi kodu ani historii wysłanych postów. GitHub daje wtedy 2000 darmowych minut Actions miesięcznie, a każde uruchomienie liczy się jako co najmniej 1 minuta. Częstotliwość ustaw tak, żeby się w tym zmieścić (patrz krok 6).
   - **Public** – minuty Actions są bez limitu. Sekrety z webhookami nadal pozostają ukryte, ale każdy widzi kod i plik `posted.json`.
3. Wejdź w **Settings → Actions → General**:
   - w sekcji **Actions permissions** zaznacz **Allow all actions and reusable workflows**,
   - na dole strony w **Workflow permissions** zaznacz **Read and write permissions** i kliknij **Save**. Bot musi móc zapisywać w repozytorium pliki `posted.json` i `rotation.json`.

### 2. Utwórz webhooki na serwerze Discord

Dla każdego kanału, na który mają trafiać posty:

1. Kliknij ikonę koła zębatego przy kanale (**Edytuj kanał**).
2. W zakładce **Przegląd** włącz **Kanał z ograniczeniem wiekowym**, jeśli treści są dla dorosłych.
3. W zakładce **Integracje → Webhooki** kliknij **Nowy webhook**, nadaj mu nazwę i kliknij **Kopiuj adres URL webhooka**.

Jeden webhook to jeden kanał. Każdy subreddit i każda nisza RedGifs potrzebuje własnego webhooka.

Adres webhooka działa jak hasło: kto go ma, może pisać na ten kanał. Nie wklejaj go do kodu ani do plików w repozytorium, tylko do sekretów w kroku 4.

### 3. Przygotuj listę kanałów

Jedna linijka na kanał: najpierw źródło, potem spacja i adres webhooka Discorda.

```text
r/NazwaSubreddita https://discord.com/api/webhooks/...
r/InnySubreddit https://discord.com/api/webhooks/...
redgifs/sissy-hypnosis https://discord.com/api/webhooks/...
redgifs/femboys https://discord.com/api/webhooks/...
```

- `r/nazwa` – subreddit. Zamiast tego możesz wkleić pełny adres, np. `https://www.reddit.com/r/nazwa`.
- `redgifs/nazwa` – nisza RedGifs, nazwa z adresu `https://www.redgifs.com/niches/<nazwa>`. Też możesz wkleić cały adres niszy.
- Puste linijki i linijki zaczynające się od `#` są pomijane.

Jeśli któraś linijka jest błędna, bot pominie tylko ją i wypisze w logu przebiegu, co jest nie tak. Pozostałe kanały działają dalej.

### 4. Dodaj sekret w repozytorium

Wejdź w **Settings → Secrets and variables → Actions → New repository secret**, nazwij sekret `CHANNELS` i wklej listę kanałów z kroku 3. To jedyny sekret, którego bot potrzebuje.

Konto Reddita nie jest potrzebne. Reddit blokuje adresy IP GitHub Actions, więc bot pobiera posty z publicznego archiwum Arctic Shift, które działa bez logowania.

Starsze sekrety `DISCORD_CHANNELS` i `REDGIFS_CHANNELS` (JSON) nadal działają. Kanały z nich są dołączane do listy z `CHANNELS`.

### 5. Uruchom bota ręcznie (test)

1. Wejdź w zakładkę **Actions**, po lewej wybierz **Reddit to Discord Feed** i kliknij **Run workflow → Run workflow**.
2. Po kilkudziesięciu sekundach przebieg powinien mieć zielony znaczek, a na pierwszym kanale powinny pojawić się posty.
3. W repozytorium powinien pojawić się commit **Update feed state** ze zmienionymi plikami `posted.json` i `rotation.json`.

Jeśli na kanale nic się nie pojawiło, kliknij przebieg i rozwiń krok **Run script**. Bot wypisuje tam, który kanał obsłużył i które linijki sekretu `CHANNELS` pominął. Jeśli przebieg ma czerwony X, rozwiń krok oznaczony X.

### 6. Ustaw automatyczne uruchamianie

Plik `.github/workflows/run.yml` ma wbudowany harmonogram (sekcja `schedule`), domyślnie co 10 minut. Wbudowany harmonogram GitHuba bywa zawodny: uruchomienia potrafią się spóźniać albo przepadać, szczególnie zaraz po zmianie harmonogramu. Pewniejsze jest wywoływanie bota z zewnątrz przez darmowy [cron-job.org](https://cron-job.org).

Przy prywatnym repozytorium licz się z limitem 2000 minut: uruchamianie co 10 minut daje około 4320 uruchomień miesięcznie i się nie mieści, co 30 minut daje około 1440 i się mieści.

#### Token GitHuba

1. Kliknij awatar w prawym górnym rogu GitHuba → **Settings** → na dole menu **Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Ustaw datę wygaśnięcia (**Expiration**). Po jej upływie bot przestanie się uruchamiać, dopóki nie wygenerujesz nowego tokenu i nie podmienisz go w cron-job.org.
3. W **Repository access** wybierz **Only select repositories** i wskaż swoje repozytorium.
4. W **Permissions → Repository permissions** ustaw **Actions** na **Read and write**. Nic więcej nie jest potrzebne.
5. Kliknij **Generate token** i skopiuj token (`github_pat_...`). GitHub pokazuje go tylko raz.

#### Zadanie w cron-job.org

1. Zaloguj się na [cron-job.org](https://cron-job.org) i kliknij **CREATE CRONJOB**.
2. **URL:** `https://api.github.com/repos/TWOJ_LOGIN/NAZWA_REPO/actions/workflows/run.yml/dispatches`
3. **Execution schedule:** wybierz częstotliwość, np. co 30 minut.
4. W zakładce **ADVANCED**:
   - **Request method:** `POST`
   - **Headers:**
     - `Authorization: Bearer github_pat_TWOJ_TOKEN`
     - `Accept: application/vnd.github+json`
     - `User-Agent: cron-job`
   - **Request body:** `{"ref":"main"}`
5. Kliknij **CREATE**. Po pierwszym wywołaniu historia zadania w cron-job.org powinna pokazać odpowiedź **204** (sukces), a w zakładce **Actions** na GitHubie pojawi się nowy przebieg.

Jeśli używasz cron-job.org, usuń z `.github/workflows/run.yml` sekcję `schedule` (dwie linijki: `schedule:` i `- cron: ...`), zostawiając `workflow_dispatch:`. Inaczej bot będzie uruchamiany przez oba harmonogramy naraz i zużyje dwa razy więcej minut.

## Rotacja kanałów

Kanały są obsługiwane po kolei, jeden na uruchomienie, w kolejności linijek z `CHANNELS` (kanały ze starszych sekretów JSON trafiają na koniec). Przy czterech kanałach i uruchamianiu co 30 minut każdy kanał dostaje nowe treści co 2 godziny.

Ustawienia w `bot.py`:
- `TOP_DAY_LIMIT` – ile najlepszych postów z czołówki sprawdzać w jednym przebiegu (domyślnie 5).
- `MAX_UPLOAD_BYTES` – filmy mniejsze niż ten limit są wysyłane jako plik MP4, większe jako link.
