import datetime
import os
from typing import List, Optional

import bcrypt
from fastapi import Body, Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import Column, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker
from sqlalchemy.pool import NullPool

# ---------------------------------------------------------------------------
# DATENBANK-KONFIGURATION (Supabase / Render)
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local_app.db")

# Falls Supabase mit 'postgres://' startet, auf 'postgresql://' anpassen
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Sonderzeichen im Passwort automatisch für SQLAlchemy maskieren (falls unmaskiert)
if "!" in DATABASE_URL and "%21" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("!", "%21")

# SQLite benötigt connect_args, PostgreSQL/Supabase nutzt den Transaction Pooler (NullPool)
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, poolclass=NullPool)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# SQLALCHEMY MODELLE
# ---------------------------------------------------------------------------
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)


class TaskDB(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    assignee = Column(String, nullable=True)
    deadline = Column(String, nullable=True)
    status = Column(String, default="Offen")
    created_by = Column(String, nullable=False)


class CommentDB(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"))
    author = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    timestamp = Column(String, nullable=True)


class GroupDB(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    members = Column(Text, nullable=False)  # Kommagetrennte Liste


Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# SECURITY & AUTHENTIFIZIERUNG (Direkt via Bcrypt ohne Passlib)
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    # Bcrypt beschränkt Passwörter auf max. 72 Bytes
    if len(pwd_bytes) > 72:
        pwd_bytes = pwd_bytes[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password.encode("utf-8")
    if len(pwd_bytes) > 72:
        pwd_bytes = pwd_bytes[:72]
    hash_bytes = hashed_password.encode("utf-8")
    try:
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == token).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Anmeldedaten oder Sitzung abgelaufen.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user.username


# ---------------------------------------------------------------------------
# PYDANTIC SCHEMAS
# ---------------------------------------------------------------------------
class UserCreate(BaseModel):
    username: str
    password: str


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    assignee: Optional[str] = ""
    deadline: Optional[str] = ""


class TaskStatusUpdate(BaseModel):
    status: str


class CommentCreate(BaseModel):
    message: str


class GroupCreate(BaseModel):
    name: str
    members: List[str]


# ---------------------------------------------------------------------------
# FASTAPI APP & ENDPUNKTE
# ---------------------------------------------------------------------------
app = FastAPI(title="Remind Me Backend")


@app.get("/")
def root():
    return {"status": "Backend läuft verknüpft mit Datenbank!"}


@app.post("/register")
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.username == user_data.username).first()
    if user:
        raise HTTPException(
            status_code=400, detail="Benutzername bereits vergeben."
        )

    hashed_pw = get_password_hash(user_data.password)
    new_user = UserDB(username=user_data.username, hashed_password=hashed_pw)
    db.add(new_user)
    db.commit()
    return {"message": "Benutzer erfolgreich registriert."}


@app.post("/login")
def login(
    form_data: Optional[OAuth2PasswordRequestForm] = Depends(),
    json_data: Optional[dict] = Body(None),
    db: Session = Depends(get_db)
):
    # Unterstützt sowohl Form-Data als auch JSON-Payloads vom Client
    username = None
    password = None

    if form_data and form_data.username:
        username = form_data.username
        password = form_data.password
    elif json_data:
        username = json_data.get("username")
        password = json_data.get("password")

    if not username or not password:
        raise HTTPException(
            status_code=400, detail="Benutzername und Passwort erforderlich."
        )

    user = db.query(UserDB).filter(UserDB.username == username).first()
    
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=400, detail="Benutzername oder Passwort falsch."
        )

    return {"access_token": user.username, "token_type": "bearer"}


@app.get("/tasks")
def get_tasks(current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    tasks = db.query(TaskDB).all()
    result = []
    for t in tasks:
        result.append({
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "assignee": t.assignee,
            "deadline": t.deadline,
            "status": t.status,
            "created_by": t.created_by
        })
    return result


@app.post("/tasks")
def create_task(task_data: TaskCreate, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    new_task = TaskDB(
        title=task_data.title,
        description=task_data.description,
        assignee=task_data.assignee,
        deadline=task_data.deadline,
        status="Offen",
        created_by=current_user
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return {"message": "Aufgabe erfolgreich erstellt.", "id": new_task.id}


@app.patch("/tasks/{task_id}/status")
@app.put("/tasks/{task_id}")
def update_task_status(task_id: int, status_data: TaskStatusUpdate, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

    task.status = status_data.status
    db.commit()
    return {"message": "Status aktualisiert."}


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

    if task.created_by != current_user:
        raise HTTPException(status_code=403, detail="Nur der Ersteller darf diese Aufgabe löschen.")

    db.delete(task)
    db.commit()
    return {"message": "Aufgabe gelöscht."}


@app.get("/tasks/{task_id}/comments")
def get_comments(task_id: int, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    comments = db.query(CommentDB).filter(CommentDB.task_id == task_id).all()
    result = []
    for c in comments:
        result.append({
            "author": c.author,
            "message": c.message,
            "timestamp": c.timestamp
        })
    return result


@app.post("/tasks/{task_id}/comments")
def add_comment(task_id: int, comment_data: CommentCreate, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    raw_message = comment_data.message
    timestamp_str = ""

    if raw_message.startswith("__TIME__:"):
        parts = raw_message.split("\n", 1)
        timestamp_str = parts[0].replace("__TIME__:", "").strip()
        clean_msg = parts[1] if len(parts) > 1 else ""
    else:
        clean_msg = raw_message
        timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")

    new_comment = CommentDB(
        task_id=task_id,
        author=current_user,
        message=clean_msg,
        timestamp=timestamp_str
    )
    db.add(new_comment)
    db.commit()
    return {"message": "Kommentar hinzugefügt."}


@app.get("/groups")
def get_groups(current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    groups = db.query(GroupDB).all()
    result = {}
    for g in groups:
        result[g.name] = [m.strip() for m in g.members.split(",") if m.strip()]
    return result


@app.post("/groups")
def save_group(group_data: GroupCreate, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    existing_group = db.query(GroupDB).filter(GroupDB.name == group_data.name).first()
    members_str = ", ".join(group_data.members)

    if existing_group:
        existing_group.members = members_str
    else:
        new_group = GroupDB(name=group_data.name, members=members_str)
        db.add(new_group)

    db.commit()
    return {"message": f"Gruppe '{group_data.name}' erfolgreich gespeichert."}