"""Let GPT drive a real Linux desktop with OpenAI's computer tool.

    python cua.py "Open Firefox and search for OpenAI"          # our loop, local Docker desktop
    python cua.py "Open a terminal and run htop" --orgo         # our loop, Orgo cloud computer
    python cua.py "Open a terminal and run htop" --orgo-agent   # Orgo's hosted loop, Orgo cloud computer
"""

import argparse
import base64
import os
import subprocess
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import APIError, OpenAI
from rich.console import Console
from rich.panel import Panel

load_dotenv()
console = Console()

INSTRUCTIONS = """You control a Linux desktop through screenshots, mouse and keyboard.
A terminal and a web browser are installed; open them from the dock or taskbar.
Send keyboard shortcuts as one keypress (e.g. CTRL+L). Ask before anything irreversible."""

# The computer tool sends key names like "ENTER" or "CTRL"; xdotool wants X11 keysyms.
KEYS = {
    "ENTER": "Return", "RETURN": "Return", "ESC": "Escape", "ESCAPE": "Escape",
    "TAB": "Tab", "SPACE": "space", "BACKSPACE": "BackSpace", "DELETE": "Delete",
    "DEL": "Delete", "HOME": "Home", "END": "End", "PAGEUP": "Page_Up",
    "PAGEDOWN": "Page_Down", "UP": "Up", "DOWN": "Down", "LEFT": "Left",
    "RIGHT": "Right", "ARROWUP": "Up", "ARROWDOWN": "Down", "ARROWLEFT": "Left",
    "ARROWRIGHT": "Right", "CTRL": "ctrl", "CONTROL": "ctrl", "SHIFT": "shift",
    "ALT": "alt", "OPTION": "alt", "META": "super", "CMD": "super", "SUPER": "super",
}


def keysyms(keys) -> list[str]:
    # Lowercase single letters: to xdotool, "ctrl+L" means ctrl+shift+l.
    return [KEYS.get(k.upper(), k.lower() if len(k) == 1 else k) for k in keys or []]


class Docker:
    """The local desktop from compose.yaml, driven with `docker exec` + xdotool."""

    def __init__(self, container="cua-desktop"):
        self.container = container
        self.watch_url = "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"
        try:
            self._exec("xdotool", "getmouselocation")
        except (RuntimeError, FileNotFoundError):
            raise SystemExit("Desktop not running. Start it with: docker compose up -d --build")

    def _exec(self, *args) -> bytes:
        cmd = ["docker", "exec", "-e", "DISPLAY=:99", self.container, *map(str, args)]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f"{args[0]} failed: {result.stderr.decode().strip()}")
        return result.stdout

    def screenshot(self) -> bytes:
        return self._exec("import", "-window", "root", "png:-")

    def act(self, a):
        held = keysyms(getattr(a, "keys", None)) if a.type != "keypress" else []
        down = [arg for k in held for arg in ("keydown", k)]
        up = [arg for k in held for arg in ("keyup", k)]
        match a.type:
            case "click":
                button = {"left": 1, "wheel": 2, "right": 3, "back": 8, "forward": 9}[a.button]
                self._exec("xdotool", "mousemove", a.x, a.y, *down, "click", button, *up)
            case "double_click":
                self._exec("xdotool", "mousemove", a.x, a.y, *down, "click", "--repeat", 2, 1, *up)
            case "move":
                self._exec("xdotool", "mousemove", a.x, a.y)
            case "drag":
                steps = [arg for p in a.path[1:] for arg in ("mousemove", p.x, p.y)]
                start = a.path[0]
                self._exec("xdotool", "mousemove", start.x, start.y, "mousedown", 1, *steps, "mouseup", 1)
            case "scroll":
                clicks = []
                for delta, neg, pos in ((a.scroll_y, 4, 5), (a.scroll_x, 6, 7)):
                    if delta:
                        clicks += ["click", neg if delta < 0 else pos] * max(1, abs(round(delta / 100)))
                self._exec("xdotool", "mousemove", a.x, a.y, *clicks)
            case "keypress":
                self._exec("xdotool", "key", "+".join(keysyms(a.keys)))
            case "type":
                self._exec("xdotool", "type", "--delay", 10, "--", a.text)
            case "wait":
                time.sleep(2)
            case _:
                raise ValueError(f"Unsupported action: {a.type}")

    def close(self):
        pass  # The container keeps running so you can look around; `docker compose down` stops it.


class Orgo:
    """A fresh Orgo cloud computer, driven over its REST API (docs.orgo.ai)."""

    API = "https://www.orgo.ai/api"

    def __init__(self, keep=False, name=None):
        self.keep = keep
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {os.environ['ORGO_API_KEY']}"
        workspace = os.getenv("ORGO_WORKSPACE_ID") or self._call("GET", "/workspaces")["workspaces"][0]["id"]
        with console.status("Booting an Orgo computer…"):
            pc = self._call("POST", "/computers", json={
                "workspace_id": workspace, "name": name or f"cua-{int(time.time())}", "ram": 4, "cpu": 1,
                "billing_mode": os.getenv("ORGO_BILLING_MODE", "plan"),  # "hourly" for free-credit accounts
            })
            self.id = pc["id"]
            for _ in range(60):
                if pc["status"] in ("running", "error"):
                    break
                time.sleep(2)
                pc = self._call("GET", f"/computers/{self.id}")
            if pc["status"] != "running":
                self._call("DELETE", f"/computers/{self.id}")
                raise SystemExit(f"Orgo computer did not start (status: {pc['status']}).")
        self.watch_url = pc.get("connection_url") or "https://www.orgo.ai/workspaces"

    def _call(self, method, path, **kwargs):
        r = self.http.request(method, self.API + path, timeout=60, **kwargs)
        if not r.ok:
            raise SystemExit(f"Orgo API error {r.status_code} on {method} {path}: {r.text[:300]}")
        return r.json()

    def _do(self, action, **body):
        return self._call("POST", f"/computers/{self.id}/{action}", json=body)

    def screenshot(self) -> bytes:
        shot = self._call("GET", f"/computers/{self.id}/screenshot",
                          params={"response_format": "base64", "format": "png"})
        return base64.b64decode(shot["image"])

    def act(self, a):
        mods = keysyms(getattr(a, "keys", None)) if a.type != "keypress" else []
        match a.type:
            case "click":
                button = {"wheel": "middle"}.get(a.button, a.button)
                self._do("click", x=a.x, y=a.y, button=button, **({"modifiers": mods} if mods else {}))
            case "double_click":
                self._do("click", x=a.x, y=a.y, double=True)
            case "move":
                self._do("mouse-move", x=a.x, y=a.y)
            case "drag":
                start, end = a.path[0], a.path[-1]
                self._do("drag", start_x=start.x, start_y=start.y, end_x=end.x, end_y=end.y)
            case "scroll":
                if a.scroll_y:
                    self._do("scroll", x=a.x, y=a.y, direction="down" if a.scroll_y > 0 else "up",
                             amount=max(1, abs(round(a.scroll_y / 100))))
            case "keypress":
                self._do("key", key="+".join(keysyms(a.keys)))
            case "type":
                self._do("type", text=a.text)
            case "wait":
                time.sleep(2)
            case _:
                raise ValueError(f"Unsupported action: {a.type}")

    def close(self):
        if not self.keep:
            self._call("DELETE", f"/computers/{self.id}")
        console.print(f"[dim]Orgo computer {self.id} {'kept running' if self.keep else 'deleted'}.[/]")


def describe(a) -> str:
    fields = {k: v for k, v in a.model_dump().items() if k != "type" and v not in (None, [])}
    if "path" in fields:
        fields["path"] = " → ".join(f"({p['x']},{p['y']})" for p in fields["path"])
    return f"[bold cyan]{a.type}[/] " + " ".join(f"[dim]{k}=[/]{v}" for k, v in fields.items())


def run(task: str, computer, model: str, max_steps: int, fast: bool = False):
    client = OpenAI()
    shots = Path("runs") / time.strftime("%Y%m%d-%H%M%S")
    shots.mkdir(parents=True)
    request = dict(model=model, tools=[{"type": "computer"}])
    if fast:  # Fast mode: up to 2.5x faster, billed at 2x.
        request["service_tier"] = "fast"

    console.print(Panel(task, title=f"[bold]{model}{' ⚡ fast' if fast else ''}[/]", subtitle=f"watch live: {computer.watch_url}"))
    response = client.responses.create(**request, instructions=INSTRUCTIONS, input=task)

    for step in range(1, max_steps + 1):
        call = next((item for item in response.output if item.type == "computer_call"), None)
        if call is None:  # No more actions: the model is done.
            console.print(Panel(response.output_text or "(no answer)", title="✅ Done", border_style="green"))
            console.print(f"[dim]Screenshots saved to {shots}/[/]")
            return

        # OpenAI flags risky moments (e.g. suspected prompt injection). A human decides.
        checks = getattr(call, "pending_safety_checks", None) or []
        for check in checks:
            console.print(f"[bold yellow]⚠ Safety check:[/] {check.message}")
        if checks and console.input("Continue? [y/N] ").strip().lower() != "y":
            raise SystemExit("Stopped by user.")

        for action in call.actions or [call.action]:
            console.print(f"  [dim]{step:>2}[/] {describe(action)}")
            if action.type != "screenshot":  # We send a fresh screenshot after every batch anyway.
                computer.act(action)
        time.sleep(1)  # Let the UI settle before looking again.

        png = computer.screenshot()
        (shots / f"{step:02}.png").write_bytes(png)
        output = {
            "type": "computer_call_output",
            "call_id": call.call_id,
            "output": {
                "type": "computer_screenshot",
                "image_url": "data:image/png;base64," + base64.b64encode(png).decode(),
                "detail": "original",
            },
        }
        if checks:
            output["acknowledged_safety_checks"] = [c.model_dump(exclude_none=True) for c in checks]
        response = client.responses.create(**request, previous_response_id=response.id, input=[output])

    console.print(f"[yellow]Stopped after {max_steps} steps.[/]")


def run_orgo_agent(task: str, computer: Orgo):
    """Hand the whole loop to Orgo: one OpenAI-compatible call, GPT-6 Astra runs on Orgo's side."""
    client = OpenAI(base_url=f"{Orgo.API}/v1", api_key=os.environ["ORGO_API_KEY"])
    console.print(Panel(task, title="[bold]orgo-agent[/]", subtitle=f"watch live: {computer.watch_url}"))
    stream = client.chat.completions.create(
        model="orgo-agent", messages=[{"role": "user", "content": task}], stream=True,
        extra_body={"computer_id": computer.id},
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            console.print(chunk.choices[0].delta.content, end="", markup=False, highlight=False)
    console.print("\n[bold green]✅ Done[/]")


def main():
    parser = argparse.ArgumentParser(description="Let GPT use a computer.")
    parser.add_argument("task", nargs="?", default="Open Firefox, go to openai.com and tell me the top headline.")
    parser.add_argument("--orgo", action="store_true", help="use an Orgo cloud computer instead of Docker")
    parser.add_argument("--orgo-agent", action="store_true", help="let Orgo run the loop (no OpenAI key needed)")
    parser.add_argument("--keep", action="store_true", help="keep the Orgo computer running afterwards")
    parser.add_argument("--name", help="name for the new Orgo computer")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-6-astra"))
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--fast", action="store_true", help="OpenAI Fast mode: quicker, 2x the price")
    args = parser.parse_args()

    cloud = args.orgo or args.orgo_agent
    for key in ([] if args.orgo_agent else ["OPENAI_API_KEY"]) + (["ORGO_API_KEY"] if cloud else []):
        if not os.getenv(key):
            raise SystemExit(f"Missing {key}. Copy .env.example to .env and fill it in.")

    computer = Orgo(keep=args.keep, name=args.name) if cloud else Docker()
    try:
        if args.orgo_agent:
            run_orgo_agent(args.task, computer)
        else:
            run(args.task, computer, args.model, args.max_steps, args.fast)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
    except APIError as e:
        detail = e.body.get("message") if isinstance(e.body, dict) else None
        console.print(f"[bold red]OpenAI API error:[/] {detail or e.message}")
    finally:
        computer.close()


if __name__ == "__main__":
    main()
