import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient

load_dotenv()

# --------------------------------------------------
# Configuration
# --------------------------------------------------

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "attendance_db")

if not MONGODB_URI:
    raise ValueError("MONGODB_URI is not set in .env")


# --------------------------------------------------
# MongoDB Connection Pool
# --------------------------------------------------

client = MongoClient(
    MONGODB_URI,

    # Connection pool
    maxPoolSize=50,
    minPoolSize=5,

    # Connection timeout
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,

    # Socket timeout
    socketTimeoutMS=10000,

    # Keep connections alive
    maxIdleTimeMS=60000,
)

db = client[DB_NAME]

attendance_collection = db["attendance_results"]


# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(
    title="Attendance Dashboard",
    version="1.0.0",
)

templates = Jinja2Templates(directory="templates")


# --------------------------------------------------
# Startup
# --------------------------------------------------

@app.on_event("startup")
def startup_db():
    try:
        client.admin.command("ping")
        print("✓ Connected to MongoDB Atlas")
        print("✓ MongoDB connection pool ready")
        print("  Min connections: 5")
        print("  Max connections: 50")

    except Exception as e:
        print(f"✗ MongoDB connection failed: {e}")


# --------------------------------------------------
# Shutdown
# --------------------------------------------------

@app.on_event("shutdown")
def shutdown_db():
    client.close()
    print("✓ MongoDB connection pool closed")


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
