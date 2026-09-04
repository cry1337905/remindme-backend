import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForms
from jose import JWTError, jwt
from pydantic import BaseModel
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# KONFIGURATION & DATENBANKVERBINDUNG (SUPABASE)
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL Umweltvariable ist nicht gesetzt!")

# Fix für Render / Supabase URL-Formate & SSL Mode
db_url = DATABASE_URL.replace("postgres://", "postgresql://")
if "sslmode" not in db_url and "localhost" not in db_url:
    if "?" in db_url:
        db_url += "&sslmode=require"
    else:
        db_url += "?sslmode=require"

engine = create_engine(db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# JWT & PASSWORT HASHING
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "remind_me_super_secret_jwt_key_1337")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # Token 24 Stunden gültig

password_hash = PasswordHash((BcryptHasher(),))
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ---------------------------------------------------------------------------
# SQLALCHEMY MODELLE
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)


class TaskDB(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_by = Column(String, nullable=False)
    assignee = Column(String, nullable=True)
    deadline = Column(String, nullable=True)


# Tabellen in Supabase automatisch anlegen falls sie fehlen
Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# PYDANTIC SCHEMAS (API REQUEST / RESPONSE)
# ---------------------------------------------------------------------------
class UserCreate(BaseModel):
    username: str
    password: str


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    assignee: Optional[str] = ""
    deadline: Optional[str] = ""


class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    created_by: str
    assignee: Optional[str]
    deadline: Optional[str]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# DEPENDENCIES (DATENBANK & BENUTZER-AUTHENTIFIZIERUNG)
# ---------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token konnte nicht validiert werden.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = (
        db.query(UserDB).filter(UserDB.username == username).first()
    )
    if user is None:
        raise credentials_exception
    return user


# ---------------------------------------------------------------------------
# FASTAPI ENDPUNKTE
# ---------------------------------------------------------------------------
app = FastAPI(title="Remind Me =) Backend")


@app.get("/")
def root():
    return {"status": "ok", "message": "Remind Me =) API läuft!"}


@app.post("/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = (
        db.query(UserDB).filter(UserDB.username == user.username).first()
    )
    if db_user:
        raise HTTPException(
            status_code=400, detail="Benutzername ist bereits vergeben."
        )

    # Passwort kürzen falls > 72 Zeichen (Bcrypt Limit)
    safe_password = user.password[:72]
    hashed_pw = hash_password(safe_password)

    new_user = UserDB(username=user.username, hashed_password=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Benutzer erfolgreich registriert."}


@app.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = (
        db.query(UserDB)
        .filter(UserDB.username == form_data.username)
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=400, detail="Ungültiger Benutzername oder Passwort."
        )

    safe_password = form_data.password[:72]
    if not verify_password(safe_password, user.hashed_password):
        raise HTTPException(
            status_code=400, detail="Ungültiger Benutzername oder Passwort."
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/tasks", response_model=List[TaskResponse])
def read_tasks(
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(TaskDB).all()


@app.post("/tasks", response_model=TaskResponse)
def create_task(
    task: TaskCreate,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_task = TaskDB(
        title=task.title,
        description=task.description,
        created_by=current_user.username,
        assignee=task.assignee,
        deadline=task.deadline,
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task


@app.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=404, detail="Aufgabe nicht gefunden."
        )

    db.delete(task)
    db.commit()
    return {"message": f"Aufgabe {task_id} wurde gelöscht."}