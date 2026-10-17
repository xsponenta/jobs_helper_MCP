# Jobs Helper MCP

Пошук вакансій на DOU, Djinni і Happy Monday, адаптація CV та відгук від імені кандидата. Система складається з двох агентів і власного MCP-сервера. План — у `PLAN.md`, огляд і схема архітектури — у `docs/OVERVIEW.md`.

## Встановлення
```bash
uv sync
uv run playwright install chromium
```

## Дані кандидата і політика відправки
- **`candidate.yaml`** у корені: скопіюй `01_recon/candidate.example.yaml` і заповни. Файл не комітиться.
- **Політика відправки** окремо для кожного сайту зберігається в `settings.yaml`:
  ```bash
  uv run python -m jobs_helper policy show
  uv run python -m jobs_helper policy set djinni confirm   # manual | confirm | auto
  ```

## Пошук із командного рядка
```bash
uv run python -m jobs_helper search "python django" --remote --min-salary 3000
uv run python -m jobs_helper get "djinni:853661-middle-python-developer"
uv run python -m jobs_helper hm-sync --budget 100        # оновити індекс Happy Monday
uv run python -m jobs_helper snapshot --query python --query react --name my_snapshot
```

## Підключення до Claude Code / Claude Desktop
**Claude Code:** у корені є `.mcp.json`. Запусти `claude` у папці репозиторію й підтверди сервери `jobs` і `linkedin`.

**Claude Desktop:** додай у `claude_desktop_config.json` (шлях до репозиторію — свій):
```json
{
  "mcpServers": {
    "jobs": {"command": "uv", "args": ["run", "--directory", "/ABS/PATH/jobs_helper_MCP", "python", "-m", "jobs_helper.mcp_jobs.server"],
             "env": {"AGENT_ROLE": "manual", "JOBS_APPLY_BACKEND": "browser"}},
    "linkedin": {"command": "uv", "args": ["run", "--directory", "/ABS/PATH/jobs_helper_MCP", "python", "-m", "jobs_helper.mcp_linkedin.server"],
                 "env": {"AGENT_ROLE": "manual"}}
  }
}
```

## Логін на сайтах і в LinkedIn
```bash
uv run python -m jobs_helper login djinni        # також dou, happymonday
uv run python -m jobs_helper login linkedin      # OAuth
```
- **Сайти вакансій.** Відкривається звичайне вікно браузера: кандидат входить сам, сесія зберігається в `.browser_profiles/<сайт>/` (не комітиться). Пароль програма не бачить. На Djinni можна відгукнутися й без логіну, якщо в `candidate.yaml` є LinkedIn.
- **LinkedIn**, підготовка один раз:
  1. Створи застосунок на https://www.linkedin.com/developers/apps (потрібна LinkedIn Page).
  2. Додай продукти «Sign In with LinkedIn using OpenID Connect» і «Share on LinkedIn».
  3. Redirect URL: `http://localhost:8765/callback`.
  4. Client ID і Secret запиши в `.secrets/linkedin.env` (`LINKEDIN_CLIENT_ID=…`, `LINKEDIN_CLIENT_SECRET=…`).

## MCP-сервер `jobs`
- **Інструменти:** `search_jobs`, `get_job`, `apply`.
- **Ресурси:** `jobs://applications` (трекер заявок), `jobs://policy` (політика відправки).
- **Промпт:** `find_and_apply`.

`apply` з `JOBS_APPLY_BACKEND=browser` відкриває форму в браузері кандидата: на Djinni, DOU, Happy Monday або на сайті компанії / ATS (перевірено на Lever, PeopleForce, Greenhouse, Ashby, BambooHR). Далі він діє за політикою сайту. Без нього використовується `mock`.
```bash
uv run python -m jobs_helper.mcp_jobs.server                                   # stdio
npx @modelcontextprotocol/inspector uv run python -m jobs_helper.mcp_jobs.server
uv run python scripts/smoke_mcp_jobs.py                                        # перевірка через MCP-клієнт
```

Змінні оточення:

| Змінна | Що задає |
|---|---|
| `AGENT_ROLE` | хто викликає сервер; `apply` дозволений тільки ролям `reviewer` і `single_agent` |
| `JOBS_SNAPSHOT` | шлях до знімка вакансій або `latest`; без неї пошук іде по живих сайтах |
| `JOBS_APPLY_BACKEND` | бекенд відгуку: `mock` (тести, оцінка) або `browser` (справжні форми) |
| `JOBS_HEADLESS=1` | браузер без вікна (лише для тестів) |
| `JOBS_HUMAN_TIMEOUT` | скільки секунд чекати рішення людини для `manual` / `confirm` (за замовчуванням 900) |
| `JOBS_DRY_RUN=1` | заповнювати форми, але нічого не відправляти |
| `JOBS_WORKSPACE` | куди писати трекер заявок і документи (за замовчуванням `workspace/`) |

## MCP-сервер `linkedin`
Інструменти:
- `get_profile` — ім'я, email і фото з OpenID;
- `publish_post` — пост за політикою: `manual` відкриває вікно публікації LinkedIn з уже вставленим текстом, `confirm` показує попередній перегляд із кнопками, `auto` публікує через API;
- `save_profile_update` — готовий текст для профілю в `profile_update.md`. API для редагування профілю LinkedIn не існує.

```bash
uv run python -m jobs_helper.mcp_linkedin.server
```

## Агенти
```bash
# два агенти (Скаут → Перевіряльник), на знімку вакансій, без справжньої відправки
uv run python -m jobs_helper.agents.run --candidate data/candidates/olena \
    --snapshot data/snapshots/2026-10-17_eval.json --dry-run --today 2026-10-17 \
    "Знайди remote Python backend вакансію і відгукнись на одну найкращу"
# той самий запит одним агентом
uv run python -m jobs_helper.agents.run --mode single ...
# трейс у HTML
uv run python scripts/trace_view.py runs/<run>/trace.jsonl
```
Агенти працюють через Claude Agent SDK і логін Claude Code, API-ключ не потрібен.

## Оцінка
```bash
uv run python -m evaluation.run_eval                 # 5 кейсів × {multi, single}
uv run python -m evaluation.run_eval --repeat 3      # кілька спроб на кейс
```
Звіт пишеться в `runs/eval_<час>/report.md`. Підсумки — у `RESULTS.md`.

## Тести
```bash
uv run pytest
```

## Структура
```
01_recon/            етап 1: розвідка сайтів (ноутбуки, RECON.md)
jobs_helper/
  sources/           DOU (RSS), Djinni (RSS + JSON-LD), Happy Monday (sitemap + HTML)
  snapshot.py        знімки вакансій для оцінки
  mcp_jobs/          MCP-сервер `jobs` (server.py) і його логіка (service.py)
  apply/             заповнення форм: planner, mock- і browser-бекенд, PDF із CV
  linkedin/          OAuth і логіка LinkedIn
  mcp_linkedin/      MCP-сервер `linkedin`
  agents/            рантайм, промпти, оркестратор, трейсинг
  policy.py          settings.yaml / candidate.yaml
  tracker.py         трекер заявок
data/snapshots/      заморожені вакансії
data/candidates/     вигадані тестові кандидати (cv.md + candidate.yaml)
evaluation/          5 тестових кейсів, грейдери, прогін і звіт
tests/               тести та фікстури
```
