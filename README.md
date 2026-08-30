# investor-toolkit

A tool for early-stage investment work, usable as a CLI or as a web app. Point it at a pitch
deck (PDF, PPTX or DOCX) and it extracts the deal facts with Claude, then produces two artifacts: a
branded single-page investor leave-behind PDF, and the ten objections an investor will raise
about the deal, each with a rebuttal grounded in the deck. Extraction is deliberately conservative — the model is
instructed to return an empty field rather than infer or embellish, so anything on the one-pager
traces back to the deck.

## Installation

```bash
make install
```

This creates a `.venv` and installs the package in editable mode with dev extras. Then copy the
environment template and add your key:

```bash
cp .env.example .env      # then edit .env and set ANTHROPIC_API_KEY
```

An Anthropic API key is required — both the deal extraction and the email generation call
Claude. Create one at [console.anthropic.com](https://console.anthropic.com/settings/keys). The
tool exits immediately if `ANTHROPIC_API_KEY` is not set.

`.env` is loaded with `override=True`, so its values beat machine-wide environment variables of
the same name — a stale global `ANTHROPIC_API_KEY` can't silently shadow the project's key. The
trade-off is that a one-off shell override (`ANTHROPIC_API_KEY=other investor-toolkit ...`) is
ignored while `.env` exists; edit or remove the file instead. On Railway there is no `.env`, so
the service variables are used directly.

## CLI usage

```bash
.venv/bin/investor-toolkit decks/halcyon_bio_seed.pdf \
  --context "intro via mutual LP, met at JPM Healthcare 2026" \
  --output-dir ./outputs \
  --print-objections
```

Or through the Makefile:

```bash
make run DECK=decks/halcyon_bio_seed.pdf CONTEXT="inbound from AngelList"
```

### Options

| Option | Description |
| --- | --- |
| `--context TEXT` | How the investor interaction occurred. Default: `initial outreach` |
| `--output-dir PATH` | Directory for output files. Default: `./outputs` |
| `--objections-only` | Skip PDF generation, generate objections only |
| `--pdf-only` | Skip objection generation, produce PDF only |
| `--print-objections` | Print all ten objections and rebuttals to stdout after saving |
| `--no-email` | Skip the archive email for this run (see Archive email below) |
| `--help` | Show the help message and exit |

## Web app

```bash
make serve          # http://127.0.0.1:8000
```

Upload a deck (click or drag and drop), give the interaction context, and choose which outputs
you want. A run takes two Claude calls and typically one to three minutes, so uploads become
background jobs: the browser is redirected to a job page that shows a live progress stepper,
then the ten objections and rebuttals inline with download buttons for the PDF and the
Markdown file.

The interface follows the TEN Capital Network design: dark navy card on an ambient tri-colour
glow, Sora/Inter/JetBrains Mono type, and the real three-figure brand mark. Markup lives in
`investor_toolkit/web_templates/` — `base.html` carries the whole design system, and
`index.html` / `job.html` / `error.html` are the three screens.

The brand mark is the authentic 256×256 asset at `investor_toolkit/static/ten_capital_mark.png`,
served from `/static`. The favicon is derived from it: `static/favicon.ico` (16/32/48px) is
served at the root `/favicon.ico`, which is the path browsers request on their own regardless of
the `<link>` tags, with the 256px PNG declared alongside it for high-DPI tabs. `/static` is mounted outside the auth gate, so
the mark and favicon still render on the HTTP Basic challenge page. The accent palette is
sampled from that file — `--teal` is `#4FC4D6`, taken from the mark itself rather than
approximated, so the UI accents match the logo sitting beside them.

Progress updates poll `/api/jobs/{id}` and reload the page when the stage advances; with
JavaScript disabled the page falls back to a `<meta refresh>`. File type and size are checked
in the browser for fast feedback and again on the server, which is the real gate.

| Route | Purpose |
| --- | --- |
| `GET /` | Upload form |
| `POST /jobs` | Start a run; redirects to the job page |
| `GET /jobs/{id}` | Job status page, then results |
| `GET /api/jobs/{id}` | Same state as JSON, for scripting |
| `GET /jobs/{id}/download/{pdf\|markdown}` | Download an artifact |
| `GET /healthz` | Liveness probe (never gated, no key required) |

Artifacts live in a per-job temporary directory and are deleted after `JOB_TTL_SECONDS`
(default one hour). Because jobs are held in process memory, run the service as a **single
replica** — a second instance will not know about the first one's jobs.

## Deploying to Railway

The repo carries everything Railway needs: `requirements.txt` (Nixpacks install),
`.python-version`, a `Procfile`, and `railway.toml` with the start command and a health check
against `/healthz`.

1. Create a Railway project from this repo (`railway init` / `railway up`, or connect the
   GitHub repo in the dashboard).
2. Set the service variables:
   - `ANTHROPIC_API_KEY` — **required**
   - `APP_PASSWORD` — strongly recommended (see below)
   - `RESEND_API_KEY` — to email a copy of every run (see Archive email)
3. Deploy, then generate a public domain for the service. Railway injects `PORT`; the start
   command already binds to it on `0.0.0.0`.
4. Keep the service at one replica.

### Environment variables

| Variable | Required | Effect |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Claude API key. Without it the form is disabled and `POST /jobs` returns 503 |
| `APP_PASSWORD` | No | When set, the whole site is behind HTTP Basic auth (any username, this password). When unset, the deployment is **open to anyone with the URL — and every run spends on your API key** |
| `RESEND_API_KEY` | No | Switches the archive email on (below). The only mail variable you need |
| `MAIL_FROM` | No | Override the sender. Default: `TEN Capital Deck Analyzer <deck-analyzer@tencapital.group>` |
| `RUN_COPY_TO` | No | Override the recipient. Default: `Info@tencapital.group` |
| `JOB_TTL_SECONDS` | No | How long artifacts are kept. Default `3600` |
| `WEB_MAX_WORKERS` | No | Concurrent decks processed. Default `2` |

Uploads are capped at 25MB.

### Archive email

Set `RESEND_API_KEY` and **every completed run — web app and CLI alike — emails its results**
through Resend, with the upload page's disclosure naming the recipient. That one variable is all
a new environment needs: `MAIL_FROM` and `RUN_COPY_TO` are optional overrides that default to
`TEN Capital Deck Analyzer <deck-analyzer@tencapital.group>` and `Info@tencapital.group`
(both defined at the top of `mailer.py`).

The message carries the analysis itself, not just files: a deal summary block (stage, ask, why
now, traction, market size, business model, team, moat, risks retired, contact) and all five
objections and rebuttals rendered inline, so it is readable straight from the inbox.
The one-pager PDF and the Markdown file are attached as well. Everything drawn from the deck is
HTML-escaped before it reaches the body.

Setting `MAIL_FROM` or `RUN_COPY_TO` *without* `RESEND_API_KEY` is a genuine half-configuration:
a warning appears on the upload page and no copy is sent, rather than the disclosure quietly
becoming untrue. Leave all three unset and the feature is off, with no mention of it in the UI.

`MAIL_FROM` must use a domain verified in your Resend dashboard — an unverified domain is the
usual cause of a rejected send. A mail failure never loses the analysis: the run still completes,
the artifacts are still written, and the failure is reported (a warning on the results page, a
yellow line on the CLI) with Resend's own reason.

Pass `--no-email` to a CLI run to skip the archive copy for that run — useful while testing so
the inbox doesn't fill with sample decks.

## Outputs

Both files land in `--output-dir` (default `./outputs`, gitignored) for the CLI, or behind the
download links for the web app:

- **`{Company}_one_pager.pdf`** — a single letter-size page: navy header band with company name
  and tagline; a two-column body carrying Problem, Solution, Why Now, Business Model and Market
  Size on the left and Traction, Team, Stage, Ask and Key Risks Retired on the right; and a
  confidentiality footer with contact details. Content that exceeds its column is truncated with
  an ellipsis so the output is always exactly one page.
- **`{Company}_objections.md`** — the ten objections under `## N. Title` headings, each with
  the objection in the investor's own voice (25–45 words) and a rebuttal (70–120 words). The
  rebuttal is grounded strictly in the deck: where the deck does not support an answer it says
  so and names the evidence that would settle it, rather than inventing support.

## Document template

Both outputs are rendered from a structural template rather than hard-coded layout, so the
document format is edited in one place and applied to every future deck:

- **`investor_toolkit/templates/one_pager_template.json`** — the analyzed field set (each field
  with the extraction guidance sent to the model), the page geometry, palette and typography,
  the header and footer blocks, the two columns and the labelled section in each, the overflow
  rule, and the ten objection categories with what each probes and what a strong rebuttal must
  do. It holds structure only, no company content.
- **`investor_toolkit/templates/objections_template.md`** — the Markdown skeleton for the
  objections document: a header block and one repeatable objection block marked by
  `<!-- BEGIN OBJECTION BLOCK -->` / `<!-- END OBJECTION BLOCK -->`, using `{{placeholder}}`
  tokens.

Render the blank structure to see what the template produces before running a deck through it:

```bash
python -m investor_toolkit.one_pager outputs/one_pager_template.pdf
```

To use a different template for a run, point `INVESTOR_TOOLKIT_TEMPLATE` at another JSON file
with the same shape. Adding, reordering, relabelling or moving a section between columns is a
template edit only. Adding a *new* analyzed field also requires adding it to `DealData` in `models.py`, and a new
objection category requires a matching field on `ObjectionSet`; the app validates both on
startup and names the offending key if the two drift apart.

## Model configuration

Both calls run on `claude-opus-5` with structured outputs (`output_config.format`), so responses
are schema-validated JSON rather than parsed prose. Extraction runs at `medium` effort — it is a
literal extraction task — and objection generation at `high`, where judging what the deck does
and does not support is the actual work. Responses are streamed so a long request cannot hit an HTTP read timeout.

Server-side refusal fallbacks are enabled (`fallbacks: "default"`): if Opus 5's safety
classifiers decline a request, Anthropic re-runs it on the recommended fallback model inside the
same call. If the whole chain declines, the run fails with the refusal category rather than
returning partial output.

## Supported decks

- `.pdf` — text layer required; image-only scans will not extract
- `.pptx` — text boxes, grouped shapes, and tables, one chunk per slide
- `.docx` — paragraphs and tables, split on page breaks

Any other extension is rejected, by the file picker and again on the server.

## Development

```bash
make lint     # ruff over investor_toolkit/
```

Layout: `parser.py` (deck ingestion), `synthesizer.py` (Claude client + deal extraction),
`one_pager.py` (ReportLab canvas rendering), `objections.py` (Claude objection set),
`models.py` (Pydantic models), `template.py` (template loading and validation),
`templates/` (the document structure template), `cli.py` (Click entry point),
`web.py` + `web_templates/` (FastAPI app), `mailer.py` (Resend archive copy).
