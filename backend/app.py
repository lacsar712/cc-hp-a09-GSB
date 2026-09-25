import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
DEFAULT_RESIDUE_PERCENT = Decimal("10")
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
    input_g: float = Field(gt=0)
    residue_g: float = Field(ge=0)


class PercentIn(BaseModel):
    residue_percent: float = Field(ge=0, le=100)


class CorrectInputIn(BaseModel):
    input_g: float = Field(gt=0)


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
        raise HTTPException(status_code=403, detail="仅炮制员可执行此操作")
    return user


def residue_over_limit(input_g: float, residue_g: float, percent: Decimal) -> bool:
    """残渣克重超过投料克重的设定百分比即返回 True。用 Decimal 避免浮点边界误差。"""
    limit = Decimal(str(input_g)) * percent / Decimal(100)
    return Decimal(str(residue_g)) > limit


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
                created_at timestamptz NOT NULL,
                input_g numeric(10,2),
                residue_g numeric(10,2)
            )"""
        )
        # 兼容旧库：补齐残渣闭环新增列
        columns = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'batches'"
            ).fetchall()
        }
        if "input_g" not in columns:
            conn.execute("ALTER TABLE batches ADD COLUMN input_g numeric(10,2)")
        if "residue_g" not in columns:
            conn.execute("ALTER TABLE batches ADD COLUMN residue_g numeric(10,2)")

        # 炮制员设定的残渣占投料百分比（全所单一门禁参数）
        conn.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key text PRIMARY KEY,
                value numeric(5,2) NOT NULL,
                updated_by text NOT NULL,
                updated_at timestamptz NOT NULL
            )"""
        )
        # 残渣履历：只追加，不修改、不删除
        conn.execute(
            """CREATE TABLE IF NOT EXISTS residue_ledger (
                id serial PRIMARY KEY,
                batch_id integer NOT NULL REFERENCES batches(id),
                input_g numeric(10,2) NOT NULL,
                residue_g numeric(10,2) NOT NULL,
                action text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        if conn.execute("SELECT 1 FROM settings WHERE key = 'residue_percent'").fetchone() is None:
            conn.execute(
                """INSERT INTO settings (key, value, updated_by, updated_at)
                   VALUES ('residue_percent', %s, %s, %s)""",
                (DEFAULT_RESIDUE_PERCENT, "system", datetime.now(timezone.utc)),
            )

        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}, 100.0, 5.0),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}, 100.0, 8.0),
            ]
            for herb, doc, input_g, residue_g in samples:
                verdict, reason = judge(doc)
                row = conn.execute(
                    """INSERT INTO batches
                           (herb, doc, verdict, reason, created_by, created_at, input_g, residue_g)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason,
                     "processor", now, input_g, residue_g),
                ).fetchone()
                conn.execute(
                    """INSERT INTO residue_ledger
                           (batch_id, input_g, residue_g, action, created_by, created_at)
                       VALUES (%s, %s, %s, '写入', %s, %s)""",
                    (row["id"], input_g, residue_g, "processor", now),
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


@app.get("/api/settings/residue-percent")
def get_residue_percent(_user: dict = Depends(current_user)):
    with connect() as conn:
        row = conn.execute(
            "SELECT value, updated_by, updated_at FROM settings WHERE key = 'residue_percent'"
        ).fetchone()
    return {
        "residue_percent": float(row["value"]),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


@app.put("/api/settings/residue-percent")
def update_residue_percent(body: PercentIn, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """UPDATE settings
                  SET value = %s, updated_by = %s, updated_at = %s
                WHERE key = 'residue_percent'
            RETURNING value, updated_by, updated_at""",
            (body.residue_percent, user["username"], now),
        ).fetchone()
        conn.commit()
    return {
        "residue_percent": float(row["value"]),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


@app.get("/api/residue-ledger")
def list_residue_ledger(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT l.id, l.batch_id, b.herb,
                      l.input_g::float8 AS input_g,
                      l.residue_g::float8 AS residue_g,
                      l.action, l.created_by, l.created_at
                 FROM residue_ledger l
                 JOIN batches b ON b.id = l.batch_id
             ORDER BY l.id DESC"""
        ).fetchall()
    return rows


@app.get("/api/batches")
def list_batches(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, herb, doc, verdict, reason, created_by,
                      input_g::float8 AS input_g,
                      residue_g::float8 AS residue_g
                 FROM batches ORDER BY id DESC"""
        ).fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    if body.residue_g > body.input_g:
        raise HTTPException(status_code=400, detail="残渣克重不得超过投料克重")
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        percent = conn.execute(
            "SELECT value FROM settings WHERE key = 'residue_percent'"
        ).fetchone()["value"]
        # 残渣门禁：超限直接拒写，记录不落地
        if residue_over_limit(body.input_g, body.residue_g, percent):
            limit = Decimal(str(body.input_g)) * percent / Decimal(100)
            raise HTTPException(
                status_code=400,
                detail=f"残渣 {body.residue_g:g} 克超过投料 {body.input_g:g} 克的 {float(percent):g}% 上限"
                       f"（{float(limit):g} 克），拒绝写入",
            )
        row = conn.execute(
            """INSERT INTO batches
                   (herb, doc, verdict, reason, created_by, created_at, input_g, residue_g)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by,
                         input_g::float8 AS input_g, residue_g::float8 AS residue_g""",
            (body.herb.strip(), json.dumps(doc, ensure_ascii=False), verdict, reason,
             user["username"], now, body.input_g, body.residue_g),
        ).fetchone()
        # 投料与残渣同时写入履历
        conn.execute(
            """INSERT INTO residue_ledger
                   (batch_id, input_g, residue_g, action, created_by, created_at)
               VALUES (%s, %s, %s, '写入', %s, %s)""",
            (row["id"], body.input_g, body.residue_g, user["username"], now),
        )
        conn.commit()
    return row


@app.put("/api/batches/{batch_id}/input")
def correct_batch_input(batch_id: int, body: CorrectInputIn, user: dict = Depends(require_writer)):
    """事后改正投料克重：残渣克重保持不变，履历追加一行，不覆盖旧残渣。"""
    now = datetime.now(timezone.utc)
    with connect() as conn:
        batch = conn.execute(
            "SELECT id, input_g, residue_g FROM batches WHERE id = %s", (batch_id,)
        ).fetchone()
        if batch is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        percent = conn.execute(
            "SELECT value FROM settings WHERE key = 'residue_percent'"
        ).fetchone()["value"]
        residue_g = float(batch["residue_g"])
        # 改正后的投料仍须满足残渣门禁
        if residue_over_limit(body.input_g, residue_g, percent):
            limit = Decimal(str(body.input_g)) * percent / Decimal(100)
            raise HTTPException(
                status_code=400,
                detail=f"改正后投料 {body.input_g:g} 克下，旧残渣 {residue_g:g} 克超过 {float(percent):g}% 上限"
                       f"（{float(limit):g} 克），拒绝改正",
            )
        conn.execute("UPDATE batches SET input_g = %s WHERE id = %s", (body.input_g, batch_id))
        # 追加履历：记新投料，残渣仍记旧值
        conn.execute(
            """INSERT INTO residue_ledger
                   (batch_id, input_g, residue_g, action, created_by, created_at)
               VALUES (%s, %s, %s, '改正投料', %s, %s)""",
            (batch_id, body.input_g, residue_g, user["username"], now),
        )
        conn.commit()
        row = conn.execute(
            """SELECT id, herb, doc, verdict, reason, created_by,
                      input_g::float8 AS input_g, residue_g::float8 AS residue_g
                 FROM batches WHERE id = %s""",
            (batch_id,),
        ).fetchone()
    return row
