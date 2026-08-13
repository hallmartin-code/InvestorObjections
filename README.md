# investor-toolkit

A tool for early-stage investment work, usable as a CLI or as a web app. Point it at a pitch
deck (PDF or PPTX) and it extracts the deal facts with Claude, then produces two artifacts: a
branded single-page investor leave-behind PDF, and a set of five follow-up emails, each anchored
to a different "why now" angle (timing inflection, de-risking, market structural shift, capital
efficiency, and a soft re-engagement ask). Extraction is deliberately conservative — the model is
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

## CLI usage

```bash
.venv/bin/investor-toolkit decks/halcyon_bio_seed.pdf \
  --context "intro via mutual LP, met at JPM Healthcare 2026" \
  --output-dir ./outputs \
  --print-emails
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
| `--emails-only` | Skip PDF generation, generate emails only |
| `--pdf-only` | Skip email generation, produce PDF only |
| `--print-emails` | Print all five emails to stdout after saving |
| `--help` | Show the help message and exit |

## Web app

```bash
make serve          # http://127.0.0.1:8000
```

Upload a deck (click or drag and drop), give the interaction context, and choose which outputs
you want. A run takes two Claude calls and typically one to three minutes, so uploads become
background jobs: the browser is redirected to a job page that shows a live progress stepper,
then the five emails inline with download buttons for the PDF and the Markdown file.

The interface follows the TEN Capital Network design: dark navy card on an ambient tri-colour
glow, Sora/Inter/JetBrains Mono type, and the three-figure brand mark. Markup lives in
`investor_toolkit/web_templates/` — `base.html` carries the whole design system, and
`index.html` / `job.html` / `error.html` are the three screens.

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
3. Deploy, then generate a public domain for the service. Railway injects `PORT`; the start
   command already binds to it on `0.0.0.0`.
4. Keep the service at one replica.

### Environment variables

| Variable | Required | Effect |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Claude API key. Without it the form is disabled and `POST /jobs` returns 503 |
| `APP_PASSWORD` | No | When set, the whole site is behind HTTP Basic auth (any username, this password). When unset, the deployment is **open to anyone with the URL — and every run spends on your API key** |
| `RESEND_API_KEY` | No | Resend API key, for the archive copy (below) |
| `MAIL_FROM` | No | Sender, e.g. `Deck Analyzer <deck-analyzer@tencapital.group>` |
| `RUN_COPY_TO` | No | Address that receives a copy of every completed run |
| `JOB_TTL_SECONDS` | No | How long artifacts are kept. Default `3600` |
| `WEB_MAX_WORKERS` | No | Concurrent decks processed. Default `2` |

Uploads are capped at 25MB.

### Archive email

When `RESEND_API_KEY`, `MAIL_FROM` and `RUN_COPY_TO` are all set, every completed run emails
its artifacts to `RUN_COPY_TO` through Resend, and the upload page's disclosure names that
address. Setting only some of the three is treated as a misconfiguration: a warning appears on
the upload page and no copy is sent, rather than the disclosure quietly becoming untrue. Leave
all three unset and the feature is off, with no mention of it in the UI.

`MAIL_FROM` must use a domain verified in your Resend dashboard — an unverified domain is the
usual cause of a rejected send. A mail failure never loses the analysis: the run still completes,
the downloads still work, and the results page carries a warning with Resend's reason.

This applies to the web app only. CLI runs never send email.

## Outputs

Both files land in `--output-dir` (default `./outputs`, gitignored) for the CLI, or behind the
download links for the web app:

- **`{Company}_one_pager.pdf`** — a single letter-size page: navy header band with company name
  and tagline; a two-column body carrying Problem, Solution, Why Now, Business Model and Market
  Size on the left and Traction, Team, Stage, Ask and Key Risks Retired on the right; and a
  confidentiality footer with contact details. Content that exceeds its column is truncated with
  an ellipsis so the output is always exactly one page.
- **`{Company}_follow_up_emails.md`** — the five follow-up emails under `## Email N — Title`
  headers, each 90–140 words, with word counts. Written to be forwardable inside a partnership:
  calm, factual, no hype, and no direct ask for money.

## Document template

Both outputs are rendered from a structural template rather than hard-coded layout, so the
document format is edited in one place and applied to every future deck:

- **`investor_toolkit/templates/one_pager_template.json`** — the analyzed field set (each field
  with the extraction guidance sent to the model), the page geometry, palette and typography,
  the header and footer blocks, the two columns and the labelled section in each, the overflow
  rule, and the five email angles with their focus and goal. It holds structure only, no
  company content.
- **`investor_toolkit/templates/follow_up_emails_template.md`** — the Markdown skeleton for the
  email document: a header block and one repeatable email block marked by
  `<!-- BEGIN EMAIL BLOCK -->` / `<!-- END EMAIL BLOCK -->`, using `{{placeholder}}` tokens.

Render the blank structure to see what the template produces before running a deck through it:

```bash
python -m investor_toolkit.one_pager outputs/one_pager_template.pdf
```

To use a different template for a run, point `INVESTOR_TOOLKIT_TEMPLATE` at another JSON file
with the same shape. Adding, reordering, relabelling or moving a section between columns is a
template edit only. Adding a *new* analyzed field also requires adding it to `DealData` in
`models.py`; the app validates this on startup and names the offending field if the two drift
apart.

## Model configuration

Both calls run on `claude-opus-5` with structured outputs (`output_config.format`), so responses
are schema-validated JSON rather than parsed prose. Extraction runs at `medium` effort — it is a
literal extraction task — and email generation at `high`, where writing quality is the whole
deliverable. Responses are streamed so a long request cannot hit an HTTP read timeout.

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
`one_pager.py` (ReportLab canvas rendering), `email_generator.py` (Claude email set),
`models.py` (Pydantic models), `template.py` (template loading and validation),
`templates/` (the document structure template), `cli.py` (Click entry point),
`web.py` + `web_templates/` (FastAPI app), `mailer.py` (Resend archive copy).
