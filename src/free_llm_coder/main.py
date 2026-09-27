import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.live import Live
from html import escape as html_escape
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
import yaml
import sys
import os
import time
from pathlib import Path

from .context.context import ContextManager
from .conversation import Conversation
from .manager.service_manager import ServiceManager
from .config_schema import build_default_config, ensure_services, validate_config
from .writer import parse_file_blocks, resolve_safe_path, diff_preview, write_file
from .executor import classify_command, run_command, TIMEOUT_EXIT_CODE
from .logging_setup import setup_logging, get_logger

log = get_logger("main")

app = typer.Typer()
console = Console()

# Global Config Path: ~/.free-llm-coder/config.yaml
APP_DIR = Path.home() / ".free-llm-coder"
CONFIG_PATH = APP_DIR / "config.yaml"

def load_config():
    if not CONFIG_PATH.exists():
        console.print(f"[yellow]Config not found at {CONFIG_PATH}[/yellow]")
        console.print("[blue]Running initial setup...[/blue]")
        _init_config()
        
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        console.print(f"[red]Config at {CONFIG_PATH} is empty or malformed.[/red]")
        raise typer.Exit(code=1)

    # Inject newly-supported drivers; persist only when something changed
    # (avoids rewriting the config file on every run).
    if ensure_services(config):
        console.print("[blue]Added newly-supported services to your configuration.[/blue]")
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(config, f)

    # Fail fast on a broken config, naming exactly what is wrong.
    errors = validate_config(config)
    if errors:
        console.print(f"[red]Invalid configuration in {CONFIG_PATH}:[/red]")
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(code=1)

    return config

def _init_config():
    """Create default config file in app dir."""
    APP_DIR.mkdir(parents=True, exist_ok=True)

    default_config = build_default_config()
    default_config['browser']['user_data_dir'] = str(APP_DIR / "user_data")

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(default_config, f)
    console.print(f"[green]Created default config at {CONFIG_PATH}[/green]")

@app.command()
def init():
    """Initialize configuration in home directory."""
    if CONFIG_PATH.exists():
        console.print(f"[yellow]Config already exists at {CONFIG_PATH}[/yellow]")
    else:
        _init_config()

@app.command()
def chat(
    target_dir: str = typer.Option(".", "--dir", "-d", help="Target project directory to analyze (code mode)"),
    service: str = typer.Option(None, "--service", "-s", help="Preferred LLM service (chatgpt, gemini, qwen)"),
    mode: str = typer.Option("code", "--mode", "-m",
                             help="'code': coding assistant with project context and file/command blocks; "
                                  "'chat': general-purpose Q&A, questions sent verbatim"),
    headless: bool = typer.Option(None, "--headless/--headful",
                                  help="Hide (or show) the automated browser windows; overrides browser.headless in config"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview file writes and commands without applying them"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug-level logs in the console")
):
    """
    Start the Free LLM Coder assistant (Interactive Mode).

    Interactive commands: 'exit'/'quit' to leave, '/new' fresh chat,
    '/switch [service]' answer later prompts with another service (the
    conversation is handed over automatically), '/status' service health,
    '/models' list the active service's models live, '/model <name>' pick
    one, '/modes' + '/mode <name>' for the service's mode/tool menu.
    """
    if mode not in ("code", "chat"):
        console.print(f"[red]Unknown mode '{mode}'. Use 'code' or 'chat'.[/red]")
        raise typer.Exit(code=1)

    setup_logging(APP_DIR / "logs", verbose=verbose)
    log.info("starting chat target=%s service=%s mode=%s dry_run=%s headless=%s",
             target_dir, service, mode, dry_run, headless)
    config = load_config()
    if headless is not None:
        config['browser']['headless'] = headless
    if config['browser'].get('headless'):
        console.print("[dim]Browser windows are hidden (headless). "
                      "If a service starts failing, retry with --headful -- "
                      "some sites challenge headless browsers.[/dim]")
    console.print(Panel.fit("Welcome to Free LLM Coder!", style="bold green"))
    console.print(f"[dim]Mode: {mode}[/dim]")
    if mode == "code":
        console.print(f"[dim]Target Directory: {Path(target_dir).resolve()}[/dim]")
    if service:
        console.print(f"[dim]Preferred Service: {service}[/dim]")

    # Initialize Managers
    try:
        ctx_mgr = ContextManager(root_path=target_dir, config=config, mode=mode)
        svc_mgr = ServiceManager(config, preferred_service=service)
    except Exception as e:
        console.print(f"[red]Initialization Error: {e}[/red]")
        raise typer.Exit(code=1)

    # Program-side transcript: lets a different service take over mid-
    # conversation (usage limit hit) without losing the dialogue so far.
    conversation = Conversation()

    # prompt_toolkit handles wide/composed characters (Hangul, CJK) correctly
    # -- plain input() leaves half-deleted glyphs behind on backspace -- and
    # gives arrow-key history for free.
    prompt_session = PromptSession()

    # Session-level service preference; changed at runtime with /switch.
    preferred_name = service

    console.print(f"[blue]Loaded {len(svc_mgr.services_config)} services.[/blue]")
    console.print("[yellow]Tip: Login to services via browser first if needed.[/yellow]")

    while True:
        try:
            # Keep the prompt message single-line: a newline inside it breaks
            # prompt_toolkit's redraw-origin tracking, leaving ghost glyphs
            # behind when wide (CJK) characters are deleted.
            # in_thread=True: Playwright's sync API keeps an asyncio loop
            # running on this thread once the browser starts, and prompt()
            # cannot start its own loop next to it.
            console.print()
            label = _prompt_label(svc_mgr, preferred_name)
            suffix = f"<ansibrightblack> ({html_escape(label)})</ansibrightblack>" if label else ""
            user_input = prompt_session.prompt(
                HTML(f"<ansicyan><b>You</b></ansicyan>{suffix}: "), in_thread=True,
            )
        except (EOFError, KeyboardInterrupt):
            # Ctrl+D / Ctrl+C at the prompt: leave cleanly.
            svc_mgr.close_all()
            break
        user_input = user_input.strip()
        command = user_input.lower()

        if command in ['exit', 'quit']:
            svc_mgr.close_all()
            break

        if not user_input:
            continue

        if command in ['/new', '/reset']:
            ctx_mgr.reset()
            conversation.reset()
            svc_mgr.new_chat_all()
            console.print("[green]Started a fresh chat.[/green]")
            continue

        if command in ['/status', '/services']:
            _print_service_status(svc_mgr)
            continue

        first_word = command.split()[0]
        if first_word in ['/models', '/modes', '/model', '/mode']:
            kind = 'model' if first_word in ['/models', '/model'] else 'mode'
            wanted = user_input.split(maxsplit=1)[1].strip() if len(user_input.split(maxsplit=1)) > 1 else None

            # Bring up the driver this session currently prefers.
            svc_mgr.reset_rotation()
            if preferred_name:
                svc_mgr.select_service(preferred_name)
            if svc_mgr.is_exhausted():
                console.print("[red]No service available right now.[/red]")
                continue
            try:
                driver, service_name = svc_mgr.get_active()
            except Exception as e:
                console.print(f"[red]Could not open a service: {e}[/red]")
                continue

            if not driver.supports_picker(kind):
                console.print(f"[yellow]'{service_name}' has no {kind}_menu selector configured. "
                              f"Add selectors.{kind}_menu to config.yaml to enable this.[/yellow]")
                continue

            if first_word in ['/models', '/modes'] and not wanted:
                items = driver.list_models() if kind == 'model' else driver.list_modes()
                if not items:
                    console.print(f"[yellow]Could not read the {kind} menu on '{service_name}' "
                                  f"(check selectors.{kind}_menu with flc doctor).[/yellow]")
                    continue
                console.print(f"[bold cyan]{service_name} {kind}s (live):[/bold cyan]")
                for it in items:
                    marker = " [green](current)[/green]" if it.get('selected') else ""
                    console.print(f"  - {it['name']}{marker}")
                console.print(f"[dim]Pick one with /{kind} <name>.[/dim]")
            elif wanted:
                clicked = driver.select_picker_item(wanted, kind)
                if clicked:
                    console.print(f"[green]'{service_name}' {kind} set to '{clicked}'.[/green]")
                else:
                    console.print(f"[red]No {kind} matching '{wanted}' on '{service_name}'. "
                                  f"See the live list with /{kind}s.[/red]")
            else:
                console.print(f"[yellow]Usage: /{kind} <name> (list with /{kind}s)[/yellow]")
            continue

        if command.split()[0] in ['/switch', '/use']:
            names = [s['name'] for s in svc_mgr.services_config]
            parts = command.split()
            if len(parts) > 1:
                target = parts[1]
                if target not in names:
                    console.print(f"[red]Unknown service '{target}'. Available: {', '.join(names)}[/red]")
                    continue
            else:
                # No argument: pick the next service after the current one.
                cur = min(svc_mgr.active_service_index, len(names) - 1)
                target = names[(cur + 1) % len(names)]
            preferred_name = target
            console.print(
                f"[green]Next prompts go to '{target}' first "
                "(the conversation is handed over automatically).[/green]"
            )
            continue

        if mode == "code":
            console.print("[dim]Analyzing project context...[/dim]")

        # Pick the highest-priority service whose circuit breaker allows it,
        # honoring a /switch preference when that service is available.
        svc_mgr.reset_rotation()
        if preferred_name and not svc_mgr.select_service(preferred_name):
            console.print(f"[dim]'{preferred_name}' is unavailable right now; using priority order.[/dim]")
        if svc_mgr.is_exhausted():
            console.print("[bold red]All services are on cooldown. Try again shortly.[/bold red]")
            continue

        # Retry loop for service rotation, with bounded attempts so a
        # persistently failing/empty service can never loop forever.
        service_name = "unknown"
        empty_retries = 0
        attempts = 0
        max_total_attempts = max(2, len(svc_mgr.services_config) * 2)

        while attempts < max_total_attempts:
            attempts += 1
            driver = None
            try:
                driver, service_name = svc_mgr.get_active()
                console.print(f"[dim]Sending to {service_name}...[/dim]")

                # Build the prompt for THIS service: it gets a handoff block
                # for any conversation turns it has not seen (e.g. it is
                # taking over after another service hit its limit), plus --
                # in code mode -- the project context it is missing.
                handoff = conversation.handoff_block(service_name)
                if handoff:
                    console.print(f"[dim]Handing the conversation over to {service_name}...[/dim]")
                full_prompt = handoff + ctx_mgr.build_prompt(user_input, service_name)

                driver.send_message(full_prompt)

                # Live-update a trimmed tail of the response as it streams in.
                with Live(console=console, refresh_per_second=4, transient=True) as live:
                    live.update(f"[dim]Waiting for {service_name}...[/dim]")

                    def _on_update(partial: str):
                        # Keep the preview shorter than the terminal: when the
                        # panel is taller than the screen, Live cannot redraw
                        # in place and stacks duplicate panels instead.
                        max_lines = max(5, console.size.height - 6)
                        lines = partial[-1500:].splitlines()
                        snippet = "\n".join(lines[-max_lines:])
                        live.update(Panel(snippet, title=f"{service_name} (streaming...)", border_style="dim"))

                    response = driver.wait_for_response(on_update=_on_update)

                limit_kind = driver.detect_limit()

                # No answer AND limit evidence: rotate and retry this prompt.
                if limit_kind and not response:
                    console.print(f"[red]Limit reached on {service_name}. Rotating...[/red]")
                    svc_mgr.rotate_service()
                    empty_retries = 0
                    if svc_mgr.is_exhausted():
                        console.print("[bold red]All services unavailable for this prompt.[/bold red]")
                        break
                    continue

                # Bounded retry on empty responses; rotate after repeated misses.
                if not response:
                    empty_retries += 1
                    console.print(f"[red]Empty response from {service_name} (attempt {empty_retries}).[/red]")
                    if empty_retries >= 2:
                        console.print("[yellow]Repeated empty responses. Rotating service...[/yellow]")
                        svc_mgr.rotate_service()
                        empty_retries = 0
                        if svc_mgr.is_exhausted():
                            console.print("[bold red]All services unavailable for this prompt.[/bold red]")
                            break
                    else:
                        time.sleep(2)
                    continue

                # A COMPLETED answer is never discarded. Strong limit evidence
                # only cools this service down so the NEXT prompt rotates; a
                # weak (generic-keyword) hint alongside a successful answer is
                # noise -- pages quote phrases like "rate limit" legitimately.
                if limit_kind in ("selector", "service_keyword"):
                    console.print(
                        f"[yellow]{service_name} reports a usage limit; keeping this answer, "
                        "switching services for later prompts.[/yellow]"
                    )
                    svc_mgr.mark_limited()
                else:
                    if limit_kind == "common_keyword":
                        log.info("[%s] ignoring weak limit hint: answer completed", service_name)
                    svc_mgr.mark_success()
                # Confirm delivery: context files count as sent, and this
                # service is now up to date on the whole conversation.
                ctx_mgr.commit(service_name)
                conversation.record("user", user_input)
                conversation.record("assistant", response)
                conversation.mark_seen(service_name)

                console.print(Panel(Markdown(response), title=f"Response from {service_name}", border_style="green"))

                # Code mode: apply structured file blocks, then offer to run
                # shell commands. Chat mode is pure Q&A.
                if mode == "code":
                    _apply_file_blocks(response, target_dir, dry_run)
                    _run_shell_commands(response, target_dir, dry_run)

                break # Success, exit retry loop

            except Exception as e:
                console.print(f"[red]Error with {service_name}: {e}[/red]")
                # Save what the page looked like -- essential for headless
                # runs where a login wall / bot challenge is invisible.
                if driver is not None:
                    snap = driver.save_debug_snapshot(APP_DIR / "logs" / "snapshots")
                    if snap:
                        console.print(f"[dim]Saved page snapshot: {snap}[/dim]")
                svc_mgr.rotate_service()
                if svc_mgr.is_exhausted():
                    console.print("[bold red]All services unavailable for this prompt.[/bold red]")
                    break
                continue
        else:
            console.print("[bold red]Max attempts reached for this prompt. Try again or check your sessions.[/bold red]")

def _prompt_label(svc_mgr: ServiceManager, preferred_name) -> str:
    """Label for the input prompt: the service that will answer next, plus
    its model when known (set via /model or read from a /models listing)."""
    name = None
    known = {s['name'] for s in svc_mgr.services_config}
    if preferred_name in known and svc_mgr._is_available(preferred_name):
        name = preferred_name
    else:
        for svc in svc_mgr.services_config:
            if svc_mgr._is_available(svc['name']):
                name = svc['name']
                break
    if not name:
        return ""
    driver = svc_mgr.drivers.get(name)
    model = getattr(driver, 'current_model', None) if driver else None
    return f"{name} · {model}" if model else name


def _print_service_status(svc_mgr: ServiceManager):
    """Show each service's priority and circuit-breaker state."""
    from rich.table import Table

    table = Table(title="Service status")
    table.add_column("service")
    table.add_column("priority")
    table.add_column("state")

    now = time.time()
    from .manager.service_manager import COOLDOWN_SECONDS
    for svc in svc_mgr.services_config:
        name = svc['name']
        breaker = svc_mgr.breakers.get(name, {})
        state = breaker.get('state', 'closed')
        if state == 'open':
            remaining = max(0, int(COOLDOWN_SECONDS - (now - breaker.get('opened_at', 0))))
            shown = f"[red]cooldown ({remaining}s left)[/red]"
        elif state == 'half-open':
            shown = "[yellow]half-open (one trial)[/yellow]"
        else:
            fails = breaker.get('failures', 0)
            shown = "[green]available[/green]" + (f" [dim]({fails} recent failures)[/dim]" if fails else "")
        table.add_row(name, str(svc.get('priority', '-')), shown)
    console.print(table)


def _extract_bash_blocks(text: str) -> list[str]:
    """Extract content from ```bash or ```shell blocks."""
    import re
    # Regex to find ```bash ... ``` blocks
    # Supports ```bash, ```shell, or just ```sh with flexible whitespace/newline
    matches = re.findall(r'```(?:\s*bash|\s*shell|\s*sh)\s*\n(.*?)```', text, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches]


def _apply_file_blocks(response: str, target_dir: str, dry_run: bool):
    """Preview and write any ```file:<path>``` blocks from the response.

    Each file is shown (a diff for an existing file, the content for a new
    one) and confirmed individually. Paths that escape the project directory
    are refused.
    """
    blocks = parse_file_blocks(response)
    if not blocks:
        return

    console.print(f"\n[bold cyan]Found {len(blocks)} file block(s).[/bold cyan]")
    apply_all = False
    for block in blocks:
        try:
            target = resolve_safe_path(target_dir, block.path)
        except ValueError as e:
            console.print(f"[red]Skipping unsafe path:[/red] {e}")
            continue

        exists = target.exists()
        action = "overwrite" if exists else "create"
        if exists:
            diff = diff_preview(target, block.content)
            if not diff.strip():
                console.print(f"[dim]{block.path}: identical to current file; skipping.[/dim]")
                continue
            console.print(Panel(diff, title=f"{action}: {block.path}", border_style="cyan"))
        else:
            preview = block.content
            if len(preview) > 2000:
                preview = preview[:2000] + "\n... (truncated)"
            console.print(Panel(preview, title=f"{action}: {block.path}", border_style="cyan"))

        if dry_run:
            console.print(f"[dim](dry-run) would {action} {block.path}[/dim]")
            continue

        if not apply_all:
            choice = Prompt.ask(
                "Apply this file? ([y]es / [n]o / [a]ll / [q]uit)",
                choices=["y", "n", "a", "q"], default="y",
            )
            if choice == "q":
                console.print("[dim]Stopped applying files.[/dim]")
                return
            if choice == "n":
                console.print(f"[dim]Skipped {block.path}.[/dim]")
                continue
            if choice == "a":
                apply_all = True

        try:
            write_file(target, block.content)
            console.print(f"[green]Wrote {block.path}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to write {block.path}: {e}[/red]")


def _run_shell_commands(response: str, target_dir: str, dry_run: bool):
    """Preview, risk-screen, and optionally run ```bash``` blocks one by one."""
    commands = _extract_bash_blocks(response)
    if not commands:
        return

    console.print(f"\n[bold yellow]Found {len(commands)} shell command block(s).[/bold yellow]")
    run_all = False
    for i, cmd in enumerate(commands, 1):
        blocked, block_reasons, warnings = classify_command(cmd)
        console.print(Panel(cmd, title=f"Command {i}", border_style="yellow"))

        if blocked:
            console.print(f"[bold red]Blocked -- {'; '.join(block_reasons)}. Not executed.[/bold red]")
            continue
        for w in warnings:
            console.print(f"[yellow]Warning: {w}.[/yellow]")

        if dry_run:
            console.print("[dim](dry-run) command not executed.[/dim]")
            continue

        if not run_all:
            choice = Prompt.ask(
                "Run this command? ([y]es / [n]o / [a]ll / [q]uit)",
                choices=["y", "n", "a", "q"], default="n",
            )
            if choice == "q":
                console.print("[dim]Stopped running commands.[/dim]")
                return
            if choice == "n":
                console.print(f"[dim]Skipped command {i}.[/dim]")
                continue
            if choice == "a":
                run_all = True

        console.print(f"[dim]Running:[/dim] {cmd.splitlines()[0]} ...")
        code = run_command(cmd, target_dir)
        if code == 0:
            console.print("[green]Command succeeded.[/green]")
        elif code == TIMEOUT_EXIT_CODE:
            console.print("[red]Command timed out and was killed.[/red]")
        else:
            console.print(f"[red]Command exited with code {code}.[/red]")

@app.command()
def doctor(
    service: str = typer.Option(None, "--service", "-s", help="Check only this service"),
    headless: bool = typer.Option(None, "--headless/--headful",
                                  help="Hide (or show) the automated browser windows; overrides browser.headless in config"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug-level logs in the console"),
):
    """Check each configured service's selectors against the live page.

    Opens every service (or just one with -s), loads its URL, and reports
    which configured selectors still match an element. This is the first
    thing to run when a site stops responding: it tells you exactly which
    selector in config.yaml needs updating.
    """
    from rich.table import Table

    setup_logging(APP_DIR / "logs", verbose=verbose)
    config = load_config()
    if headless is not None:
        config['browser']['headless'] = headless
    svc_mgr = ServiceManager(config)

    targets = [s for s in svc_mgr.services_config if not service or s['name'] == service]
    if not targets:
        console.print(f"[red]Service '{service}' not found in config.[/red]")
        raise typer.Exit(code=1)

    # response_container / generating_indicator legitimately match nothing on
    # a fresh page, so a miss there is informational rather than a failure.
    checked_keys = ("input_area", "submit_button", "response_container",
                    "new_chat_button", "generating_indicator",
                    "model_menu", "mode_menu")
    absent_ok = {"response_container", "generating_indicator", "new_chat_button",
                 "model_menu", "mode_menu"}

    table = Table(title="Selector health")
    table.add_column("service")
    table.add_column("selector")
    table.add_column("css")
    table.add_column("status")

    for cfg in targets:
        name = cfg['name']
        console.print(f"[dim]Checking {name} ({cfg['url']})...[/dim]")
        driver = None
        try:
            driver = svc_mgr._create_driver(cfg)
            driver.start_browser(svc_mgr._get_playwright())
            driver.navigate()
            time.sleep(3)  # let client-side rendering settle

            selectors = cfg.get('selectors', {}) or {}
            for key in checked_keys:
                sel = selectors.get(key)
                if not sel:
                    continue
                try:
                    found = driver.page.query_selector(sel) is not None
                except Exception:
                    found = False
                if found:
                    status = "[green]OK[/green]"
                elif key in absent_ok:
                    status = "[yellow]not found (may be normal on an empty chat)[/yellow]"
                else:
                    status = "[red]NOT FOUND -- update config.yaml[/red]"
                table.add_row(name, key, sel, status)
        except Exception as e:
            table.add_row(name, "-", "-", f"[red]failed to open: {e}[/red]")
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass

    svc_mgr.close_all()
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address for the API server"),
    port: int = typer.Option(8000, "--port", "-p", help="Port for the API server"),
    headless: bool = typer.Option(None, "--headless/--headful",
                                  help="Hide (or show) the automated browser windows; overrides browser.headless in config"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug-level logs in the console"),
):
    """Run an OpenAI-compatible API server backed by the web LLM services.

    Point Open WebUI / LM Studio / any OpenAI SDK at http://HOST:PORT/v1
    (any API key is accepted). Each configured service appears as a model;
    the "auto" model uses priority order with limit-based rotation.
    """
    setup_logging(APP_DIR / "logs", verbose=verbose)
    config = load_config()
    if headless is not None:
        config['browser']['headless'] = headless

    import uvicorn
    from .server import create_app

    console.print(Panel.fit(
        f"OpenAI-compatible API on http://{host}:{port}/v1\n"
        "Models: auto, " + ", ".join(s['name'] for s in config['services']),
        title="free-llm-coder serve", style="bold green",
    ))
    console.print("[yellow]Note: requests are processed one at a time (a real browser session per service).[/yellow]")
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")


@app.command()
def login(service_name: str = typer.Argument(None, help="Service to log in to; omit to walk through every service one by one")):
    """
    Open a browser to log in to a service (session is saved for reuse).

    With a service name, logs in to just that one. Without one, walks
    through every configured service step by step -- confirm, skip, or
    quit at each stop.
    """
    config = load_config()
    svc_mgr = ServiceManager(config)
    # Force headful: manual sign-in needs a visible window, even if the
    # service is configured headless.
    svc_mgr.headless = False

    if service_name:
        target_cfg = next((s for s in svc_mgr.services_config if s['name'] == service_name), None)
        if not target_cfg:
            console.print(f"[red]Service '{service_name}' not found in config.[/red]")
            raise typer.Exit(code=1)
        targets = [target_cfg]
    else:
        targets = svc_mgr.services_config
        console.print(Panel.fit(
            f"Walking through {len(targets)} services. At each stop: log in in the\n"
            "browser window, then press Enter here. Already logged in? Just press Enter.",
            title="Login wizard", style="bold green",
        ))

    total = len(targets)
    for i, cfg in enumerate(targets, 1):
        name = cfg['name']
        if total > 1:
            choice = Prompt.ask(
                f"[bold cyan][{i}/{total}][/bold cyan] Log in to '{name}'? ([y]es / [s]kip / [q]uit)",
                choices=["y", "s", "q"], default="y",
            )
            if choice == "s":
                console.print(f"[dim]Skipped {name}.[/dim]")
                continue
            if choice == "q":
                break

        console.print(f"Opening browser for {name}. Please log in manually.")
        driver = None
        try:
            driver = svc_mgr._create_driver(cfg)
            driver.headless = False
            driver.start_browser(svc_mgr._get_playwright())
            driver.navigate()
            if not driver.login_required():
                console.print(f"[dim]{name} does not look logged out -- verify in the window, then press Enter.[/dim]")
        except Exception as e:
            if "ProcessSingleton" in str(e):
                console.print(f"[yellow]{name}'s browser profile is in use by another process "
                              "(flc chat/serve running?). Close it and retry.[/yellow]")
            else:
                console.print(f"[red]Could not open {name}: {e}[/red]")
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass
            continue

        Prompt.ask("Press Enter after you have logged in and verified the session...")
        driver.close()
        console.print(f"[green]Session saved for {name}.[/green]")

    svc_mgr.close_all()


@app.command()
def status(
    service: str = typer.Option(None, "--service", "-s", help="Check only this service"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug-level logs in the console"),
):
    """Check every service's saved session: logged in, login needed, or broken.

    Opens each service headlessly (no windows) and reports whether the chat
    input is reachable. Complements `flc doctor` (selector detail) and the
    in-chat `/status` command (cooldown state of the running session).
    """
    from rich.table import Table

    setup_logging(APP_DIR / "logs", verbose=verbose)
    config = load_config()
    svc_mgr = ServiceManager(config)

    targets = [s for s in svc_mgr.services_config if not service or s['name'] == service]
    if not targets:
        console.print(f"[red]Service '{service}' not found in config.[/red]")
        raise typer.Exit(code=1)

    table = Table(title="Service sessions")
    table.add_column("service")
    table.add_column("priority")
    table.add_column("status")

    user_data_base = Path(svc_mgr.user_data_base)
    for cfg in targets:
        name = cfg['name']
        if not (user_data_base / name).exists():
            table.add_row(name, str(cfg.get('priority', '-')),
                          f"[yellow]never logged in -- run: flc login {name}[/yellow]")
            continue

        console.print(f"[dim]Checking {name}...[/dim]")
        driver = None
        try:
            driver = svc_mgr._create_driver(cfg)
            driver.headless = True  # quick, windowless check
            driver.start_browser(svc_mgr._get_playwright())
            driver.navigate()
            time.sleep(2)  # let client-side rendering settle

            if driver.login_required():
                state = f"[red]login needed -- run: flc login {name}[/red]"
            elif driver.manual_gate_required():
                state = f"[red]consent/terms page pending -- run: flc login {name} and confirm it[/red]"
            elif driver._challenge_visible():
                state = "[red]human verification pending -- open it headful and complete the check[/red]"
            else:
                # Visibility matters: some pages keep a hidden decoy textarea
                # (e.g. behind a blocking overlay), which mere existence
                # checks would wrongly report as usable.
                input_sel = (cfg.get('selectors') or {}).get('input_area')
                try:
                    el = input_sel and driver.page.query_selector(input_sel)
                    found = bool(el and el.is_visible())
                except Exception:
                    found = False
                if found:
                    state = "[green]ready (chat input visible)[/green]"
                else:
                    state = f"[yellow]input not visible -- check with: flc doctor -s {name}[/yellow]"
        except Exception as e:
            if "ProcessSingleton" in str(e):
                state = "[blue]in use by another process (flc chat/serve running)[/blue]"
            else:
                state = f"[red]failed to open: {str(e).splitlines()[0][:60]}[/red]"
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass

        table.add_row(name, str(cfg.get('priority', '-')), state)

    svc_mgr.close_all()
    console.print(table)

if __name__ == "__main__":
    app()
