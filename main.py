import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import jwt
from passlib.context import CryptContext

# ---------------------------------------------------------------------------
# KONFIGURATION & DATENBANK-SETUP
# ---------------------------------------------------------------------------
SECRET_KEY = "DEIN_GEHEIMER_SCHLUESSEL_HIER_AENDERN"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 Tage gültig

# Füge hier deine Supabase Connection String ein (oder nutze Umgebungsvariablen)
DATABASE_URL = "postgresql://postgres:[DEIN-PASSWORT]@db.[DEIN-SUPABASE-REF].supabase.co:5432/postgres"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

app = FastAPI(title="Remind Me Backend")


# ---------------------------------------------------------------------------
# DATENBANK MODELLE (SQLAlchemy)
# ---------------------------------------------------------------------------
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    company_code = Column(String, nullable=True)


class TaskDB(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    assignee = Column(String, nullable=True)
    deadline = Column(String, nullable=True)
    status = Column(String, default="Offen")
    project_name = Column(String, default="Ohne Projekt")
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class GroupDB(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    members = Column(Text, nullable=False)  # Kommagetrennte E-Mails/Namen oder JSON


Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# PYDANTIC SCHEMAS (Eingabe / Ausgabe Validierung)
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


class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    assignee: Optional[str]
    deadline: Optional[str]
    status: Optional[str]
    project_name: Optional[str]
    created_by: str

    class Config:
        orm_mode = True


class GroupCreate(BaseModel):
    name: str
    members: List[str]


# ---------------------------------------------------------------------------
# HELFER-FUNKTIONEN & AUTHENTIFIZIERUNG
# ---------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token konnte nicht validiert werden",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(UserDB).filter(UserDB.email == email).first()
    if user is None:
        raise credentials_exception
    return user


# ---------------------------------------------------------------------------
# ENDPUNKTE / ROUTEN
# ---------------------------------------------------------------------------

@app.post("/register")
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_user = db.query(UserDB).filter(UserDB.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert.")

    hashed_pw = get_password_hash(user_data.password)
    new_user = UserDB(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_pw,
        company_code=user_data.company_code or "DEFAULT"
    )
    db.add(new_user)
    db.commit()
    return {"message": "Registrierung erfolgreich!"}


@app.post("/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.email == user_data.email).first()
    if not user or not verify_password(user.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Ungültige E-Mail oder Passwort.")

    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}


# ---------------------------------------------------------------------------
# GEFILTERTE AUFGABEN-ROUTE (NUR EIGENE UND ZUGEWIESENE AUFGABEN SEHEN)
# ---------------------------------------------------------------------------
@app.get("/tasks", response_model=List[TaskResponse])
def get_user_tasks(current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    user_email = current_user.email

    # 1. Alle Gruppen abrufen, in denen der angemeldete Benutzer als Mitglied eingetragen ist
    user_groups = db.query(GroupDB.name).filter(GroupDB.members.contains(user_email)).all()
    group_names = [g.name for g in user_groups]

    # 2. Filter anwenden:
    # - Der Benutzer ist Ersteller (created_by)
    # - ODER die E-Mail/Name ist direkt im 'assignee'-Feld enthalten
    # - ODER eine seiner Gruppen ist als 'assignee' zugewiesen
    tasks = db.query(TaskDB).filter(
        or_(
            TaskDB.created_by == user_email,
            TaskDB.assignee.contains(user_email),
            TaskDB.assignee.in_(group_names) if group_names else False
        )
    ).all()

    return tasks


@app.post("/tasks", response_model=TaskResponse)
def create_task(task_data: TaskCreate, current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    new_task = TaskDB(
        title=task_data.title,
        description=task_data.description,
        assignee=task_data.assignee,
        deadline=task_data.deadline,
        project_name=task_data.project_name or "Ohne Projekt",
        created_by=current_user.email
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden.")

    if task.created_by != current_user.email:
        raise HTTPException(status_code=403, detail="Nur der Ersteller darf diese Aufgabe löschen.")

    db.delete(task)
    db.commit()
    return {"message": "Aufgabe gelöscht."}


# ---------------------------------------------------------------------------
# GRUPPEN-ENDPUNKTE
# ---------------------------------------------------------------------------
@app.get("/groups")
def get_groups(current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    groups = db.query(GroupDB).all()
    result = {}
    for g in groups:
        members_list = [m.strip() for m in g.members.split(",") if m.strip()]
        result[g.name] = members_list
    return result


@app.post("/groups")
def save_group(group_data: GroupCreate, current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.query(GroupDB).filter(GroupDB.name == group_data.name).first()
    members_str = ", ".join(group_data.members)

    if existing:
        existing.members = members_str
    else:
        new_group = GroupDB(name=group_data.name, members=members_str)
        db.add(new_group)

    db.commit()
    return {"message": "Gruppe erfolgreich gespeichert!"}