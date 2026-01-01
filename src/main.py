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

# Add project root to sys.path to allow imports from src
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.context.context import ContextManager
from src.manager.service_manager import ServiceManager

app = typer.Typer()
console = Console()

CONFIG_PATH = Path("src/config.yaml")

def load_config():
    if not CONFIG_PATH.exists():
        console.print("[red]Config file not found! Please create src/config.yaml[/red]")
        raise typer.Exit(code=1)
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

@app.command()
def chat():
    """
    Start the Free LLM Coder assistant (Interactive Mode).
    """
    config = load_config()
    console.print(Panel.fit("Welcome to Free LLM Coder!", style="bold green"))

    # Initialize Managers
    try:
        ctx_mgr = ContextManager()
        svc_mgr = ServiceManager(config)
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
                break # Success, exit retry loop

            except Exception as e:
                console.print(f"[red]Error with {service_name}: {e}[/red]")
                svc_mgr.rotate_service()
                if svc_mgr.active_service_index >= len(svc_mgr.services_config):
                    console.print("[bold red]All services failed. Exiting.[/bold red]")
                    svc_mgr.close_all()
                    raise typer.Exit(code=1)
                continue

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
    driver.start_browser()
    driver.navigate()
    
    Prompt.ask("Press Enter after you have logged in and verified the session...")
    driver.close()
    console.print(f"[green]Session Saved for {service_name}.[/green]")

if __name__ == "__main__":
    app()
