from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
app = FastAPI(title="TenderReady", description="Grounded tender analysis for African SMEs.")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"profile": storage.get_profile()},
    )


@app.get("/api/profile")
def read_profile():
    profile = storage.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="No business profile has been saved yet.")
    return profile


@app.put("/api/profile")
def write_profile(profile: BusinessProfile):
    storage.save_profile(profile.model_dump())
    return {"message": "Business profile saved.", "profile": profile.model_dump()}


@app.post("/api/analyses", response_model=AnalysisRecord)
async def create_analysis(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=422, detail="Upload at least one tender document.")
    if len(files) > MAX_BUNDLE_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload no more than {MAX_BUNDLE_FILES} files at a time.",
        )
    profile_data = storage.get_profile()
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
    analysis = analyze_tender(segments, profile)
    file_names = [safe_filename(filename) for filename, _ in uploads]
    bundle_name = ", ".join(file_names)
    page_count = sum(segment.page is not None for segment in segments)
    analysis_id = storage.save_analysis(
        bundle_name,
        page_count,
        len(file_names),
        analysis.model_dump(),
    )
    record = storage.get_analysis(analysis_id)
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "file_name": record["file_name"],
        "page_count": record["page_count"],
        "document_count": record["document_count"],
        "analysis": record["analysis"],
    }


@app.get("/api/analyses")
def recent_analyses():
    return storage.list_analyses()


@app.get("/api/analyses/{analysis_id}", response_model=AnalysisRecord)
def read_analysis(analysis_id: int):
    record = storage.get_analysis(analysis_id)
    if not record:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return record
