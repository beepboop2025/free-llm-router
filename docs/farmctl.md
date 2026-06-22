# Consumer: farmctl (`ai` command)

[`farmctl`](https://github.com/beepboop2025/farmctl) (local `~/farmctl`) is a CLI for a DeviceHub/OpenSTF Android device farm. Its `ai` subcommand uses this router as a **zero-marginal-cost screen → structured-intel** layer: it captures a device's on-screen text and asks an LLM to extract from it.

## Integration

- Calls `FreeLLMRouter().chat_completion(messages, tier=..., max_tokens=...)` (async; run via `asyncio.run`).
- **Auto-discovery**: imports `free_llm_router`; if not installed, it adds `~/free-llm-router/python` (this repo) and the vendored copy in `~/social_scraper` to `sys.path` before importing. So `pip install -e ~/free-llm-router/python` is optional.
- Requires at least one provider key (`GROQ_API_KEY` / `CEREBRAS_API_KEY` / `GOOGLE_AI_STUDIO_API_KEY` / `MISTRAL_API_KEY` / `OPENROUTER_API_KEY`); degrades with a clean error otherwise.
- Default `tier` is `smart` (overridable with `--tier fast`).

This adds farmctl to the consumer list in the root README's "How it's wired into each app" table.

| App | Language | Integration | Direction |
|-----|----------|-------------|-----------|
| **farmctl** | Py (CLI) | `ai` runs `FreeLLMRouter.chat_completion` over Android screen text for extraction | free-first, lazy import |
