# discord-pull

Bot wysyła filmy i obrazki z wybranych subredditów oraz nisz RedGifs na kanały Discorda. Działa w GitHub Actions, bez własnego serwera, a listę źródeł użytkownicy serwera ustawiają wiadomościami na kanale `#bot-config`.

Każde uruchomienie obsługuje 5 kolejnych źródeł, a następne bierze kolejne, w pętli. Z subredditów brane są najlepsze posty dnia (`top/?t=day`), z RedGifs filmy z niszy w kolejności `?order=hot`. Gdy nie ma nic nowego, bot wysyła jeden starszy, jeszcze niewysłany post. Wysłane ID są zapisywane w `posted.json`, więc nic się nie powtarza.

## Instrukcja krok po kroku

### 1. Utwórz własne repozytorium na GitHubie

1. Zaloguj się na [github.com](https://github.com), otwórz stronę tego repozytorium i kliknij zielony przycisk **Use this template → Create a new repository**. Dostaniesz własną kopię bez historii commitów. Historia wysłanych postów z oryginału zostanie zignorowana przy pierwszym uruchomieniu, więc Twój bot wyśle wszystko od nowa.
2. Wybierz widoczność repozytorium:
   - **Private** – nikt nie widzi kodu ani historii wysłanych postów. GitHub daje wtedy 2000 darmowych minut Actions miesięcznie, a każde uruchomienie liczy się jako co najmniej 1 minuta. Częstotliwość ustaw tak, żeby się w tym zmieścić (patrz krok 6).
   - **Public** – minuty Actions są bez limitu. Token bota w sekretach nadal pozostaje ukryty, ale każdy widzi kod i plik `posted.json`.
3. Wejdź w **Settings → Actions → General**:
   - w sekcji **Actions permissions** zaznacz **Allow all actions and reusable workflows**,
   - na dole strony w **Workflow permissions** zaznacz **Read and write permissions** i kliknij **Save**. Bot musi móc zapisywać w repozytorium pliki `posted.json` i `rotation.json`.

### 2. Utwórz bota Discorda i dodaj go na serwer

1. Wejdź na [Discord Developer Portal](https://discord.com/developers/applications), kliknij **New Application** i nadaj nazwę.
2. W zakładce **Bot**:
   - kliknij **Reset Token** i skopiuj token. Działa jak hasło do bota, więc wklej go tylko do sekretu w kroku 4,
   - w sekcji **Privileged Gateway Intents** włącz **Message Content Intent** i zapisz. Bez tego bot nie przeczyta poleceń z kanału konfiguracyjnego.
3. Skopiuj **Application ID** z zakładki **General Information** i otwórz w przeglądarce adres (podmień `APPLICATION_ID`):
   `https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot&permissions=117824`
   Wybierz serwer i zatwierdź. Bot dostanie uprawnienia: wyświetlanie kanałów, wysyłanie wiadomości, osadzanie linków, załączanie plików, czytanie historii i dodawanie reakcji.
4. Na kanałach z treściami dla dorosłych włącz w ustawieniach kanału **Kanał z ograniczeniem wiekowym**.
5. Żeby bot mógł oznaczać rolę z `ROLE_ID` w `bot.py`, w **Ustawieniach serwera → Role** włącz przy tej roli **Pozwól wszystkim @wzmiankować tę rolę** albo daj botowi uprawnienie **Wzmiankowanie @everyone, @here i wszystkich ról**.

### 3. Utwórz kanał konfiguracyjny `#bot-config`

Utwórz na serwerze kanał tekstowy o nazwie `bot-config`. Każda wiadomość na nim to lista źródeł, jedna linijka na źródło: najpierw źródło, potem kanał, na który mają trafiać posty.

```text
r/NazwaSubreddita #kanał-z-postami
redgifs/nazwa-niszy #inny-kanał
```

- `r/nazwa` – subreddit. Możesz też wkleić pełny adres, np. `https://www.reddit.com/r/nazwa`.
- `redgifs/nazwa` – nisza RedGifs, nazwa z adresu `https://www.redgifs.com/niches/<nazwa>`. Też możesz wkleić cały adres niszy.
- Kanał wskaż, wpisując `#` i wybierając go z podpowiedzi Discorda. Działa też sama nazwa albo ID kanału.

Przy następnym uruchomieniu bot doda pod wiadomością ✅, jeśli wszystko jest poprawne. Jeśli któraś linijka jest błędna, doda ❌ i odpowie, co pominął; poprawne linijki z tej wiadomości i tak działają. Po edycji wiadomości reakcja się zaktualizuje.

Żeby usunąć źródło, usuń wiadomość (albo linijkę w niej). Kto może dodawać źródła, ustalasz uprawnieniem **Wysyłanie wiadomości** na kanale `#bot-config`. Najlepiej, żeby nie było tam innej rozmowy, bo bot odpowie ❌ na każdą wiadomość, której nie zrozumie.

Jeśli kanał ma mieć inną nazwę, dodaj w **Settings → Secrets and variables → Actions → Variables** zmienną `CONFIG_CHANNEL` z tą nazwą albo ID kanału.

### 4. Dodaj token bota do sekretów

Wejdź w **Settings → Secrets and variables → Actions → New repository secret**, nazwij sekret `DISCORD_TOKEN` i wklej token z kroku 2. To jedyny sekret, którego bot potrzebuje. Nowe źródła dodaje się już tylko na Discordzie.

Konto Reddita nie jest potrzebne. Reddit blokuje adresy IP GitHub Actions, więc bot pobiera posty z publicznego archiwum Arctic Shift, które działa bez logowania.

### 5. Uruchom bota ręcznie (test)

1. Wejdź w zakładkę **Actions**, po lewej wybierz **Reddit to Discord Feed** i kliknij **Run workflow → Run workflow**.
2. Po kilkudziesięciu sekundach przebieg powinien mieć zielony znaczek, a na pierwszym kanale powinny pojawić się posty.
3. W repozytorium powinien pojawić się commit **Update feed state** ze zmienionymi plikami `posted.json` i `rotation.json`.

Jeśli na kanale nic się nie pojawiło, kliknij przebieg i rozwiń krok **Run script**. Bot wypisuje tam, ile źródeł znalazł na `#bot-config`, które obsłużył i dlaczego coś się nie udało (np. brak uprawnień albo wyłączony Message Content Intent). Jeśli przebieg ma czerwony X, rozwiń krok oznaczony X.

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

## Komenda /when (opcjonalnie)

`/when` pokazuje, za ile mniej więcej pojawią się nowe treści na kanale, na którym ją wpiszesz (albo na kanale wybranym w opcji `channel`). Na kanale bez źródeł pokazuje wszystkie. Odpowiedź widzi tylko osoba, która wpisała komendę, a czas odlicza się na żywo. Teksty komendy są po angielsku; same odliczania („in 20 minutes”) Discord wyświetla w języku aplikacji każdego użytkownika.

Bot działa tylko w czasie przebiegów GitHub Actions, więc na komendy odpowiada darmowy Cloudflare Worker, który czyta plik `schedule.json` zapisywany przez bota po każdym przebiegu.

1. **Token GitHuba dla Workera:** utwórz fine-grained token jak w kroku 6, ale w **Repository permissions** ustaw **Contents** na **Read-only**.
2. **Worker:** zaloguj się na [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages → Create → Create Worker** (szablon Hello World) → **Deploy** → **Edit code**. Wklej całą zawartość `worker/worker.js` i kliknij **Deploy**. Skopiuj adres Workera (`https://....workers.dev`).
3. **Zmienne Workera:** w **Settings → Variables and Secrets** dodaj:
   - `DISCORD_PUBLIC_KEY` (Text) – **Public Key** z zakładki **General Information** w Discord Developer Portal,
   - `GITHUB_REPO` (Text) – `TWOJ_LOGIN/NAZWA_REPO`,
   - `GITHUB_TOKEN` (Secret) – token z punktu 1.
4. **Adres dla Discorda:** w Discord Developer Portal → **General Information** wklej adres Workera w **Interactions Endpoint URL** i zapisz. Discord od razu sprawdza Workera; jeśli zapis się nie uda, sprawdź `DISCORD_PUBLIC_KEY`.
5. **Rejestracja komendy:** na GitHubie w **Actions** wybierz **Register command → Run workflow**. Po chwili `/when` pojawi się na serwerze (czasem trzeba przeładować Discorda: Ctrl+R).

Czasy są liczone z założenia, że bot uruchamia się co 10 minut. Jeśli używasz innej częstotliwości, dodaj w **Settings → Secrets and variables → Actions → Variables** zmienną `RUN_INTERVAL_MINUTES`, np. `30`.

## Rotacja kanałów

Źródła są obsługiwane po kolei, po 5 na uruchomienie, w kolejności wiadomości na `#bot-config` (od najstarszej) i linijek w nich. Przy 15 źródłach i uruchamianiu co 30 minut każde źródło dostaje nowe treści co 1,5 godziny.

Ustawienia w `bot.py`:
- `SOURCES_PER_RUN` – ile źródeł obsłużyć w jednym przebiegu (domyślnie 5). Większa wartość to dłuższe przebiegi i więcej zużytych minut Actions.
- `TOP_DAY_LIMIT` – ile najlepszych postów z czołówki sprawdzać w jednym przebiegu (domyślnie 5).
- `MAX_UPLOAD_BYTES` – filmy mniejsze niż ten limit są wysyłane jako plik MP4, większe jako link.
