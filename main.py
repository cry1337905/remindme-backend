import os
import secrets
import shutil
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Remind Me Backend")

# CORS-Einstellungen für den Zugriff erlauben
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ordner für Datei-Uploads anlegen
UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# IN-MEMORY DATENBANK (SPEICHER)
# ---------------------------------------------------------------------------
USERS = {}            # email -> {username, password, company_code}
COMPANIES = {}        # company_code -> name
TOKENS = {}           # token -> email
GROUPS = {}           # group_name -> list of members
TASKS = []            # list of task dicts
COMMENTS = {}         # task_id (str) -> list of comment dicts
RESET_CODES = {}      # email -> code

# Sample-Daten zur Initialisierung (optional)
COMPANIES["COMP-1234"] = "Demo Firma"


# ---------------------------------------------------------------------------
# MODELS (PYDANTIC)
# ---------------------------------------------------------------------------
class RegisterModel(BaseModel):
    email: str
    username: str
    password: str
    company_name: Optional[str] = None
    company_code: Optional[str] = None

class LoginModel(BaseModel):
    email: str
    password: str

class ForgotPasswordModel(BaseModel):
    email: str

class ResetPasswordModel(BaseModel):
    email: str
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
# AUTH ENDPUNKTE
# ---------------------------------------------------------------------------
@app.post("/register")
def register(data: RegisterModel):
    email = data.email.strip().lower()
    if email in USERS:
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert")

    company_code = data.company_code
    if data.company_name:
        company_code = f"COMP-{secrets.token_hex(2).upper()}"
        COMPANIES[company_code] = data.company_name
    elif company_code:
        if company_code not in COMPANIES:
            raise HTTPException(status_code=400, detail="Ungültiger Firmen-Code")

    USERS[email] = {
        "username": data.username,
        "password": data.password,
        "company_code": company_code
    }
    return {"message": "Erfolgreich registriert", "company_code": company_code}

@app.post("/login")
def login(data: LoginModel):
    email = data.email.strip().lower()
    user = USERS.get(email)
    if not user or user["password"] != data.password:
        raise HTTPException(status_code=400, detail="E-Mail oder Passwort falsch")

    token = secrets.token_hex(16)
    TOKENS[token] = email
    return {"access_token": token, "token_type": "bearer"}

@app.post("/forgot-password")
def forgot_password(data: ForgotPasswordModel):
    email = data.email.strip().lower()
    if email not in USERS:
        raise HTTPException(status_code=404, detail="E-Mail nicht gefunden")
    
    code = f"{secrets.randbelow(1000000):06d}"
    RESET_CODES[email] = code
    print(f"[RESET CODE] Für {email}: {code}")  # Erscheint in den Render Logs
    return {"message": "Code gesendet"}

@app.post("/reset-password")
def reset_password(data: ResetPasswordModel):
    email = data.email.strip().lower()
    if RESET_CODES.get(email) != data.code:
        raise HTTPException(status_code=400, detail="Ungültiger Code")
    
    if email in USERS:
        USERS[email]["password"] = data.new_password
        del RESET_CODES[email]
        return {"message": "Passwort geändert"}
    raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")


# ---------------------------------------------------------------------------
# GRUPPEN ENDPUNKTE
# ---------------------------------------------------------------------------
@app.get("/groups")
def get_groups(user: str = Depends(get_current_user)):
    return GROUPS

@app.post("/groups")
def save_group(data: GroupModel, user: str = Depends(get_current_user)):
    GROUPS[data.name] = data.members
    return {"message": "Gruppe gespeichert"}

@app.delete("/groups/{name}")
def delete_group_path(name: str, user: str = Depends(get_current_user)):
    if name in GROUPS:
        del GROUPS[name]
        return {"message": "Gruppe gelöscht"}
    raise HTTPException(status_code=404, detail="Gruppe nicht gefunden")


# ---------------------------------------------------------------------------
# AUFGABEN (TASKS) ENDPUNKTE
# ---------------------------------------------------------------------------
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

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: str = Depends(get_current_user)):
    global TASKS
    str_id = str(task_id)
    task = next((t for t in TASKS if str(t["id"]) == str_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    
    TASKS = [t for t in TASKS if str(t["id"]) != str_id]
    if str_id in COMMENTS:
        del COMMENTS[str_id]
    return {"message": "Aufgabe gelöscht"}


# ---------------------------------------------------------------------------
# CHAT & UPLOAD ENDPUNKTE
# ---------------------------------------------------------------------------
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


# Root Route
@app.get("/")
def root():
    return {"status": "Online", "app": "Remind Me Backend"}