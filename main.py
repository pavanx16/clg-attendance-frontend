import os
import logging
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# Configuration
# --------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "attendance_db")
if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in .env")

GITHUB_PAT = os.getenv("GITHUB_PAT")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "pavanx16")
GITHUB_REPO = os.getenv("GITHUB_REPO", "githubaction")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "main.yml")

# Swagger UI / ReDoc paths — set to "" or "none" in .env to disable in production
DOCS_URL = os.getenv("DOCS_URL", "/docs")
REDOC_URL = os.getenv("REDOC_URL", "/redoc")
DOCS_URL = None if DOCS_URL.lower() in ("", "none", "disabled") else DOCS_URL
REDOC_URL = None if REDOC_URL.lower() in ("", "none", "disabled") else REDOC_URL

# --------------------------------------------------
# MongoDB Connection Pool
# --------------------------------------------------
client = MongoClient(
    MONGODB_URI,
    maxPoolSize=50,
    minPoolSize=5,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=10000,
    maxIdleTimeMS=60000,
)
db = client[DB_NAME]
attendance_collection = db["attendance_results"]

# --------------------------------------------------
# GitHub workflow trigger
# --------------------------------------------------
async def trigger_scrape_workflow():
    if not GITHUB_PAT:
        logger.error("GITHUB_PAT not set — cannot trigger workflow")
        return

    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/actions/workflows/{WORKFLOW_FILE}/dispatches"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            resp = await http_client.post(url, headers=headers, json={"ref": "main"})

        if resp.status_code == 204:
            logger.info("✓ Triggered scrape workflow successfully")
        else:
            logger.error(f"✗ Failed to trigger workflow: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"✗ Exception while triggering workflow: {e}")

scheduler = AsyncIOScheduler(timezone=ZoneInfo("Asia/Kolkata"))

# --------------------------------------------------
# FastAPI lifespan (startup/shutdown)
# --------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        client.admin.command("ping")
        logger.info("✓ Connected to MongoDB Atlas")
        logger.info("✓ MongoDB connection pool ready (min=5, max=50)")
    except Exception as e:
        logger.error(f"✗ MongoDB connection failed: {e}")

    # PRODUCTION: fires once every hour, 11:00-18:00 IST, every day.
    scheduler.add_job(
        trigger_scrape_workflow,
        CronTrigger(hour="11-18", minute=0, timezone=ZoneInfo("Asia/Kolkata")),
        id="trigger_scrape",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    logger.info("✓ Scheduler started — will trigger scrape hourly, 11:00-18:00 IST")

    yield

    # Shutdown
    scheduler.shutdown()
    client.close()
    logger.info("✓ MongoDB connection pool closed")
    logger.info("✓ Scheduler shut down")

# --------------------------------------------------
# FastAPI
# --------------------------------------------------
app = FastAPI(
    title="Attendance Dashboard",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
)
templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
# Dashboard
# --------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    users = list(
        attendance_collection.find(
            {},
            {
                "_id": 0,
                "username": 1,
                "email": 1,
                "scraped_at": 1,
                "subjects": 1,
                "statistics": 1,
                "total_subjects_scraped": 1,
            },
        ).sort("username", 1)
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "users": users,
        },
    )

# --------------------------------------------------
# Scheduler status / manual trigger (for testing)
# --------------------------------------------------
@app.get("/scheduler-status")
def scheduler_status():
    jobs = scheduler.get_jobs()
    return {
        "running": scheduler.running,
        "jobs": [
            {"id": j.id, "next_run": str(j.next_run_time)} for j in jobs
        ],
    }

@app.post("/trigger-scrape-now")
async def trigger_scrape_now():
    """Manually fire the workflow trigger, for testing without waiting for the schedule."""
    await trigger_scrape_workflow()
    return {"status": "triggered — check GitHub Actions tab"}
