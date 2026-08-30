"""FastAPI web front end for the toolkit — upload a deck, get the one-pager and emails.

Deck processing takes two Claude calls and can run for minutes, so uploads are handled as
background jobs: the browser is redirected to a job page that polls until the artifacts are
ready. Artifacts live in a per-job temporary directory and are swept after ``JOB_TTL_SECONDS``.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .email_generator import (
    EmailGenerationError,
    emails_to_list,
    emails_to_markdown,
    generate_emails,
)
from .mailer import MailError, load_config, send_run_copy
from .models import DealData, EmailSet
from .one_pager import generate_one_pager
from .parser import SUPPORTED_EXTENSIONS, extract_text
from .synthesizer import SynthesisError, extract_deal_data

# override=True: a project .env is an explicit, deliberate choice and should beat a stale
# machine-wide variable. Railway sets real env vars and ships no .env file.
load_dotenv(override=True)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))
MAX_WORKERS = int(os.environ.get("WEB_MAX_WORKERS", "2"))

# Stage labels, in pipeline order. The job page renders these as a progress stepper, so
# _run_job must set Job.stage to one of these exact strings.
STAGE_PARSE = "Parsing deck"
STAGE_EXTRACT = "Extracting deal data"
STAGE_PDF = "Generating one-pager"
STAGE_EMAILS = "Generating emails"
STAGE_MAIL = "Emailing archive copy"

ERROR_HEADINGS = {
    400: "That deck can't be processed",
    401: "Not authorized",
    404: "Nothing here",
    413: "That file is too large",
    503: "Service not configured",
}

TEMPLATE_DIR = Path(__file__).parent / "web_templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

app = FastAPI(title="TEN Capital — Investor Toolkit", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
# Brand assets are public by design — mounted before the auth dependency so the mark
# and favicon still render on the 401 challenge page.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="deck")
_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()

_basic = HTTPBasic(auto_error=False)


# --- auth -----------------------------------------------------------------------------


def require_access(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """Gate the app behind HTTP Basic when APP_PASSWORD is set; open access when it isn't."""
    password = os.environ.get("APP_PASSWORD", "")
    if not password:
        return
    supplied = credentials.password if credentials else ""
    if not secrets.compare_digest(supplied, password):
        raise HTTPException(
            status_code=401,
            detail="Not authorized",
            headers={"WWW-Authenticate": 'Basic realm="Investor Toolkit"'},
        )


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def mail_state() -> tuple[str | None, str | None]:
    """(archive recipient, configuration problem). Both None when mail is switched off."""
    try:
        config = load_config()
    except MailError as exc:
        return None, str(exc)
    return (config.recipient if config else None), None


# --- jobs -----------------------------------------------------------------------------


@dataclass
class Job:
    """One deck run: its inputs, its progress, and the artifacts it produced."""

    id: str
    filename: str
    context: str
    want_pdf: bool
    want_emails: bool
    workdir: Path
    created_at: float = field(default_factory=time.monotonic)
    status: str = "queued"  # queued | running | done | error
    stage: str = "Queued"
    error: str | None = None
    company: str = ""
    pdf_path: Path | None = None
    md_path: Path | None = None
    emails: list[dict] = field(default_factory=list)
    mail_to: str | None = None
    mail_status: str | None = None  # None | "sent" | "failed"
    mail_error: str | None = None

    def steps(self) -> list[dict]:
        """The pipeline as a progress stepper: each label with done/active/pending/failed."""
        labels = [STAGE_PARSE, STAGE_EXTRACT]
        if self.want_pdf:
            labels.append(STAGE_PDF)
        if self.want_emails:
            labels.append(STAGE_EMAILS)
        if self.mail_to:
            labels.append(STAGE_MAIL)

        if self.status == "done":
            return [{"label": label, "state": "done"} for label in labels]

        current = labels.index(self.stage) if self.stage in labels else -1
        out = []
        for index, label in enumerate(labels):
            if current < 0:
                state = "pending"  # still queued
            elif index < current:
                state = "done"
            elif index > current:
                state = "pending"
            else:
                state = "failed" if self.status == "error" else "active"
            out.append({"label": label, "state": state})
        return out

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "company": self.company,
            "pdf": f"/jobs/{self.id}/download/pdf" if self.pdf_path else None,
            "markdown": f"/jobs/{self.id}/download/markdown" if self.md_path else None,
            "emails": self.emails,
            "mail_to": self.mail_to,
            "mail_status": self.mail_status,
            "mail_error": self.mail_error,
        }


def _sweep_expired() -> None:
    """Delete artifacts for jobs past their TTL."""
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [j for j in _jobs.values() if j.created_at < cutoff]
        for job in stale:
            _jobs.pop(job.id, None)
    for job in stale:
        shutil.rmtree(job.workdir, ignore_errors=True)


def _get_job(job_id: str) -> Job:
    _sweep_expired()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return job


def _run_job(job: Job, deck_path: Path) -> None:
    """Execute one deck end to end. Runs on a worker thread; never raises."""
    try:
        job.status, job.stage = "running", STAGE_PARSE
        deck_text = extract_text(str(deck_path))
        if not deck_text.strip():
            raise ValueError(
                f"No extractable text found in {job.filename}. Is the deck image-only?"
            )

        job.stage = STAGE_EXTRACT
        deal: DealData = extract_deal_data(deck_text)
        job.company = deal.company_name or "Company"

        if job.want_pdf:
            job.stage = STAGE_PDF
            pdf_path = job.workdir / f"{deal.slug()}_one_pager.pdf"
            generate_one_pager(deal, str(pdf_path))
            job.pdf_path = pdf_path

        if job.want_emails:
            job.stage = STAGE_EMAILS
            emails: EmailSet = generate_emails(deal, job.context)
            md_path = job.workdir / f"{deal.slug()}_follow_up_emails.md"
            md_path.write_text(
                emails_to_markdown(deal, emails, job.context), encoding="utf-8"
            )
            job.md_path = md_path
            job.emails = emails_to_list(emails)

        # The archive copy is a side effect, not the deliverable: a mail failure is
        # reported on the results page but never loses a completed analysis.
        if job.mail_to:
            job.stage = STAGE_MAIL
            try:
                send_run_copy(
                    deal=deal,
                    deck_filename=job.filename,
                    context=job.context,
                    attachments=[p for p in (job.pdf_path, job.md_path) if p],
                    emails=job.emails,
                )
                job.mail_status = "sent"
            except MailError as exc:
                job.mail_status, job.mail_error = "failed", str(exc)

        job.stage, job.status = "Complete", "done"
    except (SynthesisError, EmailGenerationError, ValueError, FileNotFoundError) as exc:
        # Leave job.stage on the step that failed so the stepper can mark it.
        job.status, job.error = "error", str(exc)
    except Exception as exc:  # unexpected: still surface something actionable
        job.status, job.error = "error", f"{type(exc).__name__}: {exc}"


# --- routes ---------------------------------------------------------------------------


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Browsers request this path directly, regardless of the <link> tags."""
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness probe for Railway. Does not require auth or an API key."""
    return JSONResponse({"status": "ok", "api_key_configured": api_key_present()})


@app.exception_handler(StarletteHTTPException)
async def html_error_handler(request: Request, exc: StarletteHTTPException):
    """Render errors in the site's design for browsers, as JSON for the API."""
    headers = getattr(exc, "headers", None)
    wants_html = "text/html" in request.headers.get("accept", "")
    # 401 must stay a bare response so the browser shows its Basic-auth prompt.
    if exc.status_code == 401 or not wants_html or request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exc.status_code,
            "heading": ERROR_HEADINGS.get(exc.status_code, "Something went wrong"),
            "detail": exc.detail,
        },
        status_code=exc.status_code,
        headers=headers,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(require_access)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "api_key_missing": not api_key_present(),
            "mail_to": mail_state()[0],
            "mail_problem": mail_state()[1],
            "extensions": ",".join(SUPPORTED_EXTENSIONS),
            "extension_list": list(SUPPORTED_EXTENSIONS),
            "max_bytes": MAX_UPLOAD_BYTES,
            "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "ttl_minutes": max(1, JOB_TTL_SECONDS // 60),
        },
    )


@app.post("/jobs")
async def create_job(
    deck: UploadFile = File(...),
    context: str = Form("initial outreach"),
    outputs: list[str] = Form(default=["pdf", "emails"]),
    _: None = Depends(require_access),
) -> RedirectResponse:
    if not api_key_present():
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured.")

    suffix = Path(deck.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or deck.filename}'. "
            f"Supported types: {', '.join(SUPPORTED_EXTENSIONS)}",
        )

    payload = await deck.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Deck exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload limit.",
        )

    want_pdf = "pdf" in outputs
    want_emails = "emails" in outputs
    if not (want_pdf or want_emails):
        raise HTTPException(status_code=400, detail="Select at least one output.")

    _sweep_expired()
    job_id = uuid.uuid4().hex
    workdir = Path(tempfile.mkdtemp(prefix=f"deck-{job_id[:8]}-"))
    deck_path = workdir / f"deck{suffix}"
    deck_path.write_bytes(payload)

    job = Job(
        id=job_id,
        filename=deck.filename or f"deck{suffix}",
        context=(context or "initial outreach").strip(),
        want_pdf=want_pdf,
        want_emails=want_emails,
        workdir=workdir,
        mail_to=mail_state()[0],
    )
    with _jobs_lock:
        _jobs[job_id] = job
    _executor.submit(_run_job, job, deck_path)

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str, _: None = Depends(require_access)) -> HTMLResponse:
    job = _get_job(job_id)
    return templates.TemplateResponse(
        request,
        "job.html",
        {"job": job, "ttl_minutes": max(1, JOB_TTL_SECONDS // 60)},
    )


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, _: None = Depends(require_access)) -> JSONResponse:
    return JSONResponse(_get_job(job_id).as_dict())


@app.get("/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str, _: None = Depends(require_access)) -> FileResponse:
    job = _get_job(job_id)
    path = job.pdf_path if kind == "pdf" else job.md_path if kind == "markdown" else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="That artifact is not available.")
    media = "application/pdf" if kind == "pdf" else "text/markdown"
    return FileResponse(path, media_type=media, filename=path.name)
