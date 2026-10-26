import csv
import datetime
import hmac
import io
import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

from mailer import dispatch_post_lecture_emails, generate_otp, send_otp_email
from pdf_export import generate_class_attendance_pdf, generate_student_attendance_pdf
from model import (
    MODEL_PATH,
    decode_image_bytes,
    extract_embedding_for_image,
    load_model_if_exists,
    predict_with_model,
    train_model_background,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(APP_DIR, ".env"))
DATA_DIR = os.environ.get("DATA_DIR") or APP_DIR
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "attendance.db")
DATASET_DIR = os.path.join(DATA_DIR, "dataset")
TRAIN_STATUS_FILE = os.path.join(DATA_DIR, "train_status.json")
os.makedirs(DATASET_DIR, exist_ok=True)

DEFAULT_SECRET_KEY = "biobytes-default-session-secret-key-32b"
DEFAULT_ADMIN_PASSWORD = "admin123"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or DEFAULT_SECRET_KEY,
    ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME") or "admin",
    ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(minutes=30),
)
if len(app.config["ADMIN_PASSWORD"]) < 6:
    raise RuntimeError("ADMIN_PASSWORD must be at least 6 characters long.")

is_production = os.environ.get("FLASK_DEBUG") != "1" and (
    "RENDER" in os.environ or os.environ.get("COOKIE_SECURE") == "1"
)
if is_production:
    if app.config["SECRET_KEY"] == DEFAULT_SECRET_KEY:
        raise RuntimeError("Production deployment error: Default SECRET_KEY is not allowed in production. Set a secure SECRET_KEY.")
    if app.config["ADMIN_PASSWORD"] == DEFAULT_ADMIN_PASSWORD:
        raise RuntimeError("Production deployment error: Default ADMIN_PASSWORD ('admin123') is not allowed in production. Set a secure ADMIN_PASSWORD.")

CSRFProtect(app)
training_lock = threading.Lock()


@contextmanager
def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with connect_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                class_date TEXT NOT NULL,
                class_time TEXT NOT NULL,
                faculty TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        class_columns = {row["name"] for row in conn.execute("PRAGMA table_info(classes)").fetchall()}
        if "class_date" not in class_columns:
            conn.execute("ALTER TABLE classes ADD COLUMN class_date TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """UPDATE classes
                   SET class_date = COALESCE(NULLIF(date(created_at), ''), date('now'))
                   WHERE class_date = ''"""
            )
        if "end_time" not in class_columns:
            conn.execute("ALTER TABLE classes ADD COLUMN end_time TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """UPDATE classes
                   SET end_time = CASE
                       WHEN time(class_time, '+1 hour') IS NULL THEN '23:59'
                       WHEN class_time >= '23:00' THEN '23:59'
                       ELSE time(class_time, '+1 hour')
                   END
                   WHERE end_time = ''"""
            )
        if "status" not in class_columns:
            conn.execute("ALTER TABLE classes ADD COLUMN status TEXT NOT NULL DEFAULT 'scheduled'")
        if "room" not in class_columns:
            conn.execute("ALTER TABLE classes ADD COLUMN room TEXT NOT NULL DEFAULT ''")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                roll TEXT,
                class TEXT,
                section TEXT,
                reg_no TEXT,
                email TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT
            )"""
        )
        student_columns = {row["name"] for row in conn.execute("PRAGMA table_info(students)").fetchall()}
        if "email" not in student_columns:
            conn.execute("ALTER TABLE students ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "password_hash" not in student_columns:
            conn.execute("ALTER TABLE students ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER,
                name TEXT,
                timestamp TEXT,
                class_id INTEGER,
                email_sent INTEGER NOT NULL DEFAULT 0,
                email_sent_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        attendance_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(attendance)").fetchall()
        }
        if "class_id" not in attendance_columns:
            conn.execute("ALTER TABLE attendance ADD COLUMN class_id INTEGER")
        if "email_sent" not in attendance_columns:
            conn.execute("ALTER TABLE attendance ADD COLUMN email_sent INTEGER NOT NULL DEFAULT 0")
        if "email_sent_at" not in attendance_columns:
            conn.execute("ALTER TABLE attendance ADD COLUMN email_sent_at TEXT NOT NULL DEFAULT ''")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS attendance_daily (
                student_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                class_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (student_id, attendance_date, class_id)
            )"""
        )
        daily_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(attendance_daily)").fetchall()
        }
        if "class_id" not in daily_columns:
            conn.execute("ALTER TABLE attendance_daily RENAME TO attendance_daily_legacy")
            conn.execute(
                """CREATE TABLE attendance_daily (
                    student_id INTEGER NOT NULL,
                    attendance_date TEXT NOT NULL,
                    class_id INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (student_id, attendance_date, class_id)
                )"""
            )
            conn.execute(
                """INSERT OR IGNORE INTO attendance_daily (student_id, attendance_date, class_id)
                   SELECT student_id, attendance_date, 0 FROM attendance_daily_legacy"""
            )
            conn.execute("DROP TABLE attendance_daily_legacy")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS student_faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                image BLOB NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO attendance_daily (student_id, attendance_date, class_id)
               SELECT student_id, date(timestamp), COALESCE(class_id, 0) FROM attendance
               WHERE student_id IS NOT NULL AND date(timestamp) IS NOT NULL"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL
            )"""
        )
        existing_admins = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
        if existing_admins == 0 and app.config.get("ADMIN_USERNAME") and app.config.get("ADMIN_PASSWORD"):
            initial_email = (
                os.environ.get("ADMIN_EMAIL", "").strip()
                or os.environ.get("SMTP_USER", "").strip()
                or "admin@presentsync.edu"
            )
            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO admins (username, email, password_hash, created_by, created_at)
                   VALUES (?, ?, ?, 'system', ?)""",
                (
                    app.config["ADMIN_USERNAME"],
                    initial_email,
                    generate_password_hash(app.config["ADMIN_PASSWORD"]),
                    created_at,
                ),
            )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS otp_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                purpose TEXT NOT NULL,
                user_id INTEGER,
                role TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )"""
        )

with app.app_context():
    if not app.config.get("TESTING"):
        try:
            init_db()
        except Exception:
            pass


def migrate_dataset_images():
    if not os.path.isdir(DATASET_DIR):
        return
    for student_folder in os.listdir(DATASET_DIR):
        if not student_folder.isdigit():
            continue
        folder = os.path.join(DATASET_DIR, student_folder)
        if not os.path.isdir(folder):
            continue
        files = [
            os.path.join(folder, filename)
            for filename in os.listdir(folder)
            if filename.lower().endswith((".jpg", ".jpeg", ".png"))
        ]
        if not files:
            continue
        migrated_files = []
        with connect_db() as conn:
            student = conn.execute("SELECT 1 FROM students WHERE id = ?", (int(student_folder),)).fetchone()
            existing = conn.execute(
                "SELECT 1 FROM student_faces WHERE student_id = ? LIMIT 1", (int(student_folder),)
            ).fetchone()
            if student and not existing:
                created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                for path in files:
                    with open(path, "rb") as image_file:
                        image = image_file.read()
                    if decode_image_bytes(image) is None:
                        app.logger.warning("Skipping invalid legacy face image: %s", path)
                        continue
                    conn.execute(
                        "INSERT INTO student_faces (student_id, image, created_at) VALUES (?, ?, ?)",
                        (int(student_folder), image, created_at),
                    )
                    migrated_files.append(path)
        for path in migrated_files:
            if os.path.isfile(path):
                os.remove(path)
        if migrated_files and not os.listdir(folder):
            os.rmdir(folder)


def write_train_status(status):
    temp_path = TRAIN_STATUS_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as status_file:
        json.dump(status, status_file)
    os.replace(temp_path, TRAIN_STATUS_FILE)


def read_train_status():
    if not os.path.exists(TRAIN_STATUS_FILE):
        return {"running": False, "progress": 0, "status": "idle", "message": "No training yet."}
    try:
        with open(TRAIN_STATUS_FILE, "r", encoding="utf-8") as status_file:
            status = json.load(status_file)
        if not isinstance(status, dict):
            raise ValueError("Training status must be a JSON object.")
        return status
    except (OSError, ValueError, json.JSONDecodeError):
        app.logger.exception("Could not read training status")
        return {"running": False, "progress": 0, "status": "error", "message": "Training status file is invalid."}


def run_training():
    try:
        train_model_background(
            DB_PATH,
            lambda progress, message: write_train_status(
                {"running": True, "progress": progress, "status": "running", "message": message}
            ),
        )
        status = read_train_status()
        if status.get("running"):
            write_train_status(
                {
                    "running": False,
                    "progress": 100,
                    "status": "completed",
                    "message": status.get("message", "Training complete."),
                }
            )
    except Exception as exc:
        app.logger.exception("Model training failed")
        write_train_status(
            {"running": False, "progress": 0, "status": "error", "message": f"Training failed: {exc}"}
        )
    finally:
        training_lock.release()


init_db()
migrate_dataset_images()
initial_status = read_train_status()
if initial_status.get("running"):
    write_train_status(
        {
            "running": False,
            "progress": initial_status.get("progress", 0),
            "status": "error",
            "message": "The previous server stopped during training. Start training again.",
        }
    )


@app.before_request
def require_login():
    if request.endpoint in {"login", "verify_otp", "resend_otp", "static", "healthz"}:
        return None

    is_admin = session.get("admin_authenticated") or session.get("role") == "admin"
    is_student = session.get("role") == "student" and session.get("student_id")

    if request.path.startswith("/student/"):
        if is_student or is_admin:
            return None
        return redirect(url_for("login", next=request.path))

    if is_admin:
        return None

    if request.path.startswith(("/students", "/recognize_face", "/train_", "/attendance_", "/upload_face")):
        return jsonify({"error": "Authentication required."}), 401
    return redirect(url_for("login", next=request.path))


def valid_next_url(target):
    if not target or "\\" in target:
        return False
    parsed = urlsplit(target)
    return parsed.scheme == "" and parsed.netloc == "" and parsed.path.startswith("/") and not parsed.path.startswith("//")


def mask_email(email):
    if not email or "@" not in email:
        return email or "your registered email"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def clean_expired_otps():
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with connect_db() as conn:
            conn.execute("DELETE FROM otp_verifications WHERE expires_at < ?", (now_iso,))
    except Exception:
        pass


def find_admin(identifier):
    if not identifier:
        return None
    with connect_db() as conn:
        admin = conn.execute(
            """SELECT id, username, email, password_hash, created_by, created_at
               FROM admins
               WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) OR LOWER(TRIM(email)) = LOWER(TRIM(?))
               LIMIT 1""",
            (identifier, identifier),
        ).fetchone()
        if admin:
            return dict(admin)

    # Fallback to .env config if username matches
    if app.config.get("ADMIN_USERNAME") and hmac.compare_digest(
        identifier.encode("utf-8"), app.config["ADMIN_USERNAME"].encode("utf-8")
    ):
        return {
            "id": 1,
            "username": app.config["ADMIN_USERNAME"],
            "email": (
                os.environ.get("ADMIN_EMAIL", "").strip()
                or os.environ.get("SMTP_USER", "").strip()
                or "admin@presentsync.edu"
            ),
            "password_hash": None,
            "created_by": "system",
        }
    return None


def verify_admin_password(admin_data, input_password):
    if not admin_data or not input_password:
        return False
    if admin_data.get("password_hash"):
        if check_password_hash(admin_data["password_hash"], input_password):
            return True
    if app.config.get("ADMIN_USERNAME") and admin_data.get("username") == app.config["ADMIN_USERNAME"]:
        if app.config.get("ADMIN_PASSWORD") and hmac.compare_digest(
            input_password.encode("utf-8"), app.config["ADMIN_PASSWORD"].encode("utf-8")
        ):
            return True
    return False


@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_authenticated") or session.get("role") == "admin":
        return redirect(url_for("index"))
    if session.get("role") == "student":
        return redirect(url_for("student_dashboard"))

    error = None
    active_tab = request.args.get("tab", "student")

    if request.method == "POST":
        login_type = request.form.get("login_type", "").strip().lower()
        username_or_id = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_url = request.args.get("next") or request.form.get("next") or ""

        if not login_type:
            # Auto-detect login type if not passed
            admin_cand = find_admin(username_or_id)
            login_type = "admin" if admin_cand else "student"

        active_tab = login_type

        # 1. Admin login branch
        if login_type == "admin":
            admin = find_admin(username_or_id)
            if admin and verify_admin_password(admin, password):
                clean_expired_otps()
                admin_email = admin.get("email") or "admin@presentsync.edu"
                otp_code = generate_otp(6)
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                expires_dt = now_dt + datetime.timedelta(minutes=10)

                with connect_db() as conn:
                    conn.execute(
                        "DELETE FROM otp_verifications WHERE email = ? AND role = 'admin'",
                        (admin_email,),
                    )
                    conn.execute(
                        """INSERT INTO otp_verifications (email, otp_code, purpose, user_id, role, expires_at, created_at, attempts)
                           VALUES (?, ?, 'Admin Login', ?, 'admin', ?, ?, 0)""",
                        (admin_email, otp_code, admin["id"], expires_dt.isoformat(), now_dt.isoformat()),
                    )

                send_otp_email(admin_email, otp_code, purpose="Admin Sign In")

                session["pending_otp"] = {
                    "email": admin_email,
                    "masked_email": mask_email(admin_email),
                    "role": "admin",
                    "user_id": admin["id"],
                    "name": admin["username"],
                    "login_type": "admin",
                    "dev_otp": otp_code,
                    "next": next_url,
                }
                return redirect(url_for("verify_otp"))
            else:
                error = "Invalid administrator username or password."

        # 2. Student login branch
        elif login_type == "student":
            with connect_db() as conn:
                student = conn.execute(
                    """SELECT id, name, roll, email, password_hash, class, section, reg_no
                       FROM students
                       WHERE (LOWER(TRIM(roll)) = LOWER(TRIM(?)) OR LOWER(TRIM(email)) = LOWER(TRIM(?)))
                       LIMIT 1""",
                    (username_or_id, username_or_id),
                ).fetchone()

            if student:
                pwd_hash = student["password_hash"]
                valid_student = False
                if pwd_hash:
                    valid_student = check_password_hash(pwd_hash, password)
                else:
                    valid_student = password in {student["roll"], student["reg_no"], "123456"}

                if valid_student:
                    student_email = (
                        student["email"].strip()
                        if student["email"]
                        else f"{student['roll'].lower()}@school.edu"
                    )
                    clean_expired_otps()
                    otp_code = generate_otp(6)
                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                    expires_dt = now_dt + datetime.timedelta(minutes=10)

                    with connect_db() as conn:
                        conn.execute(
                            "DELETE FROM otp_verifications WHERE email = ? AND role = 'student'",
                            (student_email,),
                        )
                        conn.execute(
                            """INSERT INTO otp_verifications (email, otp_code, purpose, user_id, role, expires_at, created_at, attempts)
                               VALUES (?, ?, 'Student Login', ?, 'student', ?, ?, 0)""",
                            (student_email, otp_code, student["id"], expires_dt.isoformat(), now_dt.isoformat()),
                        )

                    send_otp_email(student_email, otp_code, purpose="Student Sign In")

                    session["pending_otp"] = {
                        "email": student_email,
                        "masked_email": mask_email(student_email),
                        "role": "student",
                        "user_id": student["id"],
                        "name": student["name"],
                        "login_type": "student",
                        "dev_otp": otp_code,
                        "next": next_url,
                    }
                    return redirect(url_for("verify_otp"))
                else:
                    error = "Invalid roll number or password."
            else:
                error = "Student not found with this roll number or email."
        else:
            error = "Invalid login type specified."

    return render_template("login.html", error=error, active_tab=active_tab)


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    pending = session.get("pending_otp")
    if not pending:
        return redirect(url_for("login"))

    error = None
    resent = request.args.get("resent") == "1"

    if request.method == "POST":
        entered_code = request.form.get("otp", "").strip()
        if not entered_code:
            error = "Please enter the 6-digit verification code."
        else:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with connect_db() as conn:
                record = conn.execute(
                    """SELECT id, otp_code, expires_at, attempts
                       FROM otp_verifications
                       WHERE email = ? AND role = ? AND expires_at >= ?
                       ORDER BY id DESC LIMIT 1""",
                    (pending["email"], pending["role"], now_iso),
                ).fetchone()

                if not record:
                    error = "Verification code has expired or is invalid. Please request a new code."
                elif record["attempts"] >= 5:
                    error = "Too many incorrect attempts. Please request a new verification code."
                elif record["otp_code"] != entered_code:
                    conn.execute(
                        "UPDATE otp_verifications SET attempts = attempts + 1 WHERE id = ?",
                        (record["id"],),
                    )
                    error = "Incorrect verification code. Please check your email and try again."
                else:
                    conn.execute("DELETE FROM otp_verifications WHERE id = ?", (record["id"],))

                    next_url = pending.get("next")
                    role = pending["role"]
                    user_id = pending["user_id"]
                    user_name = pending["name"]

                    session.clear()
                    session.permanent = True

                    if role == "admin":
                        session["admin_authenticated"] = True
                        session["role"] = "admin"
                        session["admin_id"] = user_id
                        session["username"] = user_name
                        return redirect(next_url if valid_next_url(next_url) else url_for("index"))
                    else:
                        session["role"] = "student"
                        session["student_id"] = user_id
                        session["student_name"] = user_name
                        return redirect(next_url if valid_next_url(next_url) else url_for("student_dashboard"))

    return render_template(
        "verify_otp.html",
        pending=pending,
        error=error,
        resent=resent,
        dev_otp=pending.get("dev_otp"),
    )


@app.route("/resend-otp", methods=["POST", "GET"])
def resend_otp():
    pending = session.get("pending_otp")
    if not pending:
        return redirect(url_for("login"))

    clean_expired_otps()
    new_code = generate_otp(6)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    expires_dt = now_dt + datetime.timedelta(minutes=10)
    purpose = f"{pending['role'].capitalize()} Sign In"

    with connect_db() as conn:
        conn.execute(
            "DELETE FROM otp_verifications WHERE email = ? AND role = ?",
            (pending["email"], pending["role"]),
        )
        conn.execute(
            """INSERT INTO otp_verifications (email, otp_code, purpose, user_id, role, expires_at, created_at, attempts)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                pending["email"],
                new_code,
                purpose,
                pending["user_id"],
                pending["role"],
                expires_dt.isoformat(),
                now_dt.isoformat(),
            ),
        )

    send_otp_email(pending["email"], new_code, purpose=purpose)
    pending["dev_otp"] = new_code
    session["pending_otp"] = pending
    return redirect(url_for("verify_otp", resent=1))


@app.route("/manage_admins", methods=["GET", "POST"])
def manage_admins():
    if not (session.get("admin_authenticated") or session.get("role") == "admin"):
        return redirect(url_for("login", next=request.path))

    error = None
    created = request.args.get("created") == "1"
    verify_mode = request.args.get("verify") == "1"
    pending_admin = session.get("pending_new_admin")

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "initiate":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username or not email or not password:
                error = "All fields (username, email, password) are required."
            elif len(password) < 6:
                error = "Password must be at least 6 characters long."
            elif password != confirm_password:
                error = "Passwords do not match."
            elif "@" not in email:
                error = "Please provide a valid email address."
            else:
                with connect_db() as conn:
                    existing = conn.execute(
                        "SELECT username, email FROM admins WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
                        (username, email),
                    ).fetchone()

                if existing:
                    error = "An administrator with that username or email already exists."
                else:
                    clean_expired_otps()
                    otp_code = generate_otp(6)
                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                    expires_dt = now_dt + datetime.timedelta(minutes=10)

                    payload = json.dumps(
                        {
                            "username": username,
                            "email": email,
                            "password_hash": generate_password_hash(password),
                            "created_by": session.get("username", "admin"),
                        }
                    )

                    with connect_db() as conn:
                        conn.execute(
                            "DELETE FROM otp_verifications WHERE email = ? AND purpose = 'New Admin Verification'",
                            (email,),
                        )
                        conn.execute(
                            """INSERT INTO otp_verifications (email, otp_code, purpose, role, payload, expires_at, created_at, attempts)
                               VALUES (?, ?, 'New Admin Verification', 'admin', ?, ?, ?, 0)""",
                            (email, otp_code, payload, expires_dt.isoformat(), now_dt.isoformat()),
                        )

                    send_otp_email(email, otp_code, purpose="New Administrator Account Verification")

                    session["pending_new_admin"] = {
                        "username": username,
                        "email": email,
                        "masked_email": mask_email(email),
                        "dev_otp": otp_code,
                    }
                    return redirect(url_for("manage_admins", verify=1))

        elif action == "verify":
            otp_code = request.form.get("otp", "").strip()
            if not pending_admin:
                error = "No pending administrator invitation found. Please initiate again."
            elif not otp_code:
                error = "Please enter the verification code sent to the email."
            else:
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                with connect_db() as conn:
                    record = conn.execute(
                        """SELECT id, otp_code, payload, attempts
                           FROM otp_verifications
                           WHERE email = ? AND purpose = 'New Admin Verification' AND expires_at >= ?
                           ORDER BY id DESC LIMIT 1""",
                        (pending_admin["email"], now_iso),
                    ).fetchone()

                    if not record:
                        error = "Verification code expired or not found. Please initiate again."
                    elif record["attempts"] >= 5:
                        error = "Too many incorrect attempts. Please cancel and re-create."
                    elif record["otp_code"] != otp_code:
                        conn.execute(
                            "UPDATE otp_verifications SET attempts = attempts + 1 WHERE id = ?",
                            (record["id"],),
                        )
                        error = "Invalid verification code. Please check the email and try again."
                    else:
                        data = json.loads(record["payload"])
                        conn.execute(
                            """INSERT INTO admins (username, email, password_hash, created_by, created_at)
                               VALUES (?, ?, ?, ?, ?)""",
                            (data["username"], data["email"], data["password_hash"], data["created_by"], now_iso),
                        )
                        conn.execute("DELETE FROM otp_verifications WHERE id = ?", (record["id"],))

                        session.pop("pending_new_admin", None)
                        return redirect(url_for("manage_admins", created=1))

        elif action == "cancel":
            session.pop("pending_new_admin", None)
            return redirect(url_for("manage_admins"))

    with connect_db() as conn:
        admins = conn.execute(
            "SELECT id, username, email, created_by, created_at FROM admins ORDER BY id ASC"
        ).fetchall()

    return render_template(
        "manage_admins.html",
        admins=[dict(a) for a in admins],
        error=error,
        created=created,
        verify_mode=verify_mode,
        pending_admin=session.get("pending_new_admin"),
    )


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    with connect_db() as conn:
        classes = conn.execute("SELECT id, name, class_date FROM classes ORDER BY class_date DESC, name").fetchall()
        cohorts = conn.execute(
            "SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND trim(class) != '' ORDER BY class"
        ).fetchall()
        sections = conn.execute(
            "SELECT DISTINCT section FROM students WHERE section IS NOT NULL AND trim(section) != '' ORDER BY section"
        ).fetchall()
    return render_template("index.html", classes=classes, cohorts=cohorts, sections=sections)


@app.route("/classes", methods=["GET", "POST"])
def classes_page():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        class_date = request.form.get("class_date", "").strip()
        class_time = request.form.get("class_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        faculty = request.form.get("faculty", "").strip()
        room = request.form.get("room", "").strip()
        try:
            valid_date = datetime.date.fromisoformat(class_date).isoformat() == class_date
        except ValueError:
            valid_date = False
        try:
            datetime.datetime.strptime(class_time, "%H:%M")
            valid_time = True
        except ValueError:
            valid_time = False
        try:
            datetime.datetime.strptime(end_time, "%H:%M")
            valid_end_time = end_time > class_time
        except ValueError:
            valid_end_time = False
        if not name or not valid_date or not valid_time or not valid_end_time or not faculty:
            error = "Provide a class name, valid date and start/end times (end after start), and faculty."
        else:
            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with connect_db() as conn:
                conn.execute(
                    """INSERT INTO classes (name, class_date, class_time, end_time, faculty, room, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?)""",
                    (name, class_date, class_time, end_time, faculty, room, created_at),
                )
            return redirect(url_for("classes_page", created=1))

    now = datetime.datetime.now()
    today = now.date().isoformat()
    with connect_db() as conn:
        classes = conn.execute(
            """SELECT c.id, c.name, c.class_date, c.class_time, c.end_time, c.faculty,
                      c.status, c.room,
                      COUNT(DISTINCT a.student_id) AS attended_count
               FROM classes c
               LEFT JOIN attendance a
                 ON a.class_id = c.id AND (date(a.timestamp) = c.class_date OR date(a.timestamp, 'localtime') = c.class_date)
               GROUP BY c.id
               ORDER BY c.id DESC""",
        ).fetchall()
    classes_list = []
    for class_row in classes:
        item = dict(class_row)
        item["schedule_status"] = (
            "Completed"
            if item.get("status") == "completed"
            else "Past - read-only"
            if datetime.datetime.combine(
                datetime.date.fromisoformat(item["class_date"]),
                datetime.time.fromisoformat(item["end_time"]),
            ) <= now
            else "Attendance open"
            if datetime.datetime.combine(
                datetime.date.fromisoformat(item["class_date"]),
                datetime.time.fromisoformat(item["class_time"]),
            ) <= now
            else "Upcoming"
        )
        classes_list.append(item)
    classes = classes_list
    return render_template(
        "classes.html",
        classes=classes,
        error=error,
        created=request.args.get("created") == "1",
        completed=request.args.get("completed") == "1",
        today=today,
    )


@app.route("/classes/<int:class_id>/complete", methods=["POST"])
def complete_class(class_id):
    with connect_db() as conn:
        cls = conn.execute("SELECT id, name FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not cls:
            abort(404, description="Class not found.")
        conn.execute("UPDATE classes SET status = 'completed' WHERE id = ?", (class_id,))

    # Trigger background email delivery for all attended students
    threading.Thread(
        target=dispatch_post_lecture_emails,
        args=(class_id, connect_db),
        daemon=True,
        name=f"dispatch-emails-{class_id}",
    ).start()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"success": True, "message": "Class marked completed and attendance emails are being dispatched."})
    return redirect(url_for("classes_page", completed=1))


@app.route("/attendance_stats")
def attendance_stats():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    period = request.args.get("period", "30")
    if period not in {"7", "30", "90", "all"}:
        abort(400, description="period must be 7, 30, 90, or all")
    class_id_value = request.args.get("class_id", "")
    class_id = None
    if class_id_value:
        if not class_id_value.isdigit() or int(class_id_value) < 1:
            abort(400, description="class_id must be a positive integer")
        class_id = int(class_id_value)
    cohort = request.args.get("cohort", "").strip()
    section = request.args.get("section", "").strip()
    if period == "all":
        first_date_conditions = []
        first_date_params = []
        if class_id is not None:
            first_date_conditions.append("class_id = ?")
            first_date_params.append(class_id)
        first_date_student_conditions = []
        if cohort:
            first_date_student_conditions.append("s.class = ?")
            first_date_params.append(cohort)
        if section:
            first_date_student_conditions.append("s.section = ?")
            first_date_params.append(section)
        if first_date_student_conditions:
            first_date_conditions.append(
                "EXISTS (SELECT 1 FROM students s WHERE s.id = attendance.student_id AND "
                + " AND ".join(first_date_student_conditions)
                + ")"
            )
        first_date_where = " WHERE " + " AND ".join(first_date_conditions) if first_date_conditions else ""
        with connect_db() as conn:
            first_date = conn.execute(
                f"SELECT MIN(date(timestamp)) FROM attendance{first_date_where}", first_date_params
            ).fetchone()[0]
        start_date = datetime.date.fromisoformat(first_date) if first_date else today
    else:
        start_date = today - datetime.timedelta(days=int(period) - 1)
    days = [start_date + datetime.timedelta(days=i) for i in range((today - start_date).days + 1)]
    conditions = ["date(timestamp) >= ?", "date(timestamp) <= ?"]
    params = [start_date.isoformat(), today.isoformat()]
    if class_id is not None:
        conditions.append("class_id = ?")
        params.append(class_id)
    student_conditions = []
    student_params = []
    if cohort:
        student_conditions.append("s.class = ?")
        student_params.append(cohort)
    if section:
        student_conditions.append("s.section = ?")
        student_params.append(section)
    if student_conditions:
        conditions.append(
            "EXISTS (SELECT 1 FROM students s WHERE s.id = attendance.student_id AND "
            + " AND ".join(student_conditions)
            + ")"
        )
        params.extend(student_params)
    with connect_db() as conn:
        rows = conn.execute(
            f"""SELECT date(timestamp) AS attendance_date, COUNT(*) AS count
                FROM attendance
                WHERE {" AND ".join(conditions)}
                GROUP BY date(timestamp)""",
            params,
        ).fetchall()
    counts_by_date = {row["attendance_date"]: row["count"] for row in rows}
    return jsonify(
        {
            "dates": [day.strftime("%d-%b") for day in days],
            "counts": [counts_by_date.get(day.isoformat(), 0) for day in days],
            "period": period,
        }
    )


@app.route("/attendance_analysis")
def attendance_analysis():
    period = request.args.get("period", "30")
    if period not in {"7", "30", "90", "all"}:
        abort(400, description="period must be 7, 30, 90, or all")
    class_id_value = request.args.get("class_id", "")
    class_id = None
    if class_id_value:
        if not class_id_value.isdigit() or int(class_id_value) < 1:
            abort(400, description="class_id must be a positive integer")
        class_id = int(class_id_value)
    cohort = request.args.get("cohort", "").strip()
    section = request.args.get("section", "").strip()
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start_date = today - datetime.timedelta(days=int(period) - 1) if period != "all" else None
    attendance_conditions = []
    attendance_params = []
    if start_date:
        attendance_conditions.append("date(a.timestamp) >= ?")
        attendance_params.append(start_date.isoformat())
    attendance_conditions.append("date(a.timestamp) <= ?")
    attendance_params.append(today.isoformat())
    if class_id is not None:
        attendance_conditions.append("a.class_id = ?")
        attendance_params.append(class_id)
    attendance_filter = " AND ".join(attendance_conditions)
    student_conditions = []
    student_params = []
    if cohort:
        student_conditions.append("s.class = ?")
        student_params.append(cohort)
    if section:
        student_conditions.append("s.section = ?")
        student_params.append(section)
    student_filter = " WHERE " + " AND ".join(student_conditions) if student_conditions else ""
    with connect_db() as conn:
        students = conn.execute(
            f"""SELECT s.id, s.name, s.roll, s.class AS cohort, s.section,
                      COUNT(DISTINCT a.id) AS attendance_count,
                      COUNT(DISTINCT CASE
                          WHEN a.id IS NOT NULL AND a.class_id IS NULL THEN 'date:' || date(a.timestamp)
                          WHEN a.id IS NOT NULL THEN 'class:' || a.class_id
                      END) AS sessions_attended,
                      COUNT(DISTINCT date(a.timestamp)) AS active_days,
                      (SELECT MAX(date(history.timestamp)) FROM attendance history
                       WHERE history.student_id = s.id) AS last_attended
               FROM students s
               LEFT JOIN attendance a ON a.student_id = s.id AND {attendance_filter}
               {student_filter}
               GROUP BY s.id, s.name, s.roll, s.class, s.section
               ORDER BY attendance_count DESC, s.name""",
            attendance_params + student_params,
        ).fetchall()
        trend_conditions = [
            "date(a.timestamp) >= date(limits.today, '-13 days')",
            "date(a.timestamp) <= limits.today",
        ]
        trend_params = []
        if class_id is not None:
            trend_conditions.append("a.class_id = ?")
            trend_params.append(class_id)
        if cohort:
            trend_conditions.append("s.class = ?")
            trend_params.append(cohort)
        if section:
            trend_conditions.append("s.section = ?")
            trend_params.append(section)
        trends = conn.execute(
            f"""WITH limits AS (SELECT date(?) AS today)
                SELECT a.student_id,
                       COUNT(DISTINCT CASE
                           WHEN date(a.timestamp) >= date(limits.today, '-6 days')
                           THEN date(a.timestamp) END) AS last_7_days,
                       COUNT(DISTINCT CASE
                           WHEN date(a.timestamp) < date(limits.today, '-6 days')
                           THEN date(a.timestamp) END) AS previous_7_days
                FROM attendance a
                JOIN students s ON s.id = a.student_id
                CROSS JOIN limits
                WHERE {" AND ".join(trend_conditions)}
                GROUP BY a.student_id""",
            [today.isoformat()] + trend_params,
        ).fetchall()
        trends_by_student = {row["student_id"]: dict(row) for row in trends}
        class_student_conditions = []
        class_params = attendance_params.copy()
        if cohort:
            class_student_conditions.append("s.class = ?")
            class_params.append(cohort)
        if section:
            class_student_conditions.append("s.section = ?")
            class_params.append(section)
        student_scope = ""
        if class_student_conditions:
            student_scope = (
                " AND EXISTS (SELECT 1 FROM students s WHERE s.id = a.student_id AND "
                + " AND ".join(class_student_conditions)
                + ")"
            )
        class_conditions = ["c.class_date <= ?"]
        class_where_params = [today.isoformat()]
        if start_date:
            class_conditions.append("c.class_date >= ?")
            class_where_params.append(start_date.isoformat())
        if class_id is not None:
            class_conditions.append("c.id = ?")
            class_where_params.append(class_id)
        classes = conn.execute(
            f"""SELECT c.id, c.name, c.class_date,
                      COUNT(DISTINCT a.student_id) AS attendance_count
               FROM classes c
               LEFT JOIN attendance a ON a.class_id = c.id AND {attendance_filter}{student_scope}
               WHERE {" AND ".join(class_conditions)}
               GROUP BY c.id, c.name, c.class_date
               ORDER BY c.class_date, c.name, c.id""",
            class_params + class_where_params,
        ).fetchall()
        summary = conn.execute(
            f"""SELECT COUNT(DISTINCT a.id) AS attendance_count,
                       COUNT(DISTINCT CASE
                           WHEN a.class_id IS NULL THEN 'date:' || date(a.timestamp)
                           ELSE 'class:' || a.class_id
                       END) AS session_count
                FROM attendance a
                LEFT JOIN students s ON s.id = a.student_id
                WHERE {attendance_filter}
                {"AND s.class = ?" if cohort else ""}
                {"AND s.section = ?" if section else ""}""",
            attendance_params + student_params,
        ).fetchone()
        student_count = conn.execute(
            f"SELECT COUNT(*) FROM students s{student_filter}", student_params
        ).fetchone()[0]
    return jsonify(
        {
            "students": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "roll": row["roll"],
                    "cohort": row["cohort"],
                    "section": row["section"],
                    "attendance_count": row["attendance_count"],
                    "sessions_attended": row["sessions_attended"],
                    "active_days": row["active_days"],
                    "last_attended": row["last_attended"],
                    "last_7_days": trends_by_student.get(row["id"], {}).get("last_7_days", 0),
                    "previous_7_days": trends_by_student.get(row["id"], {}).get("previous_7_days", 0),
                }
                for row in students
            ],
            "classes": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "class_date": row["class_date"],
                    "attendance_count": row["attendance_count"],
                }
                for row in classes
            ],
            "summary": {
                "student_count": student_count,
                "attendance_count": summary["attendance_count"],
                "session_count": summary["session_count"],
                "period": period,
            },
        }
    )


@app.route("/add_student", methods=["GET", "POST"])
def add_student():
    if request.method == "GET":
        return render_template("add_student.html")
    data = request.form
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400
    email = data.get("email", "").strip()
    roll = data.get("roll", "").strip()
    reg_no = data.get("reg_no", "").strip()
    raw_password = data.get("password", "").strip() or roll or reg_no or "123456"
    pwd_hash = generate_password_hash(raw_password)
    with connect_db() as conn:
        cursor = conn.execute(
            """INSERT INTO students (name, roll, class, section, reg_no, email, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                roll,
                data.get("class", "").strip(),
                data.get("sec", "").strip(),
                reg_no,
                email,
                pwd_hash,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        student_id = cursor.lastrowid
    return jsonify({"student_id": student_id})


@app.route("/upload_face", methods=["POST"])
def upload_face():
    student_id = request.form.get("student_id", "")
    if not student_id.isdigit():
        return jsonify({"error": "A valid student_id is required."}), 400
    files = request.files.getlist("images[]")
    if not files or len(files) > 50:
        return jsonify({"error": "Upload between 1 and 50 face images."}), 400
    images = [uploaded_file.read() for uploaded_file in files if uploaded_file.filename]
    if not images or any(decode_image_bytes(image) is None for image in images):
        return jsonify({"error": "Upload valid webcam photos in JPEG or PNG format."}), 400
    with connect_db() as conn:
        exists = conn.execute("SELECT 1 FROM students WHERE id = ?", (int(student_id),)).fetchone()
        if not exists:
            return jsonify({"error": "Student not found."}), 404
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO student_faces (student_id, image, created_at) VALUES (?, ?, ?)",
            [(int(student_id), image, created_at) for image in images],
        )
    return jsonify({"saved": len(images), "storage": "database"})


@app.route("/train_model", methods=["POST"])
def train_model_route():
    if not training_lock.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 202
    write_train_status({"running": True, "progress": 0, "status": "running", "message": "Starting training."})
    thread = threading.Thread(target=run_training, name="attendance-model-training", daemon=True)
    try:
        thread.start()
    except Exception:
        training_lock.release()
        write_train_status(
            {"running": False, "progress": 0, "status": "error", "message": "Could not start training."}
        )
        app.logger.exception("Could not start model training thread")
        return jsonify({"error": "Could not start training."}), 500
    return jsonify({"status": "started"}), 202


@app.route("/train_status")
def train_status():
    return jsonify(read_train_status())


@app.route("/mark_attendance")
def mark_attendance_page():
    now = datetime.datetime.now()
    with connect_db() as conn:
        classes = conn.execute(
            """SELECT c.id, c.name, c.class_date, c.class_time, c.end_time, c.faculty,
                      COUNT(DISTINCT a.student_id) AS attended_count
               FROM classes c
               LEFT JOIN attendance a
                 ON a.class_id = c.id AND (date(a.timestamp) = c.class_date OR date(a.timestamp, 'localtime') = c.class_date)
               GROUP BY c.id
               ORDER BY c.name"""
        ).fetchall()
    classes = [
        {
            **dict(class_item),
            "attendance_open": (
                datetime.datetime.combine(
                    datetime.date.fromisoformat(class_item["class_date"]),
                    datetime.time.fromisoformat(class_item["class_time"]),
                )
                <= now
                < datetime.datetime.combine(
                    datetime.date.fromisoformat(class_item["class_date"]),
                    datetime.time.fromisoformat(class_item["end_time"]),
                )
            ),
        }
        for class_item in classes
    ]
    return render_template(
        "mark_attendance.html",
        classes=classes,
        has_open_class=any(class_item["attendance_open"] for class_item in classes),
    )


@app.route("/recognize_face", methods=["POST"])
def recognize_face():
    if "image" not in request.files:
        return jsonify({"recognized": False, "error": "No image provided."}), 400
    class_id_value = request.form.get("class_id")
    if class_id_value:
        if not class_id_value.isdigit() or int(class_id_value) < 1:
            return jsonify({"recognized": False, "error": "A valid class_id is required."}), 400
        class_id = int(class_id_value)
    else:
        class_id = 0
    class_date = None
    class_end = None
    if class_id:
        with connect_db() as conn:
            class_row = conn.execute(
                "SELECT class_date, class_time, end_time FROM classes WHERE id = ?", (class_id,)
            ).fetchone()
        if class_row is None:
            return jsonify({"recognized": False, "error": "Class not found."}), 404
        now = datetime.datetime.now()
        class_date = class_row["class_date"]
        class_start = datetime.datetime.combine(
            datetime.date.fromisoformat(class_date),
            datetime.time.fromisoformat(class_row["class_time"]),
        )
        class_end = datetime.datetime.combine(
            datetime.date.fromisoformat(class_date),
            datetime.time.fromisoformat(class_row["end_time"]),
        )
        if now < class_start:
            return jsonify(
                {"recognized": False, "error": "Attendance for this class has not opened yet."}
            ), 403
        if now >= class_end:
            return jsonify(
                {
                    "recognized": False,
                    "error": "The attendance window for this class has closed.",
                    "attendance_window_closed": True,
                }
            ), 403
    try:
        embedding = extract_embedding_for_image(request.files["image"].stream)
        if embedding is None:
            return jsonify({"recognized": False, "error": "No face detected.", "unknown_face": False})
        model = load_model_if_exists()
        if model is None:
            return jsonify({"recognized": False, "error": "Model not trained."}), 503
        student_id, similarity = predict_with_model(model, embedding)
        if similarity < 0.363:
            return jsonify({
                "recognized": False,
                "similarity": float(similarity),
                "unknown_face": True,
                "error": "Face not recognized",
            })
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        today = timestamp.date().isoformat()
        with connect_db() as conn:
            student = conn.execute("SELECT name FROM students WHERE id = ?", (int(student_id),)).fetchone()
            if student is None:
                return jsonify({"recognized": False, "error": "Recognized student no longer exists."}), 404
            if class_end is not None and datetime.datetime.now() >= class_end:
                return jsonify(
                    {
                        "recognized": False,
                        "error": "The attendance window for this class has closed.",
                        "attendance_window_closed": True,
                    }
                ), 403
            guard = conn.execute(
                """INSERT OR IGNORE INTO attendance_daily (student_id, attendance_date, class_id)
                   VALUES (?, ?, ?)""",
                (int(student_id), class_date or today, class_id),
            )
            already_marked = guard.rowcount == 0
            if not already_marked:
                conn.execute(
                    "INSERT INTO attendance (student_id, name, timestamp, class_id) VALUES (?, ?, ?, ?)",
                    (int(student_id), student["name"], timestamp.isoformat(), class_id or None),
                )
            present_count = None
            if class_id:
                present_count = conn.execute(
                    """SELECT COUNT(DISTINCT student_id) FROM attendance
                       WHERE class_id = ? AND (date(timestamp) = ? OR date(timestamp, 'localtime') = ?)""",
                    (class_id, class_date, class_date),
                ).fetchone()[0]
        return jsonify(
            {
                "recognized": True,
                "student_id": int(student_id),
                "name": student["name"],
                "similarity": float(similarity),
                "already_marked": already_marked,
                "class_id": class_id or None,
                "present_count": present_count,
            }
        )
    except FileNotFoundError as exc:
        return jsonify({"recognized": False, "error": str(exc)}), 503
    except Exception:
        app.logger.exception("Face recognition failed")
        return jsonify({"recognized": False, "error": "Face recognition failed."}), 500


@app.route("/attendance_record")
def attendance_record():
    period = request.args.get("period", "all")
    class_id_value = request.args.get("class_id", "")
    class_id = None
    if class_id_value:
        if not class_id_value.isdigit() or int(class_id_value) < 1:
            abort(400, description="class_id must be a positive integer")
        class_id = int(class_id_value)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    query = """SELECT a.id, a.student_id, a.name, a.timestamp, c.name AS class_name
               FROM attendance a LEFT JOIN classes c ON c.id = a.class_id"""
    conditions = []
    params = []
    if period == "daily":
        conditions.append("date(a.timestamp) = ?")
        params.append(today.isoformat())
    elif period == "weekly":
        conditions.append("date(a.timestamp) >= ?")
        params.append((today - datetime.timedelta(days=7)).isoformat())
    elif period == "monthly":
        conditions.append("date(a.timestamp) >= ?")
        params.append((today - datetime.timedelta(days=30)).isoformat())
    elif period != "all":
        abort(400, description="period must be all, daily, weekly, or monthly")
    if class_id is not None:
        conditions.append("a.class_id = ?")
        params.append(class_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY a.timestamp DESC LIMIT 5000"
    with connect_db() as conn:
        classes = conn.execute("SELECT id, name FROM classes ORDER BY name").fetchall()
        if class_id is not None and not any(class_item["id"] == class_id for class_item in classes):
            abort(404, description="Class not found.")
        rows = conn.execute(query, params).fetchall()
    return render_template(
        "attendance_record.html",
        records=rows,
        period=period,
        classes=classes,
        selected_class_id=class_id,
    )


@app.route("/download_csv")
def download_csv():
    class_id_value = request.args.get("class_id", "")
    class_id = None
    if class_id_value:
        if not class_id_value.isdigit() or int(class_id_value) < 1:
            abort(400, description="class_id must be a positive integer")
        class_id = int(class_id_value)
    with connect_db() as conn:
        class_name = None
        if class_id is not None:
            class_row = conn.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
            if class_row is None:
                abort(404, description="Class not found.")
            class_name = class_row["name"]
        rows = conn.execute(
            """SELECT a.id, a.student_id, a.name, a.timestamp, COALESCE(c.name, '')
               FROM attendance a LEFT JOIN classes c ON c.id = a.class_id
               WHERE (? IS NULL OR a.class_id = ?)
               ORDER BY a.timestamp DESC""",
            (class_id, class_id),
        ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("id", "student_id", "name", "timestamp", "class"))
    writer.writerows(tuple(row) for row in rows)
    memory = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    memory.seek(0)
    filename = f"attendance_class_{class_id}.csv" if class_name else "attendance.csv"
    return send_file(memory, as_attachment=True, download_name=filename, mimetype="text/csv")


@app.route("/download_pdf")
def download_pdf():
    class_id_value = request.args.get("class_id", "")
    class_id = None
    if class_id_value:
        if not class_id_value.isdigit() or int(class_id_value) < 1:
            abort(400, description="class_id must be a positive integer")
        class_id = int(class_id_value)
    with connect_db() as conn:
        class_info = None
        if class_id is not None:
            class_row = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
            if class_row is None:
                abort(404, description="Class not found.")
            class_info = dict(class_row)
        rows = conn.execute(
            """SELECT a.id, a.student_id, a.name, a.timestamp, s.roll, s.class, s.section, COALESCE(c.name, '') AS class_name
               FROM attendance a
               LEFT JOIN students s ON s.id = a.student_id
               LEFT JOIN classes c ON c.id = a.class_id
               WHERE (? IS NULL OR a.class_id = ?)
               ORDER BY a.timestamp DESC""",
            (class_id, class_id),
        ).fetchall()
    try:
        pdf_buffer = generate_class_attendance_pdf(class_info, [dict(r) for r in rows])
        filename = f"attendance_class_{class_id}.pdf" if class_id else "attendance_all.pdf"
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")
    except Exception as exc:
        app.logger.exception("Could not generate class attendance PDF")
        abort(500, description=f"PDF generation failed: {exc}")


@app.route("/student/dashboard")
def student_dashboard():
    student_id = session.get("student_id")
    is_admin = session.get("admin_authenticated") or session.get("role") == "admin"
    if not student_id and not is_admin:
        return redirect(url_for("login"))

    if is_admin and request.args.get("student_id"):
        try:
            student_id = int(request.args.get("student_id"))
        except ValueError:
            abort(400, description="Invalid student_id")

    with connect_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            abort(404, description="Student not found.")

        records = conn.execute(
            """SELECT a.id, a.timestamp, c.id AS class_id, COALESCE(c.name, 'General Session') AS class_name,
                      c.class_date, c.class_time, c.end_time, c.faculty, c.room, a.email_sent
               FROM attendance a
               LEFT JOIN classes c ON c.id = a.class_id
               WHERE a.student_id = ?
               ORDER BY a.timestamp DESC""",
            (student_id,),
        ).fetchall()

        total_classes = conn.execute(
            "SELECT COUNT(*) FROM classes WHERE class_date <= date('now')"
        ).fetchone()[0]

    attended_count = len(records)
    total_sessions = max(total_classes, attended_count)
    percentage = round((attended_count / total_sessions * 100), 1) if total_sessions > 0 else 100.0

    stats = {
        "attended_sessions": attended_count,
        "total_sessions": total_sessions,
        "percentage": min(percentage, 100.0),
    }

    return render_template(
        "student_dashboard.html",
        student=student,
        records=records,
        stats=stats,
    )


@app.route("/student/timetable")
def student_timetable():
    student_id = session.get("student_id")
    is_admin = session.get("admin_authenticated") or session.get("role") == "admin"
    if not student_id and not is_admin:
        return redirect(url_for("login"))

    if is_admin and request.args.get("student_id"):
        try:
            student_id = int(request.args.get("student_id"))
        except ValueError:
            abort(400, description="Invalid student_id")

    now = datetime.datetime.now()
    today_str = now.date().isoformat()

    with connect_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            abort(404, description="Student not found.")

        classes = conn.execute(
            """SELECT c.*,
                      EXISTS(
                          SELECT 1 FROM attendance a
                          WHERE a.class_id = c.id AND a.student_id = ?
                      ) AS attended
               FROM classes c
               ORDER BY c.class_date DESC, c.class_time ASC""",
            (student_id,),
        ).fetchall()

    enriched_classes = []
    for c in classes:
        c_dict = dict(c)
        try:
            start_dt = datetime.datetime.combine(
                datetime.date.fromisoformat(c["class_date"]),
                datetime.time.fromisoformat(c["class_time"]),
            )
            end_dt = datetime.datetime.combine(
                datetime.date.fromisoformat(c["class_date"]),
                datetime.time.fromisoformat(c["end_time"]),
            )
        except Exception:
            start_dt = end_dt = now

        if c_dict.get("status") == "completed":
            status = "Completed"
        elif now < start_dt:
            status = "Upcoming"
        elif start_dt <= now <= end_dt:
            status = "Live Now"
        else:
            status = "Past - Ended"

        c_dict["display_status"] = status
        enriched_classes.append(c_dict)

    return render_template(
        "student_timetable.html",
        student=student,
        classes=enriched_classes,
        today=today_str,
    )


@app.route("/student/download_pdf")
def student_download_pdf():
    student_id = session.get("student_id")
    is_admin = session.get("admin_authenticated") or session.get("role") == "admin"
    if not student_id and not is_admin:
        return redirect(url_for("login"))

    if is_admin and request.args.get("student_id"):
        try:
            student_id = int(request.args.get("student_id"))
        except ValueError:
            abort(400, description="Invalid student_id")

    with connect_db() as conn:
        student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            abort(404, description="Student not found.")

        records = conn.execute(
            """SELECT a.id, a.timestamp, COALESCE(c.name, 'General Session') AS class_name,
                      c.class_date, c.faculty
               FROM attendance a
               LEFT JOIN classes c ON c.id = a.class_id
               WHERE a.student_id = ?
               ORDER BY a.timestamp DESC""",
            (student_id,),
        ).fetchall()

        total_classes = conn.execute("SELECT COUNT(*) FROM classes WHERE class_date <= date('now')").fetchone()[0]

    attended = len(records)
    total = max(total_classes, attended)
    pct = round((attended / total * 100), 1) if total > 0 else 100.0

    stats = {
        "attended_sessions": attended,
        "total_sessions": total,
        "percentage": min(pct, 100.0),
    }

    try:
        pdf_buffer = generate_student_attendance_pdf(dict(student), [dict(r) for r in records], stats)
        safe_name = student["name"].replace(" ", "_")
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=f"Attendance_Transcript_{safe_name}.pdf",
            mimetype="application/pdf",
        )
    except Exception as exc:
        app.logger.exception("PDF generation error")
        abort(500, description=f"Could not generate PDF: {exc}")


@app.route("/students")
def students_list():
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT id, name, roll, class, section, reg_no, email, created_at FROM students ORDER BY id DESC"
        ).fetchall()
    return jsonify(
        {
            "students": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "roll": row["roll"],
                    "class": row["class"],
                    "section": row["section"],
                    "reg_no": row["reg_no"],
                    "email": row["email"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        }
    )


@app.route("/students/<int:student_id>", methods=["DELETE"])
def delete_student(student_id):
    with connect_db() as conn:
        conn.execute("DELETE FROM student_faces WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
        conn.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM attendance_daily WHERE student_id = ?", (student_id,))
    folder = os.path.join(DATASET_DIR, str(student_id))
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    model = load_model_if_exists()
    if model is not None:
        remaining = [(label, vector) for label, vector in zip(model["labels"], model["embeddings"]) if label != student_id]
        if remaining:
            labels, embeddings = zip(*remaining)
            temp_path = MODEL_PATH + ".tmp.npz"
            import numpy as np

            np.savez_compressed(temp_path, labels=np.asarray(labels), embeddings=np.asarray(embeddings))
            os.replace(temp_path, MODEL_PATH)
        else:
            os.remove(MODEL_PATH)
    return jsonify({"deleted": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="0.0.0.0", port=port)
