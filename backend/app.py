import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
SETTING_KEY = "residue_limit_percent"
DEFAULT_LIMIT_PERCENT = 10.0
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    steps: list[StepIn]
    input_grams: float = Field(gt=0)
    residue_grams: float = Field(ge=0)


class CorrectInputIn(BaseModel):
    input_grams: float = Field(gt=0)


class PercentIn(BaseModel):
    limit_percent: float = Field(gt=0, le=100)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可写入记录")
    return user


def get_limit_percent(conn) -> float:
    row = conn.execute(
        "SELECT value::float8 AS v FROM app_settings WHERE key = %s", (SETTING_KEY,)
    ).fetchone()
    return float(row["v"]) if row else DEFAULT_LIMIT_PERCENT


def over_limit(residue_grams: float, input_grams: float, percent: float) -> bool:
    # 残渣不得超过投料的设定百分比；等值放行，用整数化比较避免除法误差
    return residue_grams * 100 > input_grams * percent


app = FastAPI(title="饮片炮制记录台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batches (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                doc jsonb NOT NULL,
                verdict text NOT NULL,
                reason text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key text PRIMARY KEY,
                value numeric NOT NULL,
                updated_by text NOT NULL,
                updated_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS residue_records (
                id serial PRIMARY KEY,
                batch_id integer NOT NULL REFERENCES batches(id),
                kind text NOT NULL,
                input_grams numeric NOT NULL,
                residue_grams numeric NOT NULL,
                limit_percent numeric NOT NULL,
                note text NOT NULL DEFAULT '',
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}, 100.0, 8.0),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}, 100.0, 8.0),
            ]
            percent = get_limit_percent(conn)
            for herb, doc, input_grams, residue_grams in samples:
                verdict, reason = judge(doc)
                doc = {**doc, "input_grams": input_grams, "residue_grams": residue_grams}
                batch_id = conn.execute(
                    """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s) RETURNING id""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                ).fetchone()["id"]
                conn.execute(
                    """INSERT INTO residue_records
                           (batch_id, kind, input_grams, residue_grams, limit_percent, note, created_by, created_at)
                       VALUES (%s, 'weigh', %s, %s, %s, '', %s, %s)""",
                    (batch_id, input_grams, residue_grams, percent, "processor", now),
                )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/batches")
def list_batches(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT id, herb, doc, verdict, reason, created_by FROM batches ORDER BY id DESC").fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        percent = get_limit_percent(conn)
        if over_limit(body.residue_grams, body.input_grams, percent):
            raise HTTPException(
                status_code=400,
                detail=f"残渣 {body.residue_grams:g} 克超过投料 {body.input_grams:g} 克的 {percent:g}% 上限，拒绝写入",
            )
        doc = {
            "steps": [s.model_dump() for s in body.steps],
            "input_grams": body.input_grams,
            "residue_grams": body.residue_grams,
        }
        verdict, reason = judge(doc)
        now = datetime.now(timezone.utc)
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (body.herb.strip(), json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], now),
        ).fetchone()
        # 残渣与投料同笔落入履历，百分比按当时快照；履历只追加，不做更新
        conn.execute(
            """INSERT INTO residue_records
                   (batch_id, kind, input_grams, residue_grams, limit_percent, note, created_by, created_at)
               VALUES (%s, 'weigh', %s, %s, %s, '', %s, %s)""",
            (row["id"], body.input_grams, body.residue_grams, percent, user["username"], now),
        )
        conn.commit()
    return row


@app.post("/api/batches/{batch_id}/correct-input")
def correct_input(batch_id: int, body: CorrectInputIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        old = conn.execute("SELECT id, doc FROM batches WHERE id = %s", (batch_id,)).fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail="批次不存在")
        old_doc = old["doc"]
        if "residue_grams" not in old_doc:
            raise HTTPException(status_code=400, detail="该批次没有残渣记录，无法改正投料")
        residue_grams = float(old_doc["residue_grams"])
        old_input = float(old_doc["input_grams"])
        percent = get_limit_percent(conn)
        if over_limit(residue_grams, body.input_grams, percent):
            raise HTTPException(
                status_code=400,
                detail=f"残渣 {residue_grams:g} 克超过投料 {body.input_grams:g} 克的 {percent:g}% 上限，拒绝改正",
            )
        now = datetime.now(timezone.utc)
        new_doc = dict(old_doc)
        new_doc["input_grams"] = body.input_grams
        row = conn.execute(
            """UPDATE batches SET doc = %s::jsonb WHERE id = %s
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (json.dumps(new_doc, ensure_ascii=False), batch_id),
        ).fetchone()
        # 改正投料只在履历追加一行，旧行的残渣原样保留、绝不覆盖
        conn.execute(
            """INSERT INTO residue_records
                   (batch_id, kind, input_grams, residue_grams, limit_percent, note, created_by, created_at)
               VALUES (%s, 'correct_input', %s, %s, %s, %s, %s, %s)""",
            (
                batch_id,
                body.input_grams,
                residue_grams,
                percent,
                f"投料改正：{old_input:g} 克改为 {body.input_grams:g} 克，残渣沿用旧值",
                user["username"],
                now,
            ),
        )
        conn.commit()
    return row


@app.get("/api/residue/settings")
def residue_settings(_user: dict = Depends(current_user)):
    with connect() as conn:
        return {"limit_percent": get_limit_percent(conn)}


@app.put("/api/residue/settings")
def update_residue_settings(body: PercentIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_by, updated_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET
                   value = EXCLUDED.value,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = EXCLUDED.updated_at""",
            (SETTING_KEY, body.limit_percent, user["username"], datetime.now(timezone.utc)),
        )
        conn.commit()
    return {"limit_percent": body.limit_percent}


@app.get("/api/residue/records")
def list_residue_records(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.batch_id, b.herb, r.kind,
                      r.input_grams::float8 AS input_grams,
                      r.residue_grams::float8 AS residue_grams,
                      r.limit_percent::float8 AS limit_percent,
                      r.note, r.created_by, r.created_at
                 FROM residue_records r
                 JOIN batches b ON b.id = r.batch_id
                ORDER BY r.id ASC"""
        ).fetchall()
    return rows
