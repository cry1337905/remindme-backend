import os
import secrets
import shutil
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import bcrypt
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# SUPABASE KONFIGURATION (SICHER ÜBER UMGEBUNGSVARIABLEN)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ligaopexwxgoirrpiuwi.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_KEY:
    # Fängt den Fehler ab, falls die Variable auf Render noch nicht gesetzt wurde
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

class TaskStatusModel(BaseModel):
    status: str

class CommentModel(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# AUTHENTIFIZIERUNGS-DEPENDENCY
# ---------------------------------------------------------------------------
def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Fehlender oder ungültiger Token")
    token = authorization.split(" ")[1]
    if token not in TOKENS:
        raise HTTPException(status_code=401, detail="Token ungültig oder abgelaufen")
    return TOKENS[token]


# ---------------------------------------------------------------------------
# AUTH ENDPUNKTE (MIT SUPABASE VERNETZT)
# ---------------------------------------------------------------------------
@app.post("/register")
def register(data: RegisterModel):
    email = data.email.strip().lower()

    # Prüfen, ob User bereits in Supabase existiert
    res = supabase.table("users").select("*").eq("email", email).execute()
    if res.data:
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert")

    company_code = data.company_code
    if data.company_name:
        company_code = f"COMP-{secrets.token_hex(2).upper()}"
        supabase.table("companies").insert({"code": company_code, "name": data.company_name}).execute()
    elif company_code:
        comp_res = supabase.table("companies").select("*").eq("code", company_code).execute()
        if not comp_res.data:
            raise HTTPException(status_code=400, detail="Ungültiger Firmen-Code")

    # Passwort mit bcrypt verschlüsseln
    hashed_pwd = bcrypt.hashpw(data.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    # In Supabase-Tabelle "users" speichern
    supabase.table("users").insert({
        "email": email,
        "username": data.username,
        "password": hashed_pwd,
        "company_code": company_code
    }).execute()

    return {"message": "Erfolgreich registriert", "company_code": company_code}

@app.post("/login")
def login(data: LoginModel):
    email = data.email.strip().lower()

    # User aus Supabase abfragen
    res = supabase.table("users").select("*").eq("email", email).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="E-Mail oder Passwort falsch")

    user = res.data[0]
    db_password = user["password"]

    # Prüft gehashte Passwörter sowie ältere Klartext-Einträge
    is_valid = False
    if db_password.startswith("$2b$") or db_password.startswith("$2a$"):
        is_valid = bcrypt.checkpw(data.password.encode('utf-8'), db_password.encode('utf-8'))
    else:
        is_valid = (db_password == data.password)

    if not is_valid:
        raise HTTPException(status_code=400, detail="E-Mail oder Passwort falsch")

    token = secrets.token_hex(16)
    TOKENS[token] = email
    return {"access_token": token, "token_type": "bearer"}

@app.post("/forgot-password")
def forgot_password(data: ForgotPasswordModel):
    email = data.email.strip().lower()
    res = supabase.table("users").select("*").eq("email", email).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="E-Mail nicht gefunden")
    
    code = f"{secrets.randbelow(1000000):06d}"
    supabase.table("reset_codes").upsert({"email": email, "code": code}).execute()
    print(f"[RESET CODE] Für {email}: {code}")
    return {"message": "Code gesendet"}

@app.post("/reset-password")
def reset_password(data: ResetPasswordModel):
    email = data.email.strip().lower()
    code_res = supabase.table("reset_codes").select("*").eq("email", email).execute()
    
    if not code_res.data or code_res.data[0]["code"] != data.code:
        raise HTTPException(status_code=400, detail="Ungültiger Code")
    
    hashed_pwd = bcrypt.hashpw(data.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    supabase.table("users").update({"password": hashed_pwd}).eq("email", email).execute()
    supabase.table("reset_codes").delete().eq("email", email).execute()
    
    return {"message": "Passwort geändert"}


# ---------------------------------------------------------------------------
# TASK & CHAT ENDPUNKTE
# ---------------------------------------------------------------------------
TASKS = []
COMMENTS = {}
GROUPS = {}

@app.get("/groups")
def get_groups(user: str = Depends(get_current_user)):
    return GROUPS

@app.post("/groups")
def save_group(data: GroupModel, user: str = Depends(get_current_user)):
    GROUPS[data.name] = data.members
    return {"message": "Gruppe gespeichert"}

@app.get("/tasks")
def get_tasks(user: str = Depends(get_current_user)):
    return TASKS

@app.post("/tasks")
def create_task(data: TaskCreateModel, user: str = Depends(get_current_user)):
    task_id = str(len(TASKS) + 1)
    new_task = {
        "id": task_id,
        "title": data.title,
        "description": data.description,
        "assignee": data.assignee,
        "deadline": data.deadline,
        "status": "Offen",
        "created_by": user
    }
    TASKS.append(new_task)
    COMMENTS[task_id] = []
    return new_task

@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(task_id: str, data: TaskStatusModel, user: str = Depends(get_current_user)):
    for task in TASKS:
        if str(task["id"]) == str(task_id):
            task["status"] = data.status
            return task
    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")

@app.get("/tasks/{task_id}/comments")
def get_comments(task_id: str, user: str = Depends(get_current_user)):
    return COMMENTS.get(str(task_id), [])

@app.post("/tasks/{task_id}/comments")
def add_comment(task_id: str, data: CommentModel, user: str = Depends(get_current_user)):
    str_id = str(task_id)
    if str_id not in COMMENTS:
        COMMENTS[str_id] = []
    
    comment_entry = {
        "author": user,
        "message": data.message,
    }
    COMMENTS[str_id].append(comment_entry)
    return {"message": "Kommentar hinzugefügt"}

@app.post("/tasks/{task_id}/upload")
def upload_file(task_id: str, file: UploadFile = File(...), user: str = Depends(get_current_user)):
    str_id = str(task_id)
    safe_filename = f"{secrets.token_hex(4)}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    download_url = f"https://remindme-backend1.onrender.com/files/{safe_filename}"
    chat_message = f"FILE::{file.filename}::{download_url}"

    if str_id not in COMMENTS:
        COMMENTS[str_id] = []
    
    COMMENTS[str_id].append({
        "author": user,
        "message": chat_message
    })

    return {"message": "Datei hochgeladen", "url": download_url}

@app.get("/files/{filename}")
def download_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Datei nicht gefunden")

@app.get("/")
def root():
    return {"status": "Online", "app": "Remind Me Backend"}