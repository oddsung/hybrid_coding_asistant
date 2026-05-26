import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.live import Live
import yaml
import sys
import os
import time
from pathlib import Path

from .context.context import ContextManager
from .manager.service_manager import ServiceManager
from .config_schema import build_default_config, ensure_services, validate_config
from .writer import parse_file_blocks, resolve_safe_path, diff_preview, write_file
from .executor import classify_command, run_command
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
    target_dir: str = typer.Option(".", "--dir", "-d", help="Target project directory to analyze"),
    service: str = typer.Option(None, "--service", "-s", help="Preferred LLM service (chatgpt, gemini, qwen)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview file writes and commands without applying them"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug-level logs in the console")
):
    """
    Start the Free LLM Coder assistant (Interactive Mode).

    Interactive commands: type 'exit'/'quit' to leave, '/new' to start a fresh chat.
    """
    setup_logging(APP_DIR / "logs", verbose=verbose)
    log.info("starting chat target=%s service=%s dry_run=%s", target_dir, service, dry_run)
    config = load_config()
    console.print(Panel.fit("Welcome to Free LLM Coder!", style="bold green"))
    console.print(f"[dim]Target Directory: {Path(target_dir).resolve()}[/dim]")
    if service:
        console.print(f"[dim]Preferred Service: {service}[/dim]")

    # Initialize Managers
    try:
        ctx_mgr = ContextManager(root_path=target_dir, config=config)
        svc_mgr = ServiceManager(config, preferred_service=service)
    except Exception as e:
        console.print(f"[red]Initialization Error: {e}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[blue]Loaded {len(svc_mgr.services_config)} services.[/blue]")
    console.print("[yellow]Tip: Login to services via browser first if needed.[/yellow]")

    while True:
        user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
        command = user_input.strip().lower()

        if command in ['exit', 'quit']:
            svc_mgr.close_all()
            break

        if not user_input.strip():
            continue

        if command in ['/new', '/reset']:
            ctx_mgr.reset()
            svc_mgr.new_chat_all()
            console.print("[green]Started a fresh chat. Full project context will be sent on the next message.[/green]")
            continue

        # Build Prompt with Context
        console.print("[dim]Analyzing project context...[/dim]")
        full_prompt = ctx_mgr.build_prompt(user_input)

        # Pick the highest-priority service whose circuit breaker allows it.
        svc_mgr.reset_rotation()
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
            try:
                driver, service_name = svc_mgr.get_active()
                console.print(f"[dim]Sending to {service_name}...[/dim]")

                driver.send_message(full_prompt)

                # Live-update a trimmed tail of the response as it streams in.
                with Live(console=console, refresh_per_second=4, transient=True) as live:
                    live.update(f"[dim]Waiting for {service_name}...[/dim]")

                    def _on_update(partial: str):
                        snippet = partial[-1500:]
                        live.update(Panel(snippet, title=f"{service_name} (streaming...)", border_style="dim"))

                    response = driver.wait_for_response(on_update=_on_update)

                # Rotate when the service reports a usage limit.
                if driver.is_limit_reached():
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

                svc_mgr.mark_success()
                console.print(Panel(Markdown(response), title=f"Response from {service_name}", border_style="green"))

                # Apply structured file blocks, then offer to run shell commands.
                _apply_file_blocks(response, target_dir, dry_run)
                _run_shell_commands(response, target_dir, dry_run)

                break # Success, exit retry loop

            except Exception as e:
                console.print(f"[red]Error with {service_name}: {e}[/red]")
                svc_mgr.rotate_service()
                if svc_mgr.is_exhausted():
                    console.print("[bold red]All services unavailable for this prompt.[/bold red]")
                    break
                continue
        else:
            console.print("[bold red]Max attempts reached for this prompt. Try again or check your sessions.[/bold red]")

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
        else:
            console.print(f"[red]Command exited with code {code}.[/red]")

@app.command()
def login(service_name: str):
    """
    Open a browser to login to a specific service.
    Keeps the browser open until user presses Enter in CLI.
    """
    config = load_config()
    svc_mgr = ServiceManager(config)
    
    # Find specific config
    target_cfg = next((s for s in config['services'] if s['name'] == service_name), None)
    if not target_cfg:
        console.print(f"[red]Service '{service_name}' not found in config.[/red]")
        return

    console.print(f"Opening browser for {service_name}. Please log in manually.")
    # Force headful for login
    svc_mgr.headless = False
    driver = svc_mgr._create_driver(target_cfg)
    driver.start_browser(svc_mgr._get_playwright())
    driver.navigate()
    
    Prompt.ask("Press Enter after you have logged in and verified the session...")
    driver.close()
    console.print(f"[green]Session Saved for {service_name}.[/green]")

if __name__ == "__main__":
    app()
