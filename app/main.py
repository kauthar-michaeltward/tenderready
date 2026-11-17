import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analysis import analyze_tender
from app.models import AnalysisRecord, BusinessProfile
from app.pdf import extract_pdf_pages
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
async def create_analysis(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Upload a PDF file.")
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="The uploaded file must be a PDF.")
    profile_data = storage.get_profile()
    if profile_data is None:
        raise HTTPException(status_code=400, detail="Save your business profile before analyzing a tender.")
    profile = BusinessProfile.model_validate(profile_data)
    content = await file.read(12 * 1024 * 1024 + 1)
    pages = extract_pdf_pages(content)
    analysis = analyze_tender(pages, profile)
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename).name)[:160]
    analysis_id = storage.save_analysis(safe_name, len(pages), analysis.model_dump())
    record = storage.get_analysis(analysis_id)
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "file_name": record["file_name"],
        "page_count": record["page_count"],
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
