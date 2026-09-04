import os
from typing import List, Dict, Optional
from datetime import datetime

import bcrypt
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt

# ---------------------------------------------------------------------------
# FASTAPI APP INITIALISIERUNG & CORS
# ---------------------------------------------------------------------------
app = FastAPI(title="RemindMe Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# KONFIGURATION & SICHERHEIT
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "remindme_super_secret_key_12345")
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# ---------------------------------------------------------------------------
# IN-MEMORY DATENBANKEN
# ---------------------------------------------------------------------------
db_users: Dict[str, str] = {}
db_tasks: List[dict] = []
task_id_counter = 1
db_comments: Dict[str, List[dict]] = {}

db_groups: Dict[str, List[str]] = {
    "MTA (Maschinentechnische Abteilung)": ["Michael Klärner", "Daniel Lehmann"]
}

# ---------------------------------------------------------------------------
# PYDANTIC MODELLE
# ---------------------------------------------------------------------------
class UserRegister(BaseModel):
    username: str
    password: str

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    assignee: Optional[str] = "Unassigned"
    deadline: Optional[str] = ""

class TaskStatusUpdate(BaseModel):
    status: str

class CommentCreate(BaseModel):
    message: str

class GroupModel(BaseModel):
    name: str
    members: List[str]

# ---------------------------------------------------------------------------
# HELFER-FUNKTIONEN (DIREKTES BCRYPT OHNE PASSLIB)
# ---------------------------------------------------------------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Schneidet Passwörter sicherheitshalber bei 72 Bytes für bcrypt ab
    pw_bytes = plain_password.encode('utf-8')[:72]
    hash_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(pw_bytes, hash_bytes)

def get_password_hash(password: str) -> str:
    pw_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode('utf-8')

def create_access_token(data: dict):
    to_encode = data.copy()
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige Anmeldedaten oder Sitzung abgelaufen.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    if username not in db_users:
        raise credentials_exception
    return username

# ---------------------------------------------------------------------------
# ENDPUNKTE
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "RemindMe Backend läuft!"}

@app.post("/register")
def register(user: UserRegister):
    clean_user = user.username.strip()
    if not clean_user or not user.password:
        raise HTTPException(status_code=400, detail="Benutzername und Passwort erforderlich.")
        
    if clean_user in db_users:
        raise HTTPException(status_code=400, detail="Benutzername existiert bereits.")
        
    db_users[clean_user] = get_password_hash(user.password)
    return {"message": "Benutzer erfolgreich registriert."}

@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    clean_user = form_data.username.strip()
    hashed_pw = db_users.get(clean_user)
    if not hashed_pw or not verify_password(form_data.password, hashed_pw):
        raise HTTPException(status_code=400, detail="Benutzername oder Passwort falsch.")
    access_token = create_access_token(data={"sub": clean_user})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/groups")
def get_groups(current_user: str = Depends(get_current_user)):
    return db_groups

@app.post("/groups")
def save_group(group: GroupModel, current_user: str = Depends(get_current_user)):
    clean_name = group.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Gruppenname darf nicht leer sein.")
    db_groups[clean_name] = group.members
    return {"status": "ok", "message": f"Gruppe '{clean_name}' zentral gespeichert."}

@app.get("/tasks")
def get_tasks(current_user: str = Depends(get_current_user)):
    return db_tasks

@app.post("/tasks")
def create_task(task: TaskCreate, current_user: str = Depends(get_current_user)):
    global task_id_counter
    new_task = {
        "id": task_id_counter,
        "title": task.title,
        "description": task.description,
        "assignee": task.assignee,
        "deadline": task.deadline,
        "status": "Offen",
        "created_by": current_user
    }
    db_tasks.append(new_task)
    task_id_counter += 1
    return new_task

@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(task_id: int, status_update: TaskStatusUpdate, current_user: str = Depends(get_current_user)):
    for task in db_tasks:
        if task["id"] == task_id:
            task["status"] = status_update.status
            return task
    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, current_user: str = Depends(get_current_user)):
    global db_tasks
    for task in db_tasks:
        if task["id"] == task_id:
            if task["created_by"] != current_user:
                raise HTTPException(status_code=403, detail="Nur der Ersteller darf diese Aufgabe löschen.")
            db_tasks = [t for t in db_tasks if t["id"] != task_id]
            if str(task_id) in db_comments:
                del db_comments[str(task_id)]
            return {"message": "Aufgabe gelöscht."}
    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

@app.get("/tasks/{task_id}/comments")
def get_comments(task_id: str, current_user: str = Depends(get_current_user)):
    return db_comments.get(str(task_id), [])

@app.post("/tasks/{task_id}/comments")
def add_comment(task_id: str, comment: CommentCreate, current_user: str = Depends(get_current_user)):
    str_id = str(task_id)
    now_str = datetime.now().strftime("%H:%M:%S")
    comment_entry = {
        "author": current_user,
        "message": comment.message,
        "timestamp": now_str
    }
    if str_id not in db_comments:
        db_comments[str_id] = []
    db_comments[str_id].append(comment_entry)
    return comment_entry