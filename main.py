import os
import secrets
import shutil
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# SUPABASE KONFIGURATION
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ligaopexwxgoirrpiuwi.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_KEY:
    print("[WARNUNG] SUPABASE_KEY ist nicht in den Umgebungsvariablen gesetzt!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY or "DUMMY_KEY")

app = FastAPI(title="Remind Me Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# In-Memory Tokens für aktive Sitzungen
TOKENS = {}


# ---------------------------------------------------------------------------
# MODELS (PYDANTIC)
# ---------------------------------------------------------------------------
class RegisterModel(BaseModel):
    email: EmailStr
    username: str
    password: str
    company_name: Optional[str] = None
    company_code: Optional[str] = None

class LoginModel(BaseModel):
    email: EmailStr
    password: str

class ForgotPasswordModel(BaseModel):
    email: EmailStr

class ResetPasswordModel(BaseModel):
    email: EmailStr
    code: str
    new_password: str

class GroupModel(BaseModel):
    name: str
    members: List[str]

class TaskCreateModel(BaseModel):
    title: str
    description: str
    assignee: str
    deadline: str
    position: Optional[int] = 0

class TaskStatusModel(BaseModel):
    status: str

class TaskPositionModel(BaseModel):
    position: int

class CommentModel(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# AUTHENTIFIZIERUNGS-DEPENDENCY & HILFSFUNKTIONEN
# ---------------------------------------------------------------------------
def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Fehlender oder ungültiger Token")
    token = authorization.split(" ")[1]
    if token not in TOKENS:
        raise HTTPException(status_code=401, detail="Token ungültig oder abgelaufen")
    return TOKENS[token]

def get_user_company_id(email: str) -> Optional[str]:
    res = supabase.table("users").select("company_id").eq("email", email).execute()
    if res.data and len(res.data) > 0:
        return res.data[0].get("company_id")
    return None


# ---------------------------------------------------------------------------
# AUTH ENDPUNKTE
# ---------------------------------------------------------------------------
@app.post("/register")
def register(data: RegisterModel):
    email = data.email.strip().lower()

    company_code = data.company_code
    if data.company_name:
        company_code = f"COMP-{secrets.token_hex(2).upper()}"
        supabase.table("companies").insert({"code": company_code, "name": data.company_name}).execute()
    elif company_code:
        comp_res = supabase.table("companies").select("*").eq("code", company_code).execute()
        if not comp_res.data:
            raise HTTPException(status_code=400, detail="Ungültiger Firmen-Code")

    try:
        auth_response = supabase.auth.sign_up({
            "email": email,
            "password": data.password,
            "options": {
                "data": {
                    "username": data.username,
                    "company_code": company_code
                }
            }
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Registrierung fehlgeschlagen: {str(e)}")

    user_res = supabase.table("users").select("*").eq("email", email).execute()
    if not user_res.data:
        supabase.table("users").insert({
            "email": email,
            "username": data.username,
            "company_id": company_code
        }).execute()

    return {"message": "Erfolgreich registriert", "company_code": company_code}


@app.post("/login")
def login(data: LoginModel):
    email = data.email.strip().lower()

    try:
        auth_response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": data.password
        })
        
        if not auth_response.user:
            raise HTTPException(status_code=400, detail="E-Mail oder Passwort falsch")

    except Exception:
        raise HTTPException(status_code=400, detail="E-Mail oder Passwort falsch")

    token = secrets.token_hex(16)
    TOKENS[token] = email
    return {"access_token": token, "token_type": "bearer"}


@app.post("/forgot-password")
def forgot_password(data: ForgotPasswordModel):
    email = data.email.strip().lower()
    try:
        supabase.auth.reset_password_email(email)
    except Exception:
        raise HTTPException(status_code=404, detail="E-Mail nicht gefunden oder Fehler beim Senden")
    
    return {"message": "Passwort-Zurücksetzen-E-Mail gesendet"}


@app.post("/reset-password")
def reset_password(data: ResetPasswordModel):
    raise HTTPException(
        status_code=400, 
        detail="Bitte nutze den Link in der E-Mail zum Zurücksetzen des Passworts."
    )


# ---------------------------------------------------------------------------
# GRUPPEN (SUPABASE INTEGRATION)
# ---------------------------------------------------------------------------
@app.get("/groups")
def get_groups(user: str = Depends(get_current_user)):
    res = supabase.table("groups").select("*").execute()
    if not res.data:
        return []
    return res.data

@app.post("/groups")
def save_group(data: GroupModel, user: str = Depends(get_current_user)):
    company_id = get_user_company_id(user)
    payload = {
        "name": data.name,
        "members": data.members,
    }
    if company_id:
        payload["company_id"] = company_id

    res = supabase.table("groups").upsert(payload, on_conflict="name").execute()
    return {"message": "Gruppe in Supabase gespeichert", "data": res.data}

@app.delete("/groups/{name}")
def delete_group(name: str, user: str = Depends(get_current_user)):
    supabase.table("groups").delete().eq("name", name).execute()
    return {"message": f"Gruppe {name} gelöscht"}


# ---------------------------------------------------------------------------
# TASKS (SUPABASE INTEGRATION + DRAG & DROP SUPPORT)
# ---------------------------------------------------------------------------
@app.get("/tasks")
def get_tasks(user: str = Depends(get_current_user)):
    # Sortierung nach Position für Drag & Drop Reihenfolge
    res = supabase.table("tasks").select("*").order("position", desc=False).execute()
    return res.data or []

@app.post("/tasks")
def create_task(data: TaskCreateModel, user: str = Depends(get_current_user)):
    company_id = get_user_company_id(user)
    payload = {
        "title": data.title,
        "description": data.description,
        "assignee": data.assignee,
        "deadline": data.deadline,
        "status": "Offen",
        "position": data.position,
        "created_by": user
    }
    if company_id:
        payload["company_id"] = company_id

    res = supabase.table("tasks").insert(payload).execute()
    return res.data[0] if res.data else payload

@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(task_id: str, data: TaskStatusModel, user: str = Depends(get_current_user)):
    res = supabase.table("tasks").update({"status": data.status}).eq("id", task_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    return res.data[0]

@app.patch("/tasks/{task_id}/position")
def update_task_position(task_id: str, data: TaskPositionModel, user: str = Depends(get_current_user)):
    res = supabase.table("tasks").update({"position": data.position}).eq("id", task_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    return {"message": "Position aktualisiert", "data": res.data[0]}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: str = Depends(get_current_user)):
    supabase.table("tasks").delete().eq("id", task_id).execute()
    return {"message": "Aufgabe gelöscht"}


# ---------------------------------------------------------------------------
# CHAT & DATEI-UPLOAD (DRAG & DROP DATEIEN)
# ---------------------------------------------------------------------------
@app.get("/tasks/{task_id}/comments")
def get_comments(task_id: str, user: str = Depends(get_current_user)):
    res = supabase.table("comments").select("*").eq("task_id", task_id).execute()
    return res.data or []

@app.post("/tasks/{task_id}/comments")
def add_comment(task_id: str, data: CommentModel, user: str = Depends(get_current_user)):
    payload = {
        "task_id": task_id,
        "author": user,
        "message": data.message
    }
    supabase.table("comments").insert(payload).execute()
    return {"message": "Kommentar in Supabase gespeichert"}

@app.post("/tasks/{task_id}/upload")
def upload_file(task_id: str, file: UploadFile = File(...), user: str = Depends(get_current_user)):
    safe_filename = f"{secrets.token_hex(4)}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    download_url = f"https://remindme-backend1.onrender.com/files/{safe_filename}"
    chat_message = f"FILE::{file.filename}::{download_url}"

    payload = {
        "task_id": task_id,
        "author": user,
        "message": chat_message
    }
    supabase.table("comments").insert(payload).execute()

    return {"message": "Datei hochgeladen", "url": download_url}

@app.get("/files/{filename}")
def download_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Datei nicht gefunden")

@app.get("/")
def root():
    return {"status": "Online", "app": "Remind Me Backend mit Supabase & Drag-and-Drop Support"}