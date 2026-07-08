"""
Transmission — a real chat application, all in one file.

Accounts (JWT auth), multiple rooms, persistent history (SQLite), and
real-time messaging over WebSockets. Frontend files (index.html, style.css,
app.js) sit right next to this file and are served directly below.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, joinedload, relationship, sessionmaker

# ============================================================================
# Database
# ============================================================================
DATABASE_URL = "sqlite:///./chat.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow():
    return datetime.now(timezone.utc)


# ============================================================================
# Models
# ============================================================================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(32), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    messages = relationship("Message", back_populates="user")


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, index=True, nullable=False)
    created_by = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    messages = relationship("Message", back_populates="room", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    ts = Column(DateTime, default=utcnow, index=True)

    room = relationship("Room", back_populates="messages")
    user = relationship("User", back_populates="messages")


# ============================================================================
# Schemas
# ============================================================================
class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class RoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\- ]+$")


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    created_by: str
    created_at: datetime


class MessageOut(BaseModel):
    id: int
    user: str
    text: str
    ts: datetime


# ============================================================================
# Security: password hashing + JWT
# ============================================================================
# SECURITY NOTE: set a real CHAT_SECRET_KEY environment variable in production.
SECRET_KEY = os.environ.get("CHAT_SECRET_KEY", "dev-secret-change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8")[:72], hashed_password.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(username: str) -> str:
    expire = utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    username = decode_access_token(token)
    if not username:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise credentials_exception
    return user


# ============================================================================
# WebSocket connection manager (per room)
# ============================================================================
class RoomConnectionManager:
    def __init__(self) -> None:
        self.rooms: Dict[int, Dict[str, WebSocket]] = {}

    async def connect(self, room_id: int, username: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms.setdefault(room_id, {})[username] = websocket

    def disconnect(self, room_id: int, username: str) -> None:
        room = self.rooms.get(room_id)
        if room:
            room.pop(username, None)
            if not room:
                self.rooms.pop(room_id, None)

    async def broadcast(self, room_id: int, payload: dict, exclude: str | None = None) -> None:
        room = self.rooms.get(room_id, {})
        dead = []
        for name, ws in room.items():
            if name == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(name)
        for name in dead:
            self.disconnect(room_id, name)

    def roster(self, room_id: int) -> list[str]:
        return sorted(self.rooms.get(room_id, {}).keys())


manager = RoomConnectionManager()

# ============================================================================
# App setup
# ============================================================================
Base.metadata.create_all(bind=engine)
app = FastAPI(title="Transmission Chat")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def now_iso() -> str:
    return utcnow().isoformat()


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
@app.post("/api/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="That call sign is already taken.")

    user = User(username=payload.username, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.username)
    return TokenResponse(access_token=token, username=user.username)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid call sign or passphrase.")

    token = create_access_token(user.username)
    return TokenResponse(access_token=token, username=user.username)


@app.get("/api/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username}


# ---------------------------------------------------------------------------
# Room endpoints
# ---------------------------------------------------------------------------
@app.get("/api/rooms", response_model=list[RoomOut])
def list_rooms(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return db.query(Room).order_by(Room.created_at.asc()).all()


@app.post("/api/rooms", response_model=RoomOut, status_code=201)
def create_room(
    payload: RoomCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(Room).filter(Room.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="A frequency with that name already exists.")

    room = Room(name=payload.name, created_by=current_user.username)
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


@app.get("/api/rooms/{room_id}/messages", response_model=list[MessageOut])
def get_room_messages(
    room_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Frequency not found.")

    limit = max(1, min(limit, 200))
    rows = (
        db.query(Message)
        .options(joinedload(Message.user))
        .filter(Message.room_id == room_id)
        .order_by(Message.ts.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [MessageOut(id=m.id, user=m.user.username, text=m.text, ts=m.ts) for m in rows]


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int, token: str = ""):
    username = decode_access_token(token)
    if not username:
        await websocket.close(code=4001)  # invalid/missing token
        return

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        room = db.query(Room).filter(Room.id == room_id).first()
        if not user or not room:
            await websocket.close(code=4004)  # not found
            return

        if username in manager.roster(room_id):
            await websocket.close(code=4009)  # already connected in this room
            return

        await manager.connect(room_id, username, websocket)

        await websocket.send_json({
            "type": "roster",
            "users": manager.roster(room_id),
            "you": username,
        })

        await manager.broadcast(room_id, {
            "type": "system",
            "event": "join",
            "user": username,
            "ts": now_iso(),
            "users": manager.roster(room_id),
        }, exclude=username)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")

                if msg_type == "message":
                    text = str(data.get("text", "")).strip()[:2000]
                    if not text:
                        continue

                    msg = Message(room_id=room_id, user_id=user.id, text=text)
                    db.add(msg)
                    db.commit()
                    db.refresh(msg)

                    await manager.broadcast(room_id, {
                        "type": "message",
                        "id": msg.id,
                        "user": username,
                        "text": text,
                        "ts": msg.ts.replace(tzinfo=timezone.utc).isoformat(),
                    })

                elif msg_type == "typing":
                    await manager.broadcast(room_id, {
                        "type": "typing",
                        "user": username,
                        "state": bool(data.get("state", False)),
                    }, exclude=username)

        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(room_id, username)
            await manager.broadcast(room_id, {
                "type": "system",
                "event": "leave",
                "user": username,
                "ts": now_iso(),
                "users": manager.roster(room_id),
            })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Frontend (served directly from this same folder — no subfolders)
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/style.css")
async def style():
    return FileResponse(os.path.join(BASE_DIR, "style.css"), media_type="text/css")


@app.get("/app.js")
async def app_js():
    return FileResponse(os.path.join(BASE_DIR, "app.js"), media_type="application/javascript")
