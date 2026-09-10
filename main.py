import os
import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, status
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ligaopexwxgoirrpiuwi.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_KEY:
    print("WARNUNG: SUPABASE_KEY ist nicht in den Umgebungsvariablen gesetzt!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY or "DUMMY_KEY")

app = FastAPI(title="Remind Me Backend")

# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------
class UserRegister(BaseModel):
    email: EmailStr
    username: str
    password: str
    company_name: Optional[str] = None
    company_code: Optional[str] = None

class UserLogin(BaseModel):
    email: str
    password: str

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    assignee: Optional[str] = "Unassigned"
    deadline: Optional[str] = ""
    project_name: Optional[str] = "Ohne Projekt"

class TaskStatusUpdate(BaseModel):
    status: str

class CommentCreate(BaseModel):
    message: Optional[str] = None
    text: Optional[str] = None
    author: Optional[str] = None

class GroupCreate(BaseModel):
    name: str
    members: List[str]

# ---------------------------------------------------------------------------
# HELFER: SCHAFFT FORMATAUSGLEICH BEI TASK_ID (INT ODER STR)
# ---------------------------------------------------------------------------
def parse_id(val: str):
    try:
        return int(val)
    except ValueError:
        return val

def get_current_user_email(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Fehlender oder ungültiger Authorization-Header."
        )

    token = authorization.split(" ")[1]
    
    try:
        user_response = supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Ungültiges Token.")
        return user_response.user.email
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentifizierungsfehler: {str(e)}")

# ---------------------------------------------------------------------------
# AUTHENTIFIZIERUNG
# ---------------------------------------------------------------------------
@app.post("/register")
def register(user_data: UserRegister):
    try:
        response = supabase.auth.sign_up({
            "email": user_data.email,
            "password": user_data.password,
            "options": {
                "data": {
                    "username": user_data.username,
                    "company_code": user_data.company_code or "DEFAULT"
                }
            }
        })
        return {"message": "Registrierung erfolgreich!", "user": response.user}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/login")
def login(user_data: UserLogin):
    try:
        response = supabase.auth.sign_in_with_password({
            "email": user_data.email,
            "password": user_data.password
        })
        return {
            "access_token": response.session.access_token,
            "token_type": "bearer",
            "user": response.user
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail oder Passwort.")

# ---------------------------------------------------------------------------
# AUFGABEN-ENDPUNKTE
# ---------------------------------------------------------------------------
@app.get("/tasks")
def get_user_tasks(user_email: str = Depends(get_current_user_email)):
    try:
        groups_response = supabase.table("groups").select("name, members").execute()
        user_groups = []
        
        if groups_response.data:
            for group in groups_response.data:
                members = group.get("members", "")
                if user_email in str(members):
                    user_groups.append(group["name"])

        tasks_response = supabase.table("tasks").select("*").execute()
        all_tasks = tasks_response.data or []

        filtered_tasks = []
        for task in all_tasks:
            created_by = task.get("created_by", "")
            assignee = task.get("assignee", "")

            is_creator = created_by == user_email
            is_assignee = assignee and user_email in assignee
            is_group_assignee = assignee in user_groups

            if is_creator or is_assignee or is_group_assignee:
                filtered_tasks.append(task)

        return filtered_tasks

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tasks")
def create_task(task_data: TaskCreate, user_email: str = Depends(get_current_user_email)):
    try:
        new_task = {
            "title": task_data.title,
            "description": task_data.description,
            "assignee": task_data.assignee,
            "deadline": task_data.deadline,
            "project_name": task_data.project_name or "Ohne Projekt",
            "created_by": user_email,
            "status": "Offen"
        }
        response = supabase.table("tasks").insert(new_task).execute()
        return response.data[0] if response.data else new_task
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(task_id: str, status_data: TaskStatusUpdate, user_email: str = Depends(get_current_user_email)):
    try:
        parsed_id = parse_id(task_id)
        existing = supabase.table("tasks").select("*").eq("id", parsed_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

        old_status = existing.data[0].get("status", "Offen")
        new_status = status_data.status

        supabase.table("tasks").update({"status": new_status}).eq("id", parsed_id).execute()

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        comment_entry = {
            "task_id": parsed_id,
            "author": user_email,
            "message": f"STATUSGEÄNDERT: Von {user_email} von '{old_status}' zu '{new_status}' geändert.",
            "timestamp": now_str
        }
        try:
            supabase.table("comments").insert(comment_entry).execute()
        except Exception:
            pass

        return {"message": "Status aktualisiert", "status": new_status}
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str, user_email: str = Depends(get_current_user_email)):
    try:
        parsed_id = parse_id(task_id)

        task_response = supabase.table("tasks").select("*").eq("id", parsed_id).execute()
        if not task_response.data:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

        task = task_response.data[0]
        if task.get("created_by") != user_email:
            raise HTTPException(status_code=403, detail="Nur der Ersteller darf diese Aufgabe löschen.")

        supabase.table("tasks").delete().eq("id", parsed_id).execute()
        return {"message": "Aufgabe erfolgreich gelöscht."}
    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# CHAT & DATEIEN
# ---------------------------------------------------------------------------
@app.get("/tasks/{task_id}/comments")
def get_task_comments(task_id: str, user_email: str = Depends(get_current_user_email)):
    try:
        parsed_id = parse_id(task_id)
        response = supabase.table("comments").select("*").eq("task_id", parsed_id).execute()
        
        # Einheitliches Format sichern
        cleaned_comments = []
        for c in (response.data or []):
            cleaned_comments.append({
                "author": c.get("author") or c.get("user_email") or "System",
                "message": c.get("message") or c.get("text") or "",
                "timestamp": c.get("timestamp") or c.get("created_at") or ""
            })
        return cleaned_comments
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tasks/{task_id}/comments")
def add_task_comment(task_id: str, comment_data: CommentCreate, user_email: str = Depends(get_current_user_email)):
    try:
        parsed_id = parse_id(task_id)
        msg = comment_data.message or comment_data.text or ""
        now_str = datetime.datetime.now().strftime("%H:%M:%S")

        new_comment = {
            "task_id": parsed_id,
            "author": comment_data.author or user_email,
            "message": msg,
            "timestamp": now_str
        }
        response = supabase.table("comments").insert(new_comment).execute()
        return response.data[0] if response.data else new_comment
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tasks/{task_id}/upload")
@app.post("/tasks/{task_id}/attachments")
async def upload_attachment(task_id: str, file: UploadFile = File(...), user_email: str = Depends(get_current_user_email)):
    try:
        parsed_id = parse_id(task_id)
        file_bytes = await file.read()
        file_name = file.filename
        file_path = f"tasks/{parsed_id}/{file_name}"

        file_url = ""
        try:
            supabase.storage.from_("attachments").upload(file_path, file_bytes)
            file_url = supabase.storage.from_("attachments").get_public_url(file_path)
        except Exception:
            pass

        return {"message": "Datei erfolgreich hochgeladen", "url": file_url, "file_url": file_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# GRUPPEN
# ---------------------------------------------------------------------------
@app.get("/groups")
def get_groups(user_email: str = Depends(get_current_user_email)):
    try:
        response = supabase.table("groups").select("*").execute()
        result = {}
        for g in response.data or []:
            members_raw = g.get("members", "")
            if isinstance(members_raw, list):
                result[g["name"]] = members_raw
            else:
                result[g["name"]] = [m.strip() for m in str(members_raw).split(",") if m.strip()]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/groups")
def save_group(group_data: GroupCreate, user_email: str = Depends(get_current_user_email)):
    try:
        existing = supabase.table("groups").select("*").eq("name", group_data.name).execute()
        members_str = ", ".join(group_data.members)

        if existing.data:
            supabase.table("groups").update({"members": members_str}).eq("name", group_data.name).execute()
        else:
            supabase.table("groups").insert({"name": group_data.name, "members": members_str}).execute()

        return {"message": "Gruppe erfolgreich gespeichert!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))