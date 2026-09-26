# free-llm-coder

A CLI coding assistant that drives **free web LLM services** (ChatGPT, Gemini,
Qwen, Grok, DeepSeek, GLM, Kimi -- and any other chat site you describe in
config) through Playwright browser automation. It rotates across services
when one hits a rate limit, sends your project as context, applies the LLM's
structured file outputs locally, and prompts before running any shell
commands. It can also run as an **OpenAI-compatible API server** (`flc serve`)
so front-ends like Open WebUI or LM Studio can use these services as models.

> ⚠️ This automates third-party chat UIs. Be aware of each service's terms of
> service; selectors break when those sites update; you log in manually in a
> real Chrome instance whose session is persisted on disk.

---

## Install

```bash
pip install -e .
playwright install chrome     # downloads the browser Playwright drives
```

For development (running the test suite):

```bash
pip install -e ".[dev]"
pytest -q
```

## First-time setup

```bash
flc init                       # writes ~/.free-llm-coder/config.yaml
flc login chatgpt              # opens a real browser; sign in, press Enter
flc login gemini               # repeat for every service you want available
```

Logged-in sessions live under `~/.free-llm-coder/user_data/<service>/` so you
only log in once per service.

## Use

```bash
flc chat -d /path/to/your/project
```

Inside the chat loop:

- Type a question. The first turn sends the project's files (within
  `max_files` / `max_chars`); later turns send only files whose mtime changed.
- The reply streams live. When it's done the assistant's output is parsed:
  - ` ```file:relative/path ``` ` blocks → previewed (diff for an existing
    file, content for a new one) and applied after `[y]es/[n]o/[a]ll/[q]uit`.
    Paths outside the project directory are refused.
  - ` ```bash ``` ` blocks → risk-screened, warned (or blocked outright for
    destructive patterns like `rm -rf /`), and run one at a time on confirm.
- `/new` or `/reset` starts a fresh chat on the active service and resends
  the full project context next turn.
- `exit` or `quit` leaves.

### Useful flags

| Flag | Effect |
|---|---|
| `-d, --dir <path>` | Project directory the assistant should reason about (code mode) |
| `-m, --mode <code\|chat>` | `code` (default): coding assistant with project context and file/command blocks. `chat`: general-purpose Q&A -- questions are sent verbatim, no instructions at all |
| `-s, --service <name>` | Try this service first regardless of priority |
| `--dry-run` | Show what would be written / run, but don't apply anything |
| `-v, --verbose` | Show DEBUG-level logs in the console (always logged to file) |

```bash
flc chat -m chat              # use it like any general chatbot, with rotation
```

Logs are always written to `~/.free-llm-coder/logs/free-llm-coder.log` -- when
a selector breaks, this is the first place to look.

## Serve as an OpenAI-compatible API (Open WebUI / LM Studio)

Building chat UI/UX is out of scope for this project -- instead, expose the
web services through the OpenAI API shape and let existing front-ends do the
UI:

```bash
flc serve                      # http://127.0.0.1:8000/v1
flc serve --host 0.0.0.0 -p 9000
```

- `GET /v1/models` lists every configured service plus `auto` (priority order
  with limit-based rotation, same as the CLI).
- `POST /v1/chat/completions` supports `stream: true` (SSE) and non-streaming.
  Any API key is accepted.

Connect a front-end:

- **Open WebUI**: Admin Settings → Connections → OpenAI API → URL
  `http://127.0.0.1:8000/v1`, any key. Each service shows up as a model.
- **LM Studio (remote endpoint)** / any OpenAI SDK: base URL
  `http://127.0.0.1:8000/v1`, model `auto` or a service name.

Constraints to know:

- Requests are processed **one at a time** -- each exchange is a real browser
  session typing into a real chat page.
- The web chat holds the conversation history, so a service that is up to
  date gets only the newest user message. When a service takes over
  mid-conversation (usage limit rotation, or a server restart), it first
  receives a handoff transcript of the turns it missed, rebuilt from the
  front-end's message list -- the dialogue continues where it left off.
- The server tracks one conversation at a time; switching between front-end
  conversations just costs a redundant handoff, not correctness.
- Log in first (`flc login <service>`) with the same config the server uses.

## Selector health check

```bash
flc doctor                     # checks every service's selectors on the live page
flc doctor -s glm              # just one service
```

Opens each service and reports which configured selectors still match an
element -- run this first whenever a service stops responding, then fix the
reported selector in `config.yaml`.

## How it stays robust

- **Persistent Chrome session** per service so login survives across runs.
- **Streaming-start detection**: each turn waits for a brand-new response
  container to appear before reading text, so the previous turn's reply can
  never be re-shown.
- **Circuit breaker**: a service that fails 3 prompts in a row is opened for
  5 minutes; rotation transparently skips it and re-evaluates each new prompt.
- **Conversation handoff**: the program keeps its own transcript and tracks
  how much of it each service has seen. When a usage limit forces a switch
  mid-conversation, the new service receives the turns it missed (recent
  turns first, within a budget) and continues seamlessly -- in the CLI and
  in the API server alike.
- **Incremental context, per service**: only changed files are re-sent on
  follow-up turns, tracked separately per service, so a rotation target that
  never saw the project still gets the full context on its first prompt.
  Context only counts as delivered after a successful exchange, so failed
  attempts re-send it.
- **Path & command safety**: file writes go through Python (no shell, no
  heredoc) and are refused if they escape the project; shell commands are
  pattern-screened before being offered.

## Configuration

`~/.free-llm-coder/config.yaml` is the single source of truth for selectors,
URLs, priorities, and limit-detection keywords. When a site changes, edit
this file -- no code changes needed.

```yaml
browser:
  headless: false
  user_data_dir: /Users/you/.free-llm-coder/user_data
context:
  max_files: 25
  max_chars: 60000
  ignore_patterns: [".git", "__pycache__", "node_modules", "venv", "*.pyc"]
services:
  - name: chatgpt
    url: https://chatgpt.com
    priority: 1
    selectors:
      input_area: "#prompt-textarea"
      submit_button: "button[data-testid='send-button']"
      response_container: ".markdown"
      error_message: ".text-red-500"
      # new_chat_button: "<selector>"  # optional, falls back to URL reload
    limit_indicators:
      selectors: []
      keywords: ["you've reached our limit", "message limit"]
```

### When a service stops working

1. Open the chat in a browser, inspect the input box and send button.
2. Update the matching `selectors.*` in `config.yaml`.
3. If a usage-limit modal appears, capture some words from it into
   `limit_indicators.keywords` -- the rotator will pick those up next run.

## Project layout

```
src/free_llm_coder/
  main.py            # Typer CLI: chat / login / init / doctor / serve
  server.py          # OpenAI-compatible FastAPI server (flc serve)
  config_schema.py   # defaults + validation + missing-driver injection
  writer.py          # ```file:<path>``` parsing, path safety, diff preview
  executor.py        # shell-command risk classifier + timeout-guarded runner
  logging_setup.py   # console + file logger
  context/           # project scanning + initial/followup prompt building
  drivers/           # per-service Playwright drivers + generic config-only driver
  manager/           # ServiceManager with circuit breaker
tests/               # pytest suite (browser-free)
```

### Adding a new service (no code needed)

Any chat site can be added from `config.yaml` alone via the generic driver:

```yaml
services:
  - name: mistral
    url: https://chat.mistral.ai
    priority: 8
    driver: generic
    selectors:
      input_area: "textarea"
      submit_button: "button[type='submit']"
      response_container: ".assistant-message"
      # optional: element present only WHILE the reply is generating
      generating_indicator: ".stop-button"
```

Then `flc login mistral`, and verify the selectors with `flc doctor -s mistral`.
Only write a Python driver (subclass `BaseDriver`, register in
`drivers/__init__.py`) when a site needs special handling -- e.g. a custom
code-block widget like Qwen's Monaco editor.

> The bundled `glm` (chat.z.ai) and `kimi` (kimi.com) entries use the generic
> driver with best-effort selectors; these sites change often, so run
> `flc doctor -s glm` / `-s kimi` after logging in and adjust `config.yaml`
> if anything reports NOT FOUND.

## Limitations

- Web UIs change without notice; selectors will break. Logs and the config
  knobs are the maintenance surface.
- Bot detection may flag the automated browser session. The drivers use
  Playwright's persistent context with the obvious automation flags disabled,
  but no detection bypass is bulletproof.
- Sending each prompt is a real action in the service's UI; it counts against
  whatever free-tier limits that service enforces.
