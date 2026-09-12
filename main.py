#!/usr/bin/env python

import logging
import math
import os
from contextlib import asynccontextmanager
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pymongo import MongoClient


# ============================================================
# Environment
# ============================================================

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# User Model
# ============================================================

class User(BaseModel):
    email: str
    password: str
    track: str
    status: Literal["active", "inactive"] = "active"


# ============================================================
# Configuration
# ============================================================

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "attendance_db")

if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in .env")


GITHUB_PAT = os.getenv("GITHUB_PAT")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "pavanx16")
GITHUB_REPO = os.getenv("GITHUB_REPO", "githubaction")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "main.yml")


# ============================================================
# Swagger / ReDoc
# ============================================================

docs_url = os.getenv("DOCS_URL", "/docs")
redoc_url = os.getenv("REDOC_URL", "/redoc")

DOCS_URL = (
    None
    if docs_url.lower() in ("", "none", "disabled")
    else docs_url
)

REDOC_URL = (
    None
    if redoc_url.lower() in ("", "none", "disabled")
    else redoc_url
)


# ============================================================
# MongoDB
# ============================================================

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
users_collection = db["users"]
tracks_collection = db["tracks"]


# ============================================================
# Get Tracks From MongoDB
# ============================================================

def get_tracks() -> list[str]:
    """
    Get unique track names from MongoDB.
    """

    tracks = [
        doc["track"]
        for doc in tracks_collection.find(
            {},
            {
                "_id": 0,
                "track": 1,
            },
        )
        if doc.get("track")
    ]

    # Remove duplicates while preserving order
    return list(dict.fromkeys(tracks))


tracks = get_tracks()


# ============================================================
# Attendance Calculation
# ============================================================

def calculate_attendance(
    total: int,
    present: int,
) -> dict:
    """
    Calculate attendance percentage and 75% requirement.

    If attendance >= 75%:
        can_miss = maximum future classes that can be missed
                   while remaining at or above 75%.

    If attendance < 75%:
        must_attend = number of consecutive future classes
                      that must be attended to reach 75%.
    """

    # Prevent invalid values
    total = max(0, total)
    present = max(0, present)

    # Present cannot be greater than total
    present = min(present, total)

    # No classes yet
    if total == 0:
        return {
            "percentage": 0.0,
            "can_miss": 0,
            "must_attend": 0,
            "attendance_status": "none",
        }

    # --------------------------------------------------------
    # Current percentage
    # --------------------------------------------------------

    percentage = (present / total) * 100

    # --------------------------------------------------------
    # At or above 75%
    # --------------------------------------------------------

    if percentage >= 75:

        can_miss = math.floor(
            present / 0.75 - total
        )

        return {
            "percentage": round(percentage, 2),
            "can_miss": max(0, can_miss),
            "must_attend": 0,
            "attendance_status": "safe",
        }

    # --------------------------------------------------------
    # Below 75%
    # --------------------------------------------------------

    must_attend = math.ceil(
        (0.75 * total - present) / 0.25
    )

    return {
        "percentage": round(percentage, 2),
        "can_miss": 0,
        "must_attend": max(0, must_attend),
        "attendance_status": "shortage",
    }


# ============================================================
# GitHub Workflow
# ============================================================

async def trigger_scrape_workflow():

    if not GITHUB_PAT:
        logger.error(
            "GITHUB_PAT not set — cannot trigger workflow"
        )
        return

    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/actions/workflows/"
        f"{WORKFLOW_FILE}/dispatches"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:

        async with httpx.AsyncClient(timeout=10) as http_client:

            response = await http_client.post(
                url,
                headers=headers,
                json={"ref": "main"},
            )

        if response.status_code == 204:

            logger.info(
                "✓ Triggered scrape workflow successfully"
            )

        else:

            logger.error(
                f"✗ Failed to trigger workflow: "
                f"{response.status_code} "
                f"{response.text}"
            )

    except httpx.TimeoutException as error:

        logger.error(
            f"✗ Timeout while triggering workflow: {error}"
        )

    except httpx.HTTPError as error:

        logger.error(
            f"✗ Exception while triggering workflow: {error}"
        )


# ============================================================
# Scheduler
# ============================================================

scheduler = AsyncIOScheduler(
    timezone=ZoneInfo("Asia/Kolkata")
)


# ============================================================
# FastAPI Lifespan
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    # --------------------------------------------------------
    # Startup
    # --------------------------------------------------------

    try:

        client.admin.command("ping")

        logger.info(
            "✓ Connected to MongoDB Atlas"
        )

        logger.info(
            "✓ MongoDB connection pool ready "
            "(min=5, max=50)"
        )

        # Refresh tracks at startup
        global tracks
        tracks = get_tracks()

        logger.info(
            f"✓ Loaded {len(tracks)} track(s) from MongoDB"
        )

        for track in tracks:
            logger.info(
                f"    - {track}"
            )

    except Exception as error:

        logger.error(
            f"✗ MongoDB connection failed: {error}"
        )

    # --------------------------------------------------------
    # Scheduler
    # --------------------------------------------------------

    scheduler.add_job(
        trigger_scrape_workflow,
        CronTrigger(
            hour="11-18",
            minute=0,
            timezone=ZoneInfo("Asia/Kolkata"),
        ),
        id="trigger_scrape",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.start()

    logger.info(
        "✓ Scheduler started — "
        "will trigger scrape hourly, "
        "11:00-18:00 IST"
    )

    yield

    # --------------------------------------------------------
    # Shutdown
    # --------------------------------------------------------

    scheduler.shutdown()

    client.close()

    logger.info(
        "✓ MongoDB connection pool closed"
    )

    logger.info(
        "✓ Scheduler shut down"
    )


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="Attendance Dashboard",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
)

templates = Jinja2Templates(
    directory="templates"
)


# ============================================================
# Dashboard
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
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
        ).sort(
            "username",
            1,
        )
    )

    # --------------------------------------------------------
    # Calculate attendance information
    # --------------------------------------------------------

    for user in users:

        subjects = user.get("subjects", [])

        for subject in subjects:

            total = int(subject.get("total_attendances", 0))
            present = int(subject.get("total_present", 0))

            attendance = calculate_attendance(
                total=total,
                present=present,
            )

            subject.update(attendance)

        # ========================================================
        # Overall 75% calculation
        # ========================================================

        stats = user.get("statistics", {})

        overall_total = int(
            stats.get("total_attendances", 0)
        )

        overall_present = int(
            stats.get("total_present", 0)
        )

        overall_attendance = calculate_attendance(
            total=overall_total,
            present=overall_present,
        )

        user["overall_75"] = overall_attendance

        # ========================================================
        # Average 75% calculation
        # ========================================================

        if subjects:
            average_percentage = sum(
                float(subject.get("percentage", 0))
                for subject in subjects
            ) / len(subjects)

            # Convert average percentage into equivalent
            # attendance calculation
            average_total = 100
            average_present = average_percentage

            if average_percentage >= 75:
                average_can_miss = math.floor(
                    average_percentage / 0.75 - average_total
                )

                user["average_75"] = {
                    "percentage": round(average_percentage, 2),
                    "can_miss": max(0, average_can_miss),
                    "must_attend": 0,
                    "attendance_status": "safe",
                }
            else:
                average_must_attend = math.ceil(
                    (0.75 * average_total - average_present) / 0.25
                )

                user["average_75"] = {
                    "percentage": round(average_percentage, 2),
                    "can_miss": 0,
                    "must_attend": max(0, average_must_attend),
                    "attendance_status": "shortage",
                }

        else:
            user["average_75"] = {
                "percentage": 0,
                "can_miss": 0,
                "must_attend": 0,
                "attendance_status": "none",
            }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "users": users,
        },
    )


# ============================================================
# Scheduler Status
# ============================================================

@app.get("/scheduler-status")
def scheduler_status():

    jobs = scheduler.get_jobs()

    return {
        "running": scheduler.running,
        "jobs": [
            {
                "id": job.id,
                "next_run": str(
                    job.next_run_time
                ),
            }
            for job in jobs
        ],
    }


# ============================================================
# Manual Scrape Trigger
# ============================================================

@app.post("/trigger-scrape-now")
async def trigger_scrape_now():

    await trigger_scrape_workflow()

    return {
        "status":
        "triggered — check GitHub Actions tab"
    }


# ============================================================
# Add User
# ============================================================

@app.post("/add_user")
async def add_user(

    email: str = Form(...),

    password: str = Form(...),

    track: str = Form(
        ...,
        json_schema_extra={
            "enum": tracks
        },
    ),

    status: Literal[
        "active",
        "inactive",
    ] = Form("active"),
):

    # --------------------------------------------------------
    # Validate track
    # --------------------------------------------------------

    if track not in tracks:

        raise HTTPException(
            status_code=400,
            detail=f"Invalid track: {track}",
        )

    # --------------------------------------------------------
    # Create user
    # --------------------------------------------------------

    user = User(
        email=email,
        password=password,
        track=track,
        status=status,
    )

    # --------------------------------------------------------
    # Save user
    # --------------------------------------------------------

    users_collection.insert_one(
        user.model_dump()
    )

    return {
        "status": "user added"
    }


# ============================================================
# Catch All
# ============================================================

@app.get(
    "/{full_path:path}"
)
async def catch_all(
    full_path: str,
):

    return RedirectResponse(
        "/",
        status_code=302,
    )
