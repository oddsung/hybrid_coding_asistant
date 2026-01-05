import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown
import yaml
import sys
import os
import time
from pathlib import Path

from .context.context import ContextManager
from .manager.service_manager import ServiceManager

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

    # Auto-inject or update Qwen config
    services = config.get('services', [])
    qwen_defaults = {
        'name': 'qwen',
        'url': 'https://chat.qwen.ai',
        'priority': 3,
        'selectors': {
            'input_area': '#chat-input',
            'submit_button': ".send-button",
            'response_container': ".qwen-markdown",
            'error_message': None
        }
    }
    
    # Grok defaults
    grok_defaults = {
        'name': 'grok',
        'url': 'https://grok.com',
        'priority': 4,
        'selectors': {
            'input_area': 'textarea',
            'submit_button': 'button.group.flex.flex-col.justify-center.rounded-full',
            'response_container': 'div[data-testid="messageGroup"], div.message-content',
            'error_message': None
        }
    }

    # DeepSeek defaults
    deepseek_defaults = {
        'name': 'deepseek',
        'url': 'https://chat.deepseek.com',
        'priority': 5,
        'selectors': {
            'input_area': 'textarea',
            'submit_button': 'div[role="button"]:has(path)', # Icon based
            'response_container': '.ds-markdown',
            'error_message': None
        }
    }

    modified = False
    for service_defaults in [qwen_defaults, grok_defaults, deepseek_defaults]:
        name = service_defaults['name']
        cfg = next((s for s in services if s['name'] == name), None)
        if not cfg:
            console.print(f"[blue]Adding {name.capitalize()} to configuration...[/blue]")
            services.append(service_defaults)
            modified = True
        else:
            # Proactively update selectors if they differ significantly (e.g. key missing or specific value changed)
            # For Grok, always update if it's the old data-testid selector
            if name == 'grok' and 'data-testid' in cfg['selectors'].get('input_area', ''):
                console.print(f"[blue]Updating {name.capitalize()} configuration for better stability...[/blue]")
                cfg['selectors'] = service_defaults['selectors']
                modified = True
            elif name == 'deepseek' and (not cfg['selectors'].get('submit_button') or 'svg' in cfg['selectors'].get('submit_button')):
                console.print(f"[blue]Updating {name.capitalize()} configuration for better stability...[/blue]")
                cfg['selectors'] = service_defaults['selectors']
                modified = True
            elif name == 'qwen' and cfg['selectors'].get('response_container') != ".qwen-markdown":
                console.print(f"[blue]Updating {name.capitalize()} configuration...[/blue]")
                cfg['selectors'] = service_defaults['selectors']
                modified = True

    if modified:
        config['services'] = services
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(config, f)
            
    return config

def _init_config():
    """Create default config file in app dir."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    
    # Default config content (copied from original source or defined here)
    default_config = {
        'browser': {
            'headless': False,
            'user_data_dir': str(APP_DIR / "user_data")
        },
        'services': [
            {
                'name': 'chatgpt',
                'url': 'https://chatgpt.com',
                'priority': 1,
                'selectors': {
                    'input_area': '#prompt-textarea',
                    'submit_button': "button[data-testid='send-button']",
                    'response_container': '.markdown',
                    'error_message': '.text-red-500'
                }
            },
            {
                'name': 'gemini',
                'url': 'https://gemini.google.com',
                'priority': 2,
                'selectors': {
                    'input_area': "div[contenteditable='true']",
                    'submit_button': "button[aria-label='Send message']",
                    'response_container': "model-response",
                    'limit_message': "You have reached your limit"
                }
            },
            {
                'name': 'qwen',
                'url': 'https://chat.qwen.ai',
                'priority': 3,
                'selectors': {
                    'input_area': '#chat-input',
                    'submit_button': ".send-button",
                    'response_container': ".assistant-message-content",
                    'error_message': None
                }
            } 
            # Deepseek omitted for brevity, can be added
        ],
        'context': {
            'max_files': 10,
            'max_chars': 10000,
            'ignore_patterns': ['*.pyc', '__pycache__', '.git', 'node_modules', 'venv', '.idea', '.vscode']
        }
    }
    
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
    service: str = typer.Option(None, "--service", "-s", help="Preferred LLM service (chatgpt, gemini, qwen)")
):
    """
    Start the Free LLM Coder assistant (Interactive Mode).
    """
    config = load_config()
    console.print(Panel.fit("Welcome to Free LLM Coder!", style="bold green"))
    console.print(f"[dim]Target Directory: {Path(target_dir).resolve()}[/dim]")
    if service:
        console.print(f"[dim]Preferred Service: {service}[/dim]")

    # Initialize Managers
    try:
        ctx_mgr = ContextManager(root_path=target_dir)
        svc_mgr = ServiceManager(config, preferred_service=service)
    except Exception as e:
        console.print(f"[red]Initialization Error: {e}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[blue]Loaded {len(svc_mgr.services_config)} services.[/blue]")
    console.print("[yellow]Tip: Login to services via browser first if needed.[/yellow]")

    while True:
        user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
        
        if user_input.lower() in ['exit', 'quit']:
            svc_mgr.close_all()
            break

        if not user_input.strip():
            continue

        # Build Prompt with Context
        console.print("[dim]Analyzing project context...[/dim]")
        full_prompt = ctx_mgr.build_prompt(user_input)
        
        # Retry loop for rotation
        while True:
            try:
                driver = svc_mgr.get_active_driver()
                service_name = svc_mgr.services_config[svc_mgr.active_service_index]['name']
                console.print(f"[dim]Sending to {service_name}...[/dim]")
                
                driver.send_message(full_prompt)
                
                with console.status(f"Waiting for {service_name}...", spinner="dots"):
                    response = driver.wait_for_response()
                
                # Check for limits or errors in response content (naive check)
                if driver.is_limit_reached():
                    console.print(f"[red]Limit reached on {service_name}. Rotating...[/red]")
                    svc_mgr.rotate_service()
                    continue # Retry with next service

                if not response:
                    console.print(f"[red]Empty response from {service_name}. Retrying...[/red]")
                    # Potential for infinite loop if all fail in specific ways; add counter if needed
                    time.sleep(2)
                    continue

                console.print(Panel(Markdown(response), title=f"Response from {service_name}", border_style="green"))
                
                # Auto-Execute Check
                commands = _extract_bash_blocks(response)
                if commands:
                    console.print("\n[bold yellow]Found Shell Commands:[/bold yellow]")
                    for i, cmd in enumerate(commands, 1):
                        console.print(Panel(cmd, title=f"Command {i}", border_style="yellow"))
                        
                    if typer.confirm("Do you want to execute these commands?"):
                        import subprocess
                        for cmd in commands:
                            console.print(f"[dim]Running:[/dim] {cmd.splitlines()[0]} ...")
                            try:
                                subprocess.run(cmd, shell=True, cwd=target_dir, check=True)
                            except subprocess.CalledProcessError as e:
                                console.print(f"[red]Command failed:[/red] {e}")
                        console.print("[bold green]Execution Complete![/bold green]")
                    else:
                        console.print("[dim]Skipped execution.[/dim]")

                break # Success, exit retry loop

            except Exception as e:
                console.print(f"[red]Error with {service_name}: {e}[/red]")
                svc_mgr.rotate_service()
                if svc_mgr.active_service_index >= len(svc_mgr.services_config):
                    console.print("[bold red]All services failed. Exiting.[/bold red]")
                    svc_mgr.close_all()
                    raise typer.Exit(code=1)
                continue

def _extract_bash_blocks(text: str) -> list[str]:
    """Extract content from ```bash or ```shell blocks."""
    import re
    # Regex to find ```bash ... ``` blocks
    # Supports ```bash, ```shell, or just ```sh with flexible whitespace/newline
    matches = re.findall(r'```(?:\s*bash|\s*shell|\s*sh)\s*\n(.*?)```', text, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches]

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
