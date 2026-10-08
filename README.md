<p align="center">
  <img src=".github/assets/logo.png" alt="Mira logo" width="120" />
</p>

<h1 align="center">Mira <small>(fork)</small></h1>

<p align="center">
  <strong>Self-hosted AI code review — with GitHub PAT auth, Command Code / OpenCode providers, and live cost tracking.</strong>
</p>

<p align="center">
  <a href="https://github.com/daschinmoy21/mira"><img src="https://img.shields.io/badge/GitHub-daschinmoy21%2Fmira-181717?style=flat&logo=github" alt="This fork" /></a>
  <a href="https://github.com/miracodeai/mira"><img src="https://img.shields.io/badge/Upstream-miracodeai%2Fmira-orange?style=flat&logo=github" alt="Upstream Mira" /></a>
  <a href="https://docs.miracode.ai"><img src="https://img.shields.io/badge/Docs-docs.miracode.ai-blue?style=flat&logo=readthedocs&logoColor=white" alt="Upstream documentation" /></a>
</p>

<p align="center">
  <a href="#whats-different-in-this-fork">Fork changes</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="https://docs.miracode.ai">Upstream Docs</a> ·
  <a href="#benchmark">Benchmark</a>
</p>

> **Fork of [miracodeai/mira](https://github.com/miracodeai/mira).** Same self-hosted review engine, indexing, and dashboard — plus personal GitHub PAT auth (no App), first-class [Command Code](https://commandcode.ai) / [OpenCode](https://opencode.ai) LLM profiles, and dashboard spend tracking with cost-by-model breakdown.

Self-host every feature: full review engine, codebase indexing, vulnerability scanning, custom rules, org-wide package search, dashboard, learning loop. No paid tier, no license key, no SaaS upsell.

Mira reviews your pull requests using your choice of LLM (OpenRouter, Command Code, OpenCode, Bedrock, or any OpenAI-compatible endpoint) and posts concise, actionable feedback. The noise filter, confidence clamping, and learning loop ensure you only see comments that matter. See [`FEATURES.md`](FEATURES.md) for the full surface (upstream feature set still applies).

## What's different in this fork

| Area | Change |
|------|--------|
| **GitHub auth** | **PAT mode** — review as a personal collaborator with `MIRA_GITHUB_TOKEN` + repo/org webhooks. No GitHub App install required. Upstream App mode still works. |
| **LLM providers** | Built-in profiles for **Command Code** (`CMD_API_KEY`) and **OpenCode Go / Zen** (`OPENCODE_API_KEY`), plus **xAI Grok via SuperGrok / X Premium login** (`mira login xai`), in addition to OpenRouter and other OpenAI-compatible endpoints. |
| **OpenCode reliability** | Retries opaque HTTP 400s from forced `tool_choice` (e.g. OpenCode Go) by falling back to `tool_choice=auto`. |
| **Cost telemetry** | Persists real LLM tokens + billed cost per review; dashboard Cost / Spend cards show **cost by model**. Indexing estimates use **live model pricing** when available. |
| **Dashboard UX** | Activity page no longer forces page-level horizontal scroll; cost breakdowns surface model IDs cleanly. |
| **Deploy** | Build from this repo's `Dockerfile` (upstream `ghcr.io/miracodeai/mira` images do not include the PAT / provider work). |

Upstream docs, Discord, and benchmarks still describe the core product. Prefer this README for PAT setup, Command Code / OpenCode config, and local image builds.

## Why Teams Choose Mira

- **Model agnostic** — Claude, GPT, Gemini, DeepSeek, Llama, or any OpenAI-compatible endpoint: OpenRouter, Command Code, OpenCode Go/Zen, vLLM, Ollama, Together, Groq, Fireworks, or AWS Bedrock. Per-provider quirks are config, not code.
- **GitHub without an App** *(this fork)* — use a fine-grained or classic PAT as a collaborator; post reviews and answer `@mention` questions the same way App mode does.
- **Zero markup on LLM costs** — bring your own key. Dashboard shows real per-repo and **per-model** spend, not estimates.
- **Learns from your context** — synthesizes rules from merged PRs: rejected comments and human review patterns become team rules.
- **You set the rules** — custom and org-wide rules in plain language, per-repo via `.mira.yaml` or the dashboard.
- **Privacy first** — self-hosted by default. Diffs, indexes, review history, and CVE data live in your SQLite or Postgres. No phone-home, no required telemetry.
- **Low-noise reviews** — confidence thresholds, dedup, self-critique, and per-PR caps; fastest tool on the public [Code Review Bench](#benchmark).
- **Cross-PR overlap** — flags merge-conflict risk and duplicate effort against other open PRs.
- **Indexed, cross-file context** — full-repo index, org-wide package search, hourly OSV.dev CVE scanning.
- **GitHub, GitLab, and Forgejo** — auto-reviews PRs/MRs and answers bot mentions inline. Engine and dashboard are provider-agnostic.
- **Self-host on day one** — Docker + Railway / Fly.io / Render configs, SQLite or Postgres.

## Dashboard

![Mira dashboard](.github/assets/Dashboard.png)

## Your data, your dashboard

Most AI reviewers are SaaS: your diffs leave for a third-party server, and the only "view" you get is the comments that come back on a PR. Mira flips both halves:

- **Your code never leaves your infra.** Diffs, embeddings, indexes, review history, vulnerability data — all in your SQLite or Postgres.
- **The dashboard is yours**, with signals SaaS reviewers typically don't expose:
  - **Org-wide package inventory** and **CVE alerts** (hourly OSV.dev).
  - **Dependency + blast-radius graphs**.
  - **Per-repo review event stream** for live troubleshooting.
  - **Cost & token telemetry** — actual spend per repo and **per model** (this fork).
  - **Review-health page** — stale PRs, reviewer leaderboard, throughput, rubber-stamp detection, contributor analytics.

## Benchmark

Mira is **the fastest tool measured** on the public [Code Review Bench](https://codereview.withmartian.com/?mode=offline), and the only one on the speed/quality Pareto frontier: every tool that scores higher on F1 takes **5–14× longer per PR**.

![Median review time per PR, Mira vs every published competitor](.github/assets/benchmark-frontier.svg)

Plotted against every published competitor on the same subset, Mira sits in the upper-left corner: everything to the right is slower; everything above it pays 5–14× the wall time for the extra F1.

![Speed vs quality: Mira on the Pareto frontier](.github/assets/benchmark-by-language.svg)

Measured on the same 50-PR offline benchmark, judged by Claude Sonnet 4.6.

| | **Mira** | Cubic-v2 | Greptile | CodeRabbit | GitHub Copilot |
|---|---:|---:|---:|---:|---:|
| F1 | **44** | 56 | 35 | 32 | 31 |
| Precision | **43%** | 50% | 32% | 24% | 24% |
| Recall | **46%** | 65% | 40% | 50% | 43% |
| Median time / PR | **~77s** | ~9m | ~5m | ~5m | ~10m |

> Methodology: scores measured against the [Martian Code Review Bench](https://codereview.withmartian.com/?mode=offline) offline dataset with Claude Sonnet 4.6 as the judge. Figures are from upstream Mira; this fork does not change the review engine's quality/latency profile.

## Quick Start

Run this fork to auto-review every PR and merge request and answer bot `@mention` questions inline. GitHub supports **App mode** (upstream) or **PAT mode** (this fork). GitLab and Forgejo/Codeberg use access tokens as upstream documents.

**1. Deploy** — build from this repo (recommended for PAT + provider profiles):

```yaml
# mira.yaml — deployment-wide defaults. Every key is optional.
llm:
  model: "anthropic/claude-sonnet-4-6"
  indexing_model: "anthropic/claude-haiku-4-5"
  # base_url / api_key_env default to OpenRouter. Alternatives (uncomment one):
  #
  # Command Code — https://commandcode.ai/docs/provider  (env: CMD_API_KEY)
  # base_url: "https://api.commandcode.ai/provider/v1"
  # api_key_env: "CMD_API_KEY"
  # model: "deepseek/deepseek-v4-flash"
  #
  # OpenCode Go — https://opencode.ai/docs/go/  (env: OPENCODE_API_KEY)
  # base_url: "https://opencode.ai/zen/go/v1"
  # api_key_env: "OPENCODE_API_KEY"
  # model: "kimi-k2.7-code"
  # indexing_model: "deepseek-v4-flash"
  #
  # OpenCode Zen (pay-as-you-go; same OPENCODE_API_KEY)
  # base_url: "https://opencode.ai/zen/v1"
  # api_key_env: "OPENCODE_API_KEY"
  #
  # xAI Grok via your SuperGrok / X Premium login — run `mira login xai` (see below)
  # base_url: "https://api.x.ai/v1"
  # api_style: "responses"
  # model: "xai/grok-4.5"
```

```bash
# .env — secrets only.
# --- GitHub App mode (upstream) ---
# MIRA_GITHUB_APP_ID=123456
# MIRA_GITHUB_PRIVATE_KEY="$(cat private-key.pem)"
# --- GitHub PAT mode (this fork; personal collaborator; no App) ---
MIRA_GITHUB_TOKEN=ghp_...
MIRA_WEBHOOK_SECRET=your-secret
# LLM (pick the key that matches llm.api_key_env / base_url above)
OPENROUTER_API_KEY=sk-or-...
# CMD_API_KEY=...
# OPENCODE_API_KEY=...
```

```bash
# This fork (PAT + Command Code / OpenCode profiles) — build locally:
docker build -t mira:local .
docker run -p 8000:8000 --env-file .env \
  -v "$(pwd)/mira.yaml:/app/mira.yaml" \
  mira:local --config /app/mira.yaml

# Upstream image (App mode only; lacks this fork's PAT/provider work):
# docker run -p 8000:8000 --env-file .env \
#   -v "$(pwd)/mira.yaml:/app/mira.yaml" \
#   ghcr.io/miracodeai/mira:latest --config /app/mira.yaml
```

One-click Railway / Fly / Render still work if you **build from this Dockerfile** rather than the upstream image. See comments in `railway.toml`, `fly.toml`, and `render.yaml`.

**2. Connect GitHub**

- **App mode:** install a GitHub App on your repos ([upstream quickstart](https://docs.miracode.ai/quickstart)).
- **PAT mode (this fork):**
  1. Create a fine-grained or classic PAT with repo + PR write access.
  2. Add the PAT user as a collaborator on each target repo.
  3. Create a **repo or org webhook** → `https://<your-host>/github/webhook` with the same `MIRA_WEBHOOK_SECRET`.
  4. Events: `pull_request`, `issue_comment`, `pull_request_review`, `pull_request_review_comment`, `push`.

→ [Upstream GitHub App quickstart](https://docs.miracode.ai/quickstart) · [GitLab](https://docs.miracode.ai/gitlab) · [deploy options](https://docs.miracode.ai/deployment) · [models & custom endpoints](https://docs.miracode.ai/configuration/models)

### xAI Grok (SuperGrok / X Premium login)

Use Grok with your xAI subscription instead of an API key — the same device-code
sign-in the Grok Build CLI and `pi` use. On the machine that runs Mira:

```bash
mira login xai            # prints a link + code, opens your browser; approve it
```

```yaml
# mira.yaml
llm:
  base_url: "https://api.x.ai/v1"
  api_style: "responses"
  model: "xai/grok-4.5"
```

Mira stores the token in ``, else `/xai-oauth.json`,
else `~/.mira/xai-oauth.json` (mode 0600) and refreshes it automatically. In Docker,
run `mira login xai --no-browser` inside the container and keep the file on the
persistent data volume. `mira logout xai` removes it. If `XAI_API_KEY` is set it takes
precedence over the login; keys for other services (e.g. `OPENROUTER_API_KEY`) are never
sent to xAI. Reasoning effort `low`/`medium`/`high` is supported (`max` maps to `high`).
Dashboard cost figures for subscription usage are API-price estimates, not billed amounts.

**From the dashboard:** admins can do the same without a terminal. In **Settings** (and the
first-run setup) the **Provider** card lets you pick *xAI Grok* or *OpenAI Codex*, and **Log in**
shows a one-time code plus a sign-in link, then picks up the approval automatically. The model
pickers then list that provider's models (Grok from the built-in registry plus xAI's live list;
Codex straight from the installed CLI via `codex debug models`) with context window, price and
reasoning badges. Switching provider swaps the endpoint for every review/indexing call; models
that the new provider can't serve fall back to its default (`xai/grok-4.5`, `codex-default`).
Logging out of the active provider returns Mira to the deployment default.

### Codex CLI

If you already use OpenAI Codex locally, Mira can run reviews through the
Codex CLI instead of an HTTP API key. Authentication stays inside Codex via
`CODEX_HOME/auth.json`, created by `codex login`:

```yaml
# mira.yaml
llm:
  provider: "codex-cli"
  model: "codex-default"      # use the Codex CLI default model
  codex_home: "/run/codex"    # optional; defaults to CODEX_HOME
  codex_sandbox: "read-only"  # the only accepted sandbox policy
  codex_timeout_seconds: 900  # optional
```

The official Mira image includes a pinned Codex CLI. Mount a Codex login read-only:

```bash
docker run -p 8000:8000 --env-file .env \
  -e CODEX_HOME=/run/codex \
  -v "$HOME/.codex:/run/codex:ro" \
  -v "$(pwd)/mira.yaml:/app/mira.yaml:ro" \
  ghcr.io/miracodeai/mira:latest --config /app/mira.yaml
```

This provider does not require `OPENROUTER_API_KEY`. You can also sign in from the dashboard (Settings → Provider → OpenAI Codex), which runs
`codex login --device-auth` on the Mira host and stores the result under `llm.codex_home`,
`$CODEX_HOME`, `$MIRA_CODEX_HOME`, or `$MIRA_INDEX_DIR/codex` (first one set). Mira copies only `auth.json`
from the read-only mount into a private, writable temporary Codex home for each
invocation. It launches Codex in an empty temporary workspace with a minimal
environment, disables inherited shell environment variables and user/project
rules, and enforces the read-only sandbox.
Provider choice, executable/auth paths, sandbox policy, and timeout are
deployment-only settings; repository `.mira.yaml` files cannot override them.
For one-shot `mira review` runs, `--config` is treated as untrusted by default.
An operator-owned config may opt in with `--trust-execution-settings`; never use
that flag with a repository-controlled file.

Codex CLI does not expose Mira's temperature or hard output-token controls, so
Mira disables ensemble sampling for this provider. The mounted OAuth session is
still a sensitive deployment credential: use a dedicated Codex account/session
and isolate the Mira container from unrelated host files and services.

## Configuration

`mira.yaml` (loaded via `--config`) holds deployment-wide defaults. Drop a `.mira.yaml` in any repo — or use the dashboard — to override per-repo; both deep-merge over `mira.yaml` for that repo only:

```yaml
# .mira.yaml — optional per-repo override
filter:
  confidence_threshold: 0.5  # noisier repo → lower bar
  max_comments: 10
```

→ Full schema and every key: [Configuration docs](https://docs.miracode.ai/configuration). Env template for this fork: [`.env.example`](.env.example).

## Development

```bash
git clone https://github.com/daschinmoy21/mira.git
cd mira
pip install -e ".[dev,serve]"
# or: uv sync --all-extras

# Run tests
pytest tests/ -v

# Regression suite (real GitHub + LLM, ~$1, ~3 min)
OPENROUTER_API_KEY=... GITHUB_TOKEN=... pytest -m eval -v

# Lint / types
ruff check src/ tests/
mypy src/mira/ --ignore-missing-imports
```

Upstream contributions belong on [miracodeai/mira](https://github.com/miracodeai/mira). Fork-specific work (PAT mode, Command Code / OpenCode, cost-by-model UI) lives here.

## License

Apache 2.0. See [LICENSE](LICENSE). Same license as upstream.
