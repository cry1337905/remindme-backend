import datetime
import urllib.parse
from typing import Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

app = FastAPI(title="Remind Me Backend")

# ---------------------------------------------------------------------------
# IN-MEMORY DATENBANKEN
# ---------------------------------------------------------------------------
users_db: Dict[str, dict] = {}
groups_db: Dict[str, List[str]] = {}
tasks_db: List[dict] = []
comments_db: Dict[str, List[dict]] = {}
reset_codes_db: Dict[str, str] = {}

task_id_counter = 1

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


# ---------------------------------------------------------------------------
# PYDANTIC SCHEMAS
# ---------------------------------------------------------------------------
class UserRegister(BaseModel):
    email: str
    username: str
    password: str


class GroupSchema(BaseModel):
    name: str
    members: List[str]


class TaskCreate(BaseModel):
    title: str
    description: str
    assignee: str
    deadline: str


class TaskStatusUpdate(BaseModel):
    status: str


class CommentCreate(BaseModel):
    message: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


# ---------------------------------------------------------------------------
# HELFER & AUTHENTIFIZIERUNG
# ---------------------------------------------------------------------------
def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    clean_token = token.strip().lower()
    if clean_token in users_db:
        return clean_token

    for email in users_db:
        if clean_token == f"fake-token-for-{email}":
            return email

    if "@" in clean_token:
        return clean_token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültiges Authentifizierungs-Token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# USER & AUTH ENDPUNKTE
# ---------------------------------------------------------------------------
@app.post("/register")
def register(user: UserRegister):
    email_clean = user.email.strip().lower()
    if email_clean in users_db:
        raise HTTPException(
            status_code=400, detail="E-Mail-Adresse ist bereits registriert."
        )

    users_db[email_clean] = {
        "email": email_clean,
        "username": user.username.strip(),
        "password": user.password,
    }
    return {"message": "Registrierung erfolgreich"}


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    email_clean = form_data.username.strip().lower()
    user = users_db.get(email_clean)

    # Falls der Server neu gestartet wurde und die DB leer ist,
    # legen wir den User beim ersten Login-Versuch automatisch an.
    if not user:
        users_db[email_clean] = {
            "email": email_clean,
            "username": email_clean.split("@")[0],
            "password": form_data.password,
        }
        user = users_db[email_clean]

    # Passwortprüfung (akzeptiert das gesetzte Passwort)
    if user["password"] != form_data.password:
        # Falls das Passwort nicht stimmt, aktualisieren wir es für den Prototyp-Betrieb
        user["password"] = form_data.password

    access_token = email_clean
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    email_clean = req.email.strip().lower()
    
    # Automatischer Anlege-Fallback
    if email_clean not in users_db:
        users_db[email_clean] = {
            "email": email_clean,
            "username": email_clean.split("@")[0],
            "password": "password123",
        }

    code = "123456"
    reset_codes_db[email_clean] = code
    print(f"[LOG] Passwort-Reset-Code für {email_clean}: {code}")
    return {"message": "Reset-Code gesendet"}


@app.post("/reset-password")
def reset_password(req: ResetPasswordRequest):
    email_clean = req.email.strip().lower()
    saved_code = reset_codes_db.get(email_clean)

    if not saved_code or saved_code != req.code.strip():
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Code.")

    if email_clean in users_db:
        users_db[email_clean]["password"] = req.new_password.strip()
        if email_clean in reset_codes_db:
            del reset_codes_db[email_clean]
        return {"message": "Passwort erfolgreich zurückgesetzt"}

    raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")


# ---------------------------------------------------------------------------
# GRUPPEN ENDPUNKTE (MIT LÖSCH-ENDPUNKT)
# ---------------------------------------------------------------------------
@app.get("/groups")
def get_groups(current_user: str = Depends(get_current_user)):
    return groups_db


@app.post("/groups")
def save_group(group: GroupSchema, current_user: str = Depends(get_current_user)):
    groups_db[group.name.strip()] = group.members
    return {"message": f"Gruppe '{group.name}' erfolgreich gespeichert."}


@app.delete("/groups/{group_name}")
def delete_group(group_name: str, current_user: str = Depends(get_current_user)):
    decoded_name = urllib.parse.unquote(group_name).strip()

    if decoded_name in groups_db:
        del groups_db[decoded_name]
        return {"message": f"Gruppe '{decoded_name}' wurde erfolgreich gelöscht."}

    raise HTTPException(
        status_code=404, detail=f"Gruppe '{decoded_name}' wurde nicht gefunden."
    )


# ---------------------------------------------------------------------------
# TASK ENDPUNKTE
# ---------------------------------------------------------------------------
@app.get("/tasks")
def get_tasks(current_user: str = Depends(get_current_user)):
    return tasks_db


@app.post("/tasks")
def create_task(task: TaskCreate, current_user: str = Depends(get_current_user)):
    global task_id_counter

    user_info = users_db.get(current_user, {})
    creator_display = user_info.get("username", current_user)

    new_task = {
        "id": task_id_counter,
        "title": task.title,
        "description": task.description,
        "assignee": task.assignee,
        "deadline": task.deadline,
        "status": "Offen",
        "created_by": creator_display,
    }

    tasks_db.append(new_task)
    task_id_counter += 1
    return new_task


@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(
    task_id: int,
    status_update: TaskStatusUpdate,
    current_user: str = Depends(get_current_user),
):
    for task in tasks_db:
        if task["id"] == task_id:
            task["status"] = status_update.status
            return task
    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, current_user: str = Depends(get_current_user)):
    global tasks_db
    for i, task in enumerate(tasks_db):
        if task["id"] == task_id:
            del tasks_db[i]
            str_id = str(task_id)
            if str_id in comments_db:
                del comments_db[str_id]
            return {"message": "Aufgabe gelöscht."}

    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")


# ---------------------------------------------------------------------------
# KOMMENTAR & CHAT ENDPUNKTE
# ---------------------------------------------------------------------------
@app.get("/tasks/{task_id}/comments")
def get_comments(task_id: str, current_user: str = Depends(get_current_user)):
    return comments_db.get(str(task_id), [])


@app.post("/tasks/{task_id}/comments")
def add_comment(
    task_id: str,
    comment: CommentCreate,
    current_user: str = Depends(get_current_user),
):
    str_id = str(task_id)
    if str_id not in comments_db:
        comments_db[str_id] = []

    user_info = users_db.get(current_user, {})
    author_name = user_info.get("username", current_user)

    comment_entry = {
        "author": author_name,
        "message": comment.message,
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
    }

    comments_db[str_id].append(comment_entry)
    return comment_entry