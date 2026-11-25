# discord-pull

Bot wysyła filmy i obrazki z subredditów, profili użytkowników Reddita i nisz RedGifs na kanały Discorda. Działa za darmo, bez własnego serwera:

- **GitHub Actions** pobiera treści i wysyła je na Discorda,
- **Cloudflare Worker** uruchamia bota co 10 minut i obsługuje komendy `/add`, `/remove`, `/list`, `/post` i `/when`.

Każde uruchomienie obsługuje 5 kolejnych źródeł, a następne bierze kolejne, w pętli. Z subredditów brane są najlepsze posty dnia (`top/?t=day`), od użytkowników posty z ostatnich 24 godzin, z RedGifs filmy z niszy w kolejności `?order=hot`. Gdy nie ma nic nowego, bot wysyła jeden starszy, jeszcze niewysłany post. Wysłane posty są zapisywane w `posted.json`, więc nic się nie powtarza – także crossposty i ten sam plik wrzucony do kilku subredditów.

## Komendy

| Komenda | Co robi | Kto może |
|---|---|---|
| `/add source:r/nazwa [channel:#kanał]` | Dodaje źródło: `r/subreddit`, `u/użytkownik`, `redgifs/nisza` albo pełny link. Bez `channel` – bieżący kanał. | z uprawnieniem Zarządzanie kanałami |
| `/remove source:…` | Usuwa źródło (podpowiada listę). | j.w. |
| `/post source:…` | Wysyła posty z wybranego źródła od razu, bez czekania na kolejkę. | j.w. |
| `/list` | Pokazuje wszystkie źródła. | wszyscy |
| `/when [channel:#kanał]` | Za ile pojawią się nowe treści na kanale. | wszyscy |

Odpowiedzi widzi tylko osoba, która wpisała komendę. Kto może używać której komendy, zmienisz w **Ustawieniach serwera → Integracje → nazwa bota**, np. pozwalając na `/add` wybranej roli.

## Instrukcja krok po kroku

### 1. Utwórz własne repozytorium na GitHubie

1. Zaloguj się na [github.com](https://github.com), otwórz stronę tego repozytorium i kliknij **Use this template → Create a new repository**. Lista źródeł i historia wysłanych postów z oryginału zostaną zignorowane, więc zaczniesz od zera.
2. Wybierz widoczność repozytorium:
   - **Private** – nikt nie widzi kodu, listy źródeł ani historii. GitHub daje 2000 darmowych minut Actions miesięcznie, a jedno uruchomienie trwa 1–3 minuty. Przy uruchamianiu co 10 minut to się nie mieści – ustaw rzadsze uruchamianie (krok 5).
   - **Public** – minuty Actions są bez limitu. Tokeny w sekretach pozostają ukryte, ale każdy widzi kod, `sources.json` i `posted.json`.
3. W **Settings → Actions → General**:
   - w **Actions permissions** zaznacz **Allow all actions and reusable workflows**,
   - w **Workflow permissions** zaznacz **Read and write permissions** i kliknij **Save**. Bot zapisuje w repozytorium pliki stanu.

### 2. Utwórz bota Discorda

1. Na [Discord Developer Portal](https://discord.com/developers/applications) kliknij **New Application** i nadaj nazwę.
2. W zakładce **Bot** kliknij **Reset Token** i skopiuj token. Działa jak hasło do bota.
3. Na GitHubie w **Settings → Secrets and variables → Actions → New repository secret** dodaj sekret `DISCORD_TOKEN` z tym tokenem.
4. Skopiuj **Application ID** z zakładki **General Information** i otwórz adres (podmień `APPLICATION_ID`):
   `https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot+applications.commands&permissions=52224`
   Wybierz serwer i zatwierdź. Bot dostanie uprawnienia: wyświetlanie kanałów, wysyłanie wiadomości, osadzanie linków i załączanie plików.
5. Na kanałach z treściami dla dorosłych włącz w ustawieniach kanału **Kanał z ograniczeniem wiekowym**.
6. Żeby bot mógł oznaczać rolę z `ROLE_ID` w `bot.py`, włącz przy tej roli **Pozwól wszystkim @wzmiankować tę rolę** albo daj botowi uprawnienie **Wzmiankowanie @everyone, @here i wszystkich ról**.

### 3. Utwórz token GitHuba dla Workera

1. Kliknij awatar na GitHubie → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Ustaw **Expiration**. Po wygaśnięciu bot przestanie się uruchamiać, dopóki nie podmienisz tokenu w Workerze.
3. W **Repository access** wybierz **Only select repositories** i wskaż swoje repozytorium.
4. W **Repository permissions** ustaw **Actions** na **Read and write** oraz **Contents** na **Read and write**.
5. Kliknij **Generate token** i skopiuj go (`github_pat_...`).

### 4. Utwórz Cloudflare Worker

1. Zaloguj się na [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages → Create → Create Worker** (szablon Hello World) → **Deploy** → **Edit code**.
2. Wklej całą zawartość `worker/worker.js` i kliknij **Deploy**. Skopiuj adres Workera (`https://....workers.dev`).
3. W **Settings → Variables and Secrets** dodaj:
   - `DISCORD_PUBLIC_KEY` (Text) – **Public Key** z zakładki **General Information** w Discord Developer Portal,
   - `GITHUB_REPO` (Text) – `TWOJ_LOGIN/NAZWA_REPO`,
   - `GITHUB_TOKEN` (Secret) – token z kroku 3.
4. W Discord Developer Portal → **General Information** wklej adres Workera w **Interactions Endpoint URL** i zapisz. Discord od razu sprawdza Workera; jeśli zapis się nie uda, sprawdź `DISCORD_PUBLIC_KEY`.
5. Na GitHubie w **Actions** wybierz **Register command → Run workflow**. Po chwili komendy pojawią się na serwerze (czasem trzeba przeładować Discorda: Ctrl+R).

### 5. Ustaw harmonogram

W Workerze otwórz **Settings → Trigger Events → Add → Cron Triggers** i wpisz `*/10 * * * *` (co 10 minut). Inne przykłady: `*/30 * * * *` co 30 minut, `0 * * * *` co godzinę. Worker przekazuje częstotliwość botowi, więc `/when` liczy czasy właściwie (dla wpisów w postaci `*/N`; w innych przypadkach zakłada 10 minut).

Przy prywatnym repozytorium licz się z limitem 2000 minut: przy 5 źródłach na przebieg najbezpieczniej co 30–60 minut.

### 6. Dodaj źródła i sprawdź

1. Na kanale, na który mają trafiać posty, wpisz `/add source:r/NazwaSubreddita`. Worker sprawdzi, czy źródło istnieje, i doda je do `sources.json`.
2. Wpisz `/post source:r/NazwaSubreddita` – po 1–2 minutach na kanale pojawią się posty.
3. Jeśli nic się nie pojawiło, na GitHubie w **Actions** otwórz ostatni przebieg i rozwiń krok **Run script** – bot wypisuje tam, co obsłużył i dlaczego coś się nie udało (np. brak uprawnień na kanale).

Konto Reddita nie jest potrzebne. Reddit blokuje adresy IP GitHub Actions, więc bot pobiera posty z publicznego archiwum Arctic Shift, które działa bez logowania. Nowe posty trafiają do archiwum z opóźnieniem od kilku minut do około godziny.

## Ustawienia w `bot.py`

- `SOURCES_PER_RUN` – ile źródeł obsłużyć w jednym przebiegu (domyślnie 5). Większa wartość to dłuższe przebiegi i więcej zużytych minut Actions.
- `TOP_DAY_LIMIT` – ile najlepszych postów z dnia sprawdzać w jednym przebiegu (domyślnie 5).
- `ROLE_ID` – rola oznaczana w każdym poście; pusty tekst wyłącza oznaczanie.
- `MAX_UPLOAD_BYTES` – filmy mniejsze niż ten limit są wysyłane jako plik MP4, większe jako link.

Kolejność kolejki to kolejność dodawania źródeł. Przy 15 źródłach i uruchamianiu co 10 minut każde źródło dostaje nowe treści co 30 minut.
