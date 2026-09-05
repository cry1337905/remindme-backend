import datetime
import json
import os
import random
import secrets
import string
import urllib.parse
import urllib.request
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from supabase import Client, create_client

app = FastAPI(title="Remind Me Backend")

# ---------------------------------------------------------------------------
# SUPABASE KONFIGURATION
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print(
        "[WARNUNG] SUPABASE_URL oder SUPABASE_KEY fehlen in den"
        " Umgebungsvariablen!"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------------------------
# BREVO E-MAIL KONFIGURATION (HTTP API)
# ---------------------------------------------------------------------------
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", os.getenv("SMTP_USER", ""))


def send_reset_email(to_email: str, code: str) -> bool:
    """Sendet eine E-Mail direkt über die Brevo HTTP API."""
    if not SMTP_PASSWORD:
        print("[WARNUNG] SMTP_PASSWORD ist nicht in Render gesetzt!")
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": SMTP_PASSWORD,
        "content-type": "application/json",
    }
    payload = {
        "sender": {"name": "RemindMe App", "email": SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": "Dein Passwort-Reset-Code",
        "textContent": (
            f"Hallo,\n\n"
            f"dein Sicherheitscode zum Zurücksetzen des Passports lautet:\n\n"
            f"   {code}\n\n"
            f"Falls du dies nicht angefordert hast, kannst du diese E-Mail"
            f" einfach ignorieren.\n\n"
            f"Viele Grüße,\n"
            f"Dein RemindMe Team"
        ),
    }

    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 201):
                print(f"[LOG] E-Mail erfolgreich an {to_email} gesendet.")
                return True
    except Exception as e:
        print(f"[FEHLER] E-Mail konnte nicht via API gesendet werden: {e}")
        return False


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


# ---------------------------------------------------------------------------
# HELFER
# ---------------------------------------------------------------------------
def generate_company_code() -> str:
    """Generiert einen zufälligen 8-stelligen Firmen-Code (z. B. COMP-A8X2)."""
    suffix = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4)
    )
    return f"COMP-{suffix}"


# ---------------------------------------------------------------------------
# PYDANTIC SCHEMAS
# ---------------------------------------------------------------------------
class UserRegister(BaseModel):
    email: str
    username: str
    password: str
    company_code: Optional[str] = None  # Bei Beitritt
    company_name: Optional[str] = None  # Bei Neugründung


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
# AUTHENTIFIZIERUNG & NUTZER-DATEN
# ---------------------------------------------------------------------------
def get_current_user_data(token: str = Depends(oauth2_scheme)) -> dict:
    """Ermittelt den aktuellen Nutzer und seine Firmendaten direkt aus Supabase."""
    clean_token = token.strip().lower()

    res = (
        supabase.table("users")
        .select("email, username, company_code")
        .eq("email", clean_token)
        .execute()
    )

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiges Authentifizierungs-Token oder Nutzer nicht gefunden.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return res.data[0]


# ---------------------------------------------------------------------------
# USER & AUTH ENDPUNKTE
# ---------------------------------------------------------------------------
@app.post("/register")
def register(user: UserRegister):
    email_clean = user.email.strip().lower()

    # Prüfen, ob E-Mail bereits existiert
    existing = (
        supabase.table("users")
        .select("email")
        .eq("email", email_clean)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=400, detail="E-Mail-Adresse ist bereits registriert."
        )

    assigned_code = None

    # Option A: Neue Firma gründen
    if user.company_name and user.company_name.strip():
        assigned_code = generate_company_code()
        supabase.table("companies").insert(
            {"code": assigned_code, "name": user.company_name.strip()}
        ).execute()

    # Option B: Bestehender Firma beitreten
    elif user.company_code and user.company_code.strip():
        code_clean = user.company_code.strip().upper()
        company_res = (
            supabase.table("companies")
            .select("code")
            .eq("code", code_clean)
            .execute()
        )

        if not company_res.data:
            raise HTTPException(
                status_code=400,
                detail="Der eingegebene Firmen-Code ist ungültig.",
            )
        assigned_code = code_clean

    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Bitte gib entweder einen Firmennamen an (Neugründung) oder"
                " einen Firmen-Code (Beitritt)."
            ),
        )

    # Nutzer in Supabase anlegen
    supabase.table("users").insert(
        {
            "email": email_clean,
            "username": user.username.strip(),
            "password": user.password,
            "company_code": assigned_code,
        }
    ).execute()

    return {
        "message": "Registrierung erfolgreich",
        "company_code": assigned_code,
    }


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    email_clean = form_data.username.strip().lower()

    res = (
        supabase.table("users")
        .select("email, password")
        .eq("email", email_clean)
        .execute()
    )

    if not res.data or res.data[0]["password"] != form_data.password:
        raise HTTPException(
            status_code=400, detail="Ungültige E-Mail oder Passwort."
        )

    return {"access_token": email_clean, "token_type": "bearer"}


@app.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    email_clean = req.email.strip().lower()

    user_res = (
        supabase.table("users")
        .select("email")
        .eq("email", email_clean)
        .execute()
    )
    if not user_res.data:
        raise HTTPException(
            status_code=404,
            detail="Kein Konto mit dieser E-Mail-Adresse gefunden.",
        )

    code = f"{random.randint(100000, 999999)}"

    # Code in Supabase speichern/aktualisieren
    supabase.table("users").update({"reset_code": code}).eq(
        "email", email_clean
    ).execute()

    email_sent = send_reset_email(email_clean, code)
    if not email_sent:
        print(f"[FALLBACK] E-Mail-Versand fehlgeschlagen. Code lautet: {code}")

    return {"message": "Reset-Code wurde per E-Mail versendet."}


@app.post("/reset-password")
def reset_password(req: ResetPasswordRequest):
    email_clean = req.email.strip().lower()

    res = (
        supabase.table("users")
        .select("reset_code")
        .eq("email", email_clean)
        .execute()
    )

    if not res.data or res.data[0].get("reset_code") != req.code.strip():
        raise HTTPException(
            status_code=400, detail="Ungültiger oder abgelaufener Code."
        )

    supabase.table("users").update(
        {"password": req.new_password.strip(), "reset_code": None}
    ).eq("email", email_clean).execute()

    return {"message": "Passwort erfolgreich zurückgesetzt."}


# ---------------------------------------------------------------------------
# GRUPPEN ENDPUNKTE (Firmenspezifisch)
# ---------------------------------------------------------------------------
@app.get("/groups")
def get_groups(current_user: dict = Depends(get_current_user_data)):
    res = (
        supabase.table("groups")
        .select("name, members")
        .eq("company_code", current_user["company_code"])
        .execute()
    )

    groups_dict = {item["name"]: item.get("members", []) for item in res.data}
    return groups_dict


@app.post("/groups")
def save_group(
    group: GroupSchema, current_user: dict = Depends(get_current_user_data)
):
    group_name = group.name.strip()
    company_code = current_user["company_code"]

    existing = (
        supabase.table("groups")
        .select("id")
        .eq("company_code", company_code)
        .eq("name", group_name)
        .execute()
    )

    if existing.data:
        supabase.table("groups").update({"members": group.members}).eq(
            "id", existing.data[0]["id"]
        ).execute()
    else:
        supabase.table("groups").insert(
            {
                "name": group_name,
                "members": group.members,
                "company_code": company_code,
            }
        ).execute()

    return {"message": f"Gruppe '{group_name}' erfolgreich gespeichert."}


@app.delete("/groups/{group_name}")
def delete_group(
    group_name: str, current_user: dict = Depends(get_current_user_data)
):
    decoded_name = urllib.parse.unquote(group_name).strip()
    company_code = current_user["company_code"]

    res = (
        supabase.table("groups")
        .delete()
        .eq("company_code", company_code)
        .eq("name", decoded_name)
        .execute()
    )

    if res.data:
        return {
            "message": f"Gruppe '{decoded_name}' wurde erfolgreich gelöscht."
        }

    raise HTTPException(
        status_code=404,
        detail=f"Gruppe '{decoded_name}' wurde nicht gefunden.",
    )


# ---------------------------------------------------------------------------
# TASK ENDPUNKTE (Firmenspezifisch)
# ---------------------------------------------------------------------------
@app.get("/tasks")
def get_tasks(current_user: dict = Depends(get_current_user_data)):
    res = (
        supabase.table("tasks")
        .select("*")
        .eq("company_code", current_user["company_code"])
        .execute()
    )
    return res.data


@app.post("/tasks")
def create_task(
    task: TaskCreate, current_user: dict = Depends(get_current_user_data)
):
    new_task = {
        "title": task.title,
        "description": task.description,
        "assignee": task.assignee,
        "deadline": task.deadline,
        "status": "Offen",
        "created_by": current_user["username"],
        "company_code": current_user["company_code"],
    }

    res = supabase.table("tasks").insert(new_task).execute()
    return res.data[0] if res.data else new_task


@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(
    task_id: int,
    status_update: TaskStatusUpdate,
    current_user: dict = Depends(get_current_user_data),
):
    res = (
        supabase.table("tasks")
        .update({"status": status_update.status})
        .eq("id", task_id)
        .eq("company_code", current_user["company_code"])
        .execute()
    )

    if res.data:
        return res.data[0]

    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")


@app.delete("/tasks/{task_id}")
def delete_task(
    task_id: int, current_user: dict = Depends(get_current_user_data)
):
    company_code = current_user["company_code"]

    res = (
        supabase.table("tasks")
        .delete()
        .eq("id", task_id)
        .eq("company_code", company_code)
        .execute()
    )

    if res.data:
        supabase.table("comments").delete().eq("task_id", task_id).execute()
        return {"message": "Aufgabe gelöscht."}

    raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")


# ---------------------------------------------------------------------------
# KOMMENTAR ENDPUNKTE (Firmenspezifisch)
# ---------------------------------------------------------------------------
@app.get("/tasks/{task_id}/comments")
def get_comments(
    task_id: str, current_user: dict = Depends(get_current_user_data)
):
    res = (
        supabase.table("comments")
        .select("author, message, timestamp")
        .eq("task_id", task_id)
        .execute()
    )
    return res.data if res.data else []


@app.post("/tasks/{task_id}/comments")
def add_comment(
    task_id: str,
    comment: CommentCreate,
    current_user: dict = Depends(get_current_user_data),
):
    comment_entry = {
        "task_id": task_id,
        "author": current_user["username"],
        "message": comment.message,
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
    }

    supabase.table("comments").insert(comment_entry).execute()
    return comment_entry