# Backend — ORNG LED CONTROL

Python 3.12 + FastAPI application package.

## Commands

```powershell
# from repository root after scripts\bootstrap.ps1
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m uvicorn orng_led.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Health endpoint: `GET /api/health`.
