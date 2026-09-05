import os
import random
import string
import uuid
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, status
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from postgrest.exceptions import APIError

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL und SUPABASE_KEY müssen gesetzt sein.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="RemindMe Backend")

# ---------------------------------------------------------------------------
# HEALTH CHECK (Behebt 404 Fehler auf Render)
# ---------------------------------------------------------------------------
@app.get("/")
def health_check():
    return {"status": "ok", "message": "RemindMe API läuft"}

# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------
class RegisterSchema(BaseModel):
    email: EmailStr
    username: str
    password: str
    company_name: Optional[str] = None
    company_code: Optional[str] = None

class GroupSchema(BaseModel):
    name: str
    members: List[str]

class TaskSchema(BaseModel):
    title: str
    description: str
    assignee: str
    deadline: str

class StatusUpdateSchema(BaseModel):
    status: str

class CommentSchema(BaseModel):
    message: str

class ForgotPasswordSchema(BaseModel):
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    email: EmailStr
    code: str
    new_password: str

# ---------------------------------------------------------------------------
# HELFERFUNKTIONEN
# ---------------------------------------------------------------------------
def generate_company_code(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    return "COMP-" + "".join(random.choices(chars, k=length))

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder fehlender Authorization Header",
        )
    
    token = authorization.split(" ")[1]
    
    try:
        user_res = supabase.auth.get_user(token)
        if not user_res.user:
            raise HTTPException(status_code=401, detail="Sitzung abgelaufen oder ungültig")
        
        user_id = user_res.user.id
        profile_res = supabase.table("users").select("*").eq("id", user_id).execute()
        if not profile_res.data:
            raise HTTPException(status_code=404, detail="Benutzerprofil nicht gefunden")
            
        return profile_res.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentifizierungsfehler: {str(e)}",
        )

# ---------------------------------------------------------------------------
# REGISTRIERUNG (MIT DETALLIERTER FEHLERMELDUNG)
# ---------------------------------------------------------------------------
@app.post("/register")
def register(data: RegisterSchema):
    try:
        # 1. Supabase Auth Benutzer erstellen
        auth_res = supabase.auth.sign_up({
            "email": data.email,
            "password": data.password
        })
        
        if not auth_res.user:
            raise HTTPException(status_code=400, detail="Registrierung im Auth-System fehlgeschlagen.")
            
        user_id = auth_res.user.id
        company_id = None
        assigned_code = None

        # 2. Firmeneinbindung verarbeiten
        if data.company_name and data.company_name.strip():
            assigned_code = generate_company_code()
            comp_res = supabase.table("companies").insert({
                "name": data.company_name.strip(),
                "code": assigned_code
            }).execute()
            
            if comp_res.data:
                company_id = comp_res.data[0]["id"]
            else:
                raise HTTPException(status_code=500, detail="Firma konnte nicht angelegt werden.")

        elif data.company_code and data.company_code.strip():
            code_clean = data.company_code.strip().upper()
            comp_res = supabase.table("companies").select("id, code").eq("code", code_clean).execute()
            
            if not comp_res.data:
                raise HTTPException(status_code=400, detail="Ungültiger Firmen-Code. Firma existiert nicht.")
            
            company_id = comp_res.data[0]["id"]
            assigned_code = comp_res.data[0]["code"]
        else:
            raise HTTPException(
                status_code=400, 
                detail="Bitte gib entweder einen Firmennamen zum Gründen oder einen Firmen-Code zum Beitritt an."
            )

        # 3. Profil in public.users speichern
        supabase.table("users").insert({
            "id": user_id,
            "email": data.email,
            "username": data.username,
            "company_id": company_id
        }).execute()

        return {
            "message": "Registrierung erfolgreich!",
            "company_code": assigned_code
        }

    except APIError as api_err:
        print(f"DATABASE ERROR: {api_err}")
        raise HTTPException(status_code=400, detail=f"Datenbankfehler: {api_err.message}")
    except HTTPException as http_e:
        raise http_e
    except Exception as e:
        print(f"INTERNAL ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Interner Serverfehler: {str(e)}")

# ---------------------------------------------------------------------------
# LOGIN & PASSWORT RESET
# ---------------------------------------------------------------------------
@app.post("/login")
def login(data: dict):
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        raise HTTPException(status_code=400, detail="E-Mail und Passwort erforderlich")

    try:
        res = supabase.auth.sign_in_with_password({
            "email": username,
            "password": password
        })
        return {
            "access_token": res.session.access_token,
            "token_type": "bearer"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail="Anmeldung fehlgeschlagen. E-Mail oder Passwort falsch.")

@app.post("/forgot-password")
def forgot_password(data: ForgotPasswordSchema):
    try:
        supabase.auth.reset_password_email(data.email)
        return {"message": "Passwort-Zurücksetzen-Code gesendet."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/reset-password")
def reset_password(data: ResetPasswordSchema):
    try:
        res = supabase.auth.verify_otp({
            "email": data.email,
            "token": data.code,
            "type": "recovery"
        })
        if res.session:
            supabase.auth.update_user({"password": data.new_password})
            return {"message": "Passwort erfolgreich zurückgesetzt."}
        raise HTTPException(status_code=400, detail="Ungültiger Code.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fehler beim Zurücksetzen: {str(e)}")

# ---------------------------------------------------------------------------
# TASKS, COMMENTS & GROUPS
# ---------------------------------------------------------------------------
@app.get("/tasks")
def get_tasks(current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id")
    res = supabase.table("tasks").select("*").eq("company_id", company_id).execute()
    return res.data or []

@app.post("/tasks")
def create_task(data: TaskSchema, current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id")
    created_by = current_user.get("username") or current_user.get("email")

    payload = {
        "title": data.title,
        "description": data.description,
        "assignee": data.assignee,
        "deadline": data.deadline,
        "company_id": company_id,
        "created_by": created_by,
        "status": "Offen"
    }

    res = supabase.table("tasks").insert(payload).execute()
    if res.data:
        return res.data[0]
    raise HTTPException(status_code=500, detail="Aufgabe konnte nicht erstellt werden.")

@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(task_id: str, data: StatusUpdateSchema, current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id")
    res = supabase.table("tasks").update({"status": data.status}).eq("id", task_id).eq("company_id", company_id).execute()
    if res.data:
        return res.data[0]
    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden oder keine Berechtigung.")

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id")
    res = supabase.table("tasks").delete().eq("id", task_id).eq("company_id", company_id).execute()
    return {"message": "Aufgabe gelöscht."}

@app.get("/tasks/{task_id}/comments")
def get_comments(task_id: str, current_user: dict = Depends(get_current_user)):
    res = supabase.table("comments").select("*").eq("task_id", task_id).order("created_at", desc=False).execute()
    return res.data or []

@app.post("/tasks/{task_id}/comments")
def add_comment(task_id: str, data: CommentSchema, current_user: dict = Depends(get_current_user)):
    author = current_user.get("username") or current_user.get("email")
    payload = {
        "task_id": task_id,
        "author": author,
        "message": data.message
    }
    res = supabase.table("comments").insert(payload).execute()
    if res.data:
        return res.data[0]
    raise HTTPException(status_code=500, detail="Kommentar konnte nicht gespeichert werden.")

@app.get("/groups")
def get_groups(current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id")
    res = supabase.table("groups").select("*").eq("company_id", company_id).execute()
    
    groups_dict = {}
    if res.data:
        for item in res.data:
            groups_dict[item["name"]] = item.get("members", [])
    return groups_dict

@app.post("/groups")
def save_group(data: GroupSchema, current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id")
    
    existing = supabase.table("groups").select("id").eq("name", data.name).eq("company_id", company_id).execute()
    
    if existing.data:
        res = supabase.table("groups").update({"members": data.members}).eq("id", existing.data[0]["id"]).execute()
    else:
        res = supabase.table("groups").insert({
            "name": data.name,
            "members": data.members,
            "company_id": company_id
        }).execute()

    return {"message": f"Gruppe '{data.name}' gespeichert."}

@app.delete("/groups/{group_name}")
@app.delete("/groups")
def delete_group(group_name: Optional[str] = None, name: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    target_name = group_name or name
    if not target_name:
        raise HTTPException(status_code=400, detail="Gruppenname erforderlich.")
        
    company_id = current_user.get("company_id")
    supabase.table("groups").delete().eq("name", target_name).eq("company_id", company_id).execute()
    return {"message": f"Gruppe '{target_name}' gelöscht."}