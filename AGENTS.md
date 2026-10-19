# AGENTS.md

Instructions for coding agents (Claude Code, Codex, Cursor, etc.) working in this repo.

## What this is

`cua.py` is a single-file agent loop for OpenAI's `computer` tool (Responses API). The model gets a screenshot back after every batch of actions and keeps going until it stops returning `computer_call` items. The desktop is either:

- `Docker`: a local container (`cua-desktop`), started by `compose.yaml` and driven with `docker exec` + `xdotool`
- `Orgo`: a cloud computer created through `https://www.orgo.ai/api` and deleted when the run ends

With `--orgo-agent`, `run_orgo_agent` skips our loop entirely. It sends one streaming call to Orgo's OpenAI-compatible `POST /api/v1/chat/completions` with `computer_id`, and `orgo-agent` (GPT-6 Astra) runs the loop on Orgo's side. That mode only needs `ORGO_API_KEY`.

## Setup (run in order)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                 # skip if .env already exists; never overwrite it
docker compose up -d --build         # only for the local Docker backend
```

## API keys

Keys live in `.env` at the repo root, which `python-dotenv` loads. Exported shell variables also work.

| Variable | Required | Where the user gets it |
| --- | --- | --- |
| `OPENAI_API_KEY` | local and `--orgo` (not `--orgo-agent`) | https://platform.openai.com/api-keys |
| `ORGO_API_KEY` | `--orgo` and `--orgo-agent` | https://www.orgo.ai/settings/credentials |
| `OPENAI_MODEL` | no (default `gpt-6-astra`) | any model that supports the `computer` tool |
| `ORGO_WORKSPACE_ID` | no (defaults to the first workspace) | Orgo dashboard |
| `ORGO_BILLING_MODE` | no (default `plan`); set `hourly` on free Orgo accounts | Orgo billing docs |

Rules for agents:
- Never print, echo, log or commit key values. To check whether a key is set, print only the variable name, for example `grep -q '^OPENAI_API_KEY=sk' .env && echo set`.
- If a key is missing, ask the user to add it to `.env` themselves. Don't ask them to paste it into chat.
- `.env` is gitignored. Keep it that way.

## Run and verify

```bash
.venv/bin/python cua.py "Open Firefox, go to openai.com and tell me the top headline."
.venv/bin/python cua.py "Open a terminal and show me the system info" --orgo
.venv/bin/python cua.py "Open a terminal and show me the system info" --orgo-agent
```

- A successful run ends with a green `✅ Done` panel holding the model's answer.
- Screenshots from every step go to `runs/<timestamp>/NN.png`. Open the last one to confirm the result.
- Live view of the local desktop: http://localhost:6080/vnc.html?autoconnect=1&resize=scale
- Test the Docker action handlers with no API key (and no cost):
  ```bash
  .venv/bin/python -c "from openai.types.responses import computer_action as ca; from cua import Docker; d = Docker(); d.act(ca.Click(type='click', button='left', x=567, y=775)); open('/tmp/t.png', 'wb').write(d.screenshot())"
  ```
  This clicks the terminal icon in the dock.

## Conventions

- Keep `cua.py` small (about 275 lines today), single-file and dependency-light (`openai`, `requests`, `rich`, `python-dotenv`).
- A backend is any class with `watch_url`, `screenshot() -> bytes` (PNG), `act(action)` and `close()`.
- The default model is `gpt-6-astra`. OpenAI's own computer tool examples use `gpt-6.1-sol` (`--model gpt-6.1-sol`). Both use the same `computer` tool, so no code changes are needed.
- Send screenshots at full resolution with `"detail": "original"`. The model's coordinates are in screenshot pixels.
- If a `computer_call` has `pending_safety_checks`, a human must approve. Never auto-acknowledge them.
- Clean up: `docker compose down` for local runs. Orgo computers are deleted automatically unless you pass `--keep`.

## Docs

- Video walkthrough of this repo: https://www.youtube.com/watch?v=q3GFjaPOaMI
- OpenAI computer use: https://developers.openai.com/api/docs/guides/tools-computer-use
- OpenAI integration recipes: https://developers.openai.com/api/docs/guides/tools-computer-use-integration
- Orgo + OpenAI: https://docs.orgo.ai/guides/openai-computer-use
- Orgo hosted agent: https://docs.orgo.ai/api-reference/chat/completions
- Orgo API reference: https://docs.orgo.ai/api-reference/introduction (full index: https://docs.orgo.ai/llms.txt)
