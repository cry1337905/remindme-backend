from datetime import datetime, timedelta
import os
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
SECRET_KEY = os.getenv("SECRET_KEY", "SUPER_GEHEIMES_SECRET_KEY_123")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# SQLAlchemy Fix für Supabase / Render
db_url = DATABASE_URL.replace("postgres://", "postgresql://")
engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)


class TaskDB(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(String)
    created_by = Column(String)
    assignee = Column(String)
    deadline = Column(String)
    status = Column(String, default="Offen")


Base.metadata.create_all(bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Ungültiges Token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Ungültiges Token")

    user = (
        db.query(UserDB).filter(UserDB.username == username).first()
    )
    if user is None:
        raise HTTPException(status_code=401, detail="Benutzer nicht gefunden")
    return user


class UserCreate(BaseModel):
    username: str
    password: str


class TaskCreate(BaseModel):
    title: str
    description: str
    assignee: str
    deadline: str


@app.post("/register")
def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = (
        db.query(UserDB).filter(UserDB.username == user.username).first()
    )
    if db_user:
        raise HTTPException(
            status_code=400, detail="Benutzername bereits vergeben"
        )
    hashed_pw = get_password_hash(user.password)
    new_user = UserDB(username=user.username, password_hash=hashed_pw)
    db.add(new_user)
    db.commit()
    return {"message": "Benutzer erfolgreich registriert"}


@app.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = (
        db.query(UserDB).filter(UserDB.username == form_data.username).first()
    )
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="Falscher Benutzername oder Passwort"
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/tasks")
def get_tasks(
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(TaskDB).all()


@app.post("/tasks")
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
    return {"message": "Aufgabe erstellt"}


@app.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not task:
        raise HTTPException(
            status_code=404, detail="Aufgabe nicht gefunden"
        )

    if task.created_by != current_user.username:
        raise HTTPException(
            status_code=403,
            detail="Du darfst nur deine eigenen Aufgaben löschen!",
        )

    db.delete(task)
    db.commit()
    return {"message": "Aufgabe gelöscht"}