import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from store import STEPS, ShiftStore


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "shift.sqlite3"))
STORE = ShiftStore(DB_PATH)
MAX_MESSAGE_LENGTH = 300
MINSK_TIME = timezone(timedelta(hours=3))
ERRORS = {
    "note": "Опиши, что мешает выполнить шаг. Достаточно одного предложения.",
    "answer": "Напиши новичку, что делать дальше.",
    "length": "Сократи сообщение до 300 знаков.",
    "open": "У этого шага уже есть открытый запрос. Дождись ответа наставника.",
    "done": "Этот шаг уже отмечен выполненным.",
    "closed": "Запрос уже закрыт или недоступен.",
}


@asynccontextmanager
async def lifespan(app):
    STORE.initialize()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


def local_time(value):
    return datetime.fromisoformat(value).astimezone(MINSK_TIME).strftime("%d.%m в %H:%M")


templates.env.filters["local_time"] = local_time


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/health")
def health():
    return PlainTextResponse("ok")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"step_count": len(STEPS)})


@app.post("/demo")
def start_demo():
    return redirect(STORE.create_demo(), "newcomer")


def redirect(demo_id, role, error=None, anchor=""):
    path = f"/demo/{demo_id}/{role}"
    if error:
        path += "?" + urlencode({"error": error})
    if anchor:
        path += "#" + anchor
    return RedirectResponse(path, status_code=303)


def get_shift(demo_id):
    shift = STORE.shift(demo_id)
    if shift is None:
        raise HTTPException(404)
    return shift


def ensure_step(demo_id, number):
    if not 1 <= number <= len(STEPS) or not STORE.demo_exists(demo_id):
        raise HTTPException(404)


def render_shift(request, demo_id, role, error):
    return templates.TemplateResponse(
        request,
        f"{role}.html",
        {
            "shift": get_shift(demo_id),
            "demo_path": f"/demo/{demo_id}",
            "role": role,
            "error": ERRORS.get(error),
            "max_message_length": MAX_MESSAGE_LENGTH,
            "step_titles": dict(enumerate((step[0] for step in STEPS), 1)),
        },
    )


@app.get("/demo/{demo_id}/newcomer")
def newcomer(request: Request, demo_id: str, error: str = ""):
    return render_shift(request, demo_id, "newcomer", error)


@app.get("/demo/{demo_id}/mentor")
def mentor(request: Request, demo_id: str, error: str = ""):
    return render_shift(request, demo_id, "mentor", error)


@app.post("/demo/{demo_id}/newcomer/{number}/complete")
def complete(demo_id: str, number: int):
    ensure_step(demo_id, number)
    error = STORE.complete_step(demo_id, number)
    return redirect(demo_id, "newcomer", error)


@app.post("/demo/{demo_id}/newcomer/{number}/ask")
def ask(demo_id: str, number: int, note: str = Form("")):
    ensure_step(demo_id, number)
    note = note.strip()
    if not note:
        error = "note"
    elif len(note) > MAX_MESSAGE_LENGTH:
        error = "length"
    else:
        error = STORE.request_help(demo_id, number, note)
    return redirect(demo_id, "newcomer", error, f"step-{number}" if not error else "")


@app.post("/demo/{demo_id}/mentor/{request_id}/close")
def close(demo_id: str, request_id: int, answer: str = Form("")):
    if not 1 <= request_id < 2**63 or not STORE.demo_exists(demo_id):
        raise HTTPException(404)
    answer = answer.strip()
    if not answer:
        error = "answer"
    elif len(answer) > MAX_MESSAGE_LENGTH:
        error = "length"
    else:
        error = None if STORE.close_request(demo_id, request_id, answer) else "closed"
    return redirect(demo_id, "mentor", error)


@app.exception_handler(404)
def not_found(request: Request, exc):
    return templates.TemplateResponse(request, "missing.html", status_code=404)
