# free-llm-coder

A CLI coding assistant that drives **free web LLM services** (ChatGPT, Gemini,
Qwen, Grok, DeepSeek) through Playwright browser automation. It rotates across
services when one hits a rate limit, sends your project as context, applies
the LLM's structured file outputs locally, and prompts before running any
shell commands.

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
fc init                       # writes ~/.free-llm-coder/config.yaml
fc login chatgpt              # opens a real browser; sign in, press Enter
fc login gemini               # repeat for every service you want available
```

Logged-in sessions live under `~/.free-llm-coder/user_data/<service>/` so you
only log in once per service.

## Use

```bash
fc chat -d /path/to/your/project
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
| `-d, --dir <path>` | Project directory the assistant should reason about |
| `-s, --service <name>` | Try this service first regardless of priority |
| `--dry-run` | Show what would be written / run, but don't apply anything |
| `-v, --verbose` | Show DEBUG-level logs in the console (always logged to file) |

Logs are always written to `~/.free-llm-coder/logs/free-llm-coder.log` -- when
a selector breaks, this is the first place to look.

## How it stays robust

- **Persistent Chrome session** per service so login survives across runs.
- **Streaming-start detection**: each turn waits for a brand-new response
  container to appear before reading text, so the previous turn's reply can
  never be re-shown.
- **Circuit breaker**: a service that fails 3 prompts in a row is opened for
  5 minutes; rotation transparently skips it and re-evaluates each new prompt.
- **Incremental context**: only changed files are re-sent on follow-up turns.
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
  max_files: 10
  max_chars: 10000
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
  main.py            # Typer CLI: chat / login / init
  config_schema.py   # defaults + validation + missing-driver injection
  writer.py          # ```file:<path>``` parsing, path safety, diff preview
  executor.py        # shell-command risk classifier
  logging_setup.py   # console + file logger
  context/           # project scanning + initial/followup prompt building
  drivers/           # per-service Playwright drivers + DRIVER_REGISTRY
  manager/           # ServiceManager with circuit breaker
tests/               # pytest suite (browser-free)
```

Adding a new service: implement a driver subclassing `BaseDriver`, register
it in `drivers/__init__.py`, add its defaults to `config_schema.DEFAULT_SERVICES`.

## Limitations

- Web UIs change without notice; selectors will break. Logs and the config
  knobs are the maintenance surface.
- Bot detection may flag the automated browser session. The drivers use
  Playwright's persistent context with the obvious automation flags disabled,
  but no detection bypass is bulletproof.
- Sending each prompt is a real action in the service's UI; it counts against
  whatever free-tier limits that service enforces.
