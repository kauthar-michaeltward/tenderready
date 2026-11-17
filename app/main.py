from pathlib import Path
import hmac
import os
import secrets

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.analysis import analyze_tender
from app.documents import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_FILES,
    MAX_FILE_BYTES,
    extract_bundle,
    safe_filename,
)
from app.models import AnalysisRecord, BusinessProfile
from app import storage


load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
IS_PRODUCTION = os.getenv("APP_ENV", "").lower() == "production"
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if IS_PRODUCTION and not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET must be set when APP_ENV=production.")

app = FastAPI(title="TenderReady", description="Grounded tender analysis for African SMEs.")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET or "local-development-only-secret",
    same_site="lax",
    https_only=IS_PRODUCTION,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _owner_id(request: Request) -> str:
    if not os.getenv("TESTER_ACCESS_CODE"):
        if not IS_PRODUCTION:
            return "local"
        # Public site: every visitor gets a private session automatically.
        if not request.session.get("tester_id"):
            request.session["tester_id"] = secrets.token_urlsafe(32)
        return request.session["tester_id"]
    owner_id = request.session.get("tester_id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Enter the tester access code to continue.")
    return owner_id


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if os.getenv("TESTER_ACCESS_CODE") and not request.session.get("tester_id"):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": None},
        )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "profile": storage.get_profile(_owner_id(request)),
            "tester_mode": bool(os.getenv("TESTER_ACCESS_CODE")) or IS_PRODUCTION,
            "access_required": bool(os.getenv("TESTER_ACCESS_CODE")),
        },
    )


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, access_code: str = Form(...)):
    configured_code = os.getenv("TESTER_ACCESS_CODE", "")
    if not configured_code or not hmac.compare_digest(access_code, configured_code):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "That access code is not valid. Please try again."},
            status_code=401,
        )
    request.session.clear()
    request.session["tester_id"] = secrets.token_urlsafe(32)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/api/profile")
def read_profile(request: Request):
    profile = storage.get_profile(_owner_id(request))
    if profile is None:
        raise HTTPException(status_code=404, detail="No business profile has been saved yet.")
    return profile


@app.put("/api/profile")
def write_profile(profile: BusinessProfile, request: Request):
    storage.save_profile(profile.model_dump(), _owner_id(request))
    return {"message": "Business profile saved.", "profile": profile.model_dump()}


def _process_background_analysis(
    analysis_id: int,
    segments: list,
    profile: BusinessProfile,
) -> None:
    try:
        analysis = analyze_tender(segments, profile)
        storage.update_analysis_status(
            analysis_id,
            status="complete",
            analysis=analysis.model_dump(),
            error=None,
        )
    except Exception as exc:  # pragma: no cover - exercised via tests via mocks
        detail = getattr(exc, "detail", str(exc))
        if isinstance(detail, list):
            detail = "; ".join(str(item) for item in detail)
        storage.update_analysis_status(
            analysis_id,
            status="failed",
            analysis=None,
            error=str(detail),
        )


@app.post("/api/analyses")
async def create_analysis(
    background_tasks: BackgroundTasks,
    request: Request,
    files: list[UploadFile] = File(...),
):
    owner_id = _owner_id(request)
    if not files:
        raise HTTPException(status_code=422, detail="Upload at least one tender document.")
    if len(files) > MAX_BUNDLE_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload no more than {MAX_BUNDLE_FILES} files at a time.",
        )
    profile_data = storage.get_profile(owner_id)
    if profile_data is None:
        raise HTTPException(status_code=400, detail="Save your business profile before analyzing a tender.")
    profile = BusinessProfile.model_validate(profile_data)
    uploads = []
    total_bytes = 0
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=422, detail="Each uploaded document needs a filename.")
        content = await file.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Each uploaded file must be 12 MB or smaller: {safe_filename(file.filename)}.",
            )
        total_bytes += len(content)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise HTTPException(status_code=413, detail="The combined upload must be 40 MB or smaller.")
        uploads.append((file.filename, content))

    segments = extract_bundle(uploads)
    file_names = [safe_filename(filename) for filename, _ in uploads]
    bundle_name = ", ".join(file_names)
    page_count = sum(segment.page is not None for segment in segments)
    analysis_id = storage.save_analysis(
        bundle_name,
        page_count,
        len(file_names),
        owner_id=owner_id,
        status="processing",
    )
    if background_tasks is not None:
        background_tasks.add_task(_process_background_analysis, analysis_id, segments, profile)
    return {"id": analysis_id, "status": "processing"}


@app.get("/api/analyses")
def recent_analyses(request: Request):
    return storage.list_analyses(_owner_id(request))


@app.get("/api/analyses/{analysis_id}", response_model=AnalysisRecord)
def read_analysis(analysis_id: int, request: Request):
    record = storage.get_analysis(analysis_id, _owner_id(request))
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return record
