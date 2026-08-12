import os
import json
import secrets

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    secrets.token_hex(32)
)


# =========================================================
# DATABASE
# =========================================================

database_url = os.environ.get(
    "DATABASE_URL",
    "sqlite:///attendance.db"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql://",
        1
    )

if (
    database_url.startswith("postgresql://")
    and "+psycopg" not in database_url
):
    database_url = database_url.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1
    )

engine = create_engine(
    database_url,
    pool_pre_ping=True
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'student',
    student_id INTEGER
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    roll_no VARCHAR(100) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(200),
    department VARCHAR(100),
    face_embeddings TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    subject_id INTEGER NOT NULL,
    class_date DATE NOT NULL,
    start_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    UNIQUE(subject_id, class_date, start_time)
);

CREATE TABLE IF NOT EXISTS class_schedules (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    subject_id INTEGER NOT NULL,
    start_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    UNIQUE(subject_id, start_time)
);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    holiday_date DATE UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    student_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    marked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'Present',
    UNIQUE(student_id, class_id)
);
"""


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    student_id INTEGER
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    department TEXT,
    face_embeddings TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL,
    class_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    UNIQUE(subject_id, class_date, start_time)
);

CREATE TABLE IF NOT EXISTS class_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    UNIQUE(subject_id, start_time)
);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holiday_date TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    marked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'Present',
    UNIQUE(student_id, class_id)
);
"""


def is_sqlite():
    return database_url.startswith("sqlite")


def init_db():

    with engine.begin() as conn:

        schema = (
            SQLITE_SCHEMA
            if is_sqlite()
            else SCHEMA
        )

        for stmt in schema.split(";"):

            if stmt.strip():

                conn.execute(
                    text(stmt)
                )

        if is_sqlite():
            columns = conn.execute(
                text("PRAGMA table_info(classes)")
            ).fetchall()

            names = {
                row[1]
                for row in columns
            }

            if "duration_minutes" not in names:

                conn.execute(
                    text(
                        "ALTER TABLE classes ADD COLUMN "
                        "duration_minutes INTEGER NOT NULL DEFAULT 60"
                    )
                )

        else:

            conn.execute(
                text(
                    "ALTER TABLE classes ADD COLUMN IF NOT EXISTS "
                    "duration_minutes INTEGER NOT NULL DEFAULT 60"
                )
            )

        count = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM users
                WHERE role='admin'
                """
            )
        ).scalar()

        if count == 0:

            conn.execute(
                text(
                    """
                    INSERT INTO users(
                        username,
                        password_hash,
                        role
                    )
                    VALUES(
                        :u,
                        :p,
                        'admin'
                    )
                    """
                ),
                {
                    "u": os.environ.get(
                        "ADMIN_USER",
                        "admin"
                    ),
                    "p": generate_password_hash(
                        os.environ.get(
                            "ADMIN_PASSWORD",
                            "admin123"
                        )
                    )
                }
            )


IST = ZoneInfo("Asia/Kolkata")


def india_now():
    return datetime.now(IST)


def today_india():
    return india_now().date()


def today_string():
    return today_india().isoformat()


def get_today_status():

    today = today_india()

    if today.weekday() == 6:

        return {
            "is_holiday": True,
            "holiday_type": "Sunday",
            "holiday_name": "Sunday"
        }

    holiday = q(
        """
        SELECT id, holiday_date, name
        FROM holidays
        WHERE holiday_date=:d
        """,
        {"d": today.isoformat()},
        True,
        True
    )

    if holiday:

        return {
            "is_holiday": True,
            "holiday_type": "Holiday",
            "holiday_name": holiday["name"]
        }

    return {
        "is_holiday": False,
        "holiday_type": None,
        "holiday_name": None
    }


def ensure_today_sessions():

    if get_today_status()["is_holiday"]:
        return

    schedules = q(
        """
        SELECT subject_id, start_time, duration_minutes
        FROM class_schedules
        ORDER BY start_time
        """,
        fetch=True
    )

    for schedule in schedules:

        try:

            q(
                """
                INSERT INTO classes(
                    subject_id,
                    class_date,
                    start_time,
                    duration_minutes
                )
                VALUES(
                    :sid,
                    :d,
                    :t,
                    :duration
                )
                """,
                {
                    "sid": schedule["subject_id"],
                    "d": today_string(),
                    "t": str(
                        schedule["start_time"]
                    )[:8],
                    "duration": int(
                        schedule["duration_minutes"]
                    )
                }
            )

        except IntegrityError:
            pass


def parse_class_start(class_info):

    return datetime.strptime(
        f'{str(class_info["class_date"])[:10]} '
        f'{str(class_info["start_time"])[:5]}',
        "%Y-%m-%d %H:%M"
    )


def q(
    sql,
    params=None,
    fetch=False,
    one=False
):

    with engine.begin() as conn:

        result = conn.execute(
            text(sql),
            params or {}
        )

        if fetch:

            rows = result.mappings().all()

            if one:

                return (
                    rows[0]
                    if rows
                    else None
                )

            return rows

        return result


# =========================================================
# AUTH
# =========================================================

def current_user():

    uid = session.get(
        "user_id"
    )

    if not uid:
        return None

    return q(
        """
        SELECT *
        FROM users
        WHERE id=:id
        """,
        {"id": uid},
        True,
        True
    )


@app.context_processor
def inject_current_user():

    return {
        "current_user": current_user
    }


def login_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        if not current_user():

            return redirect(
                url_for("login")
            )

        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        u = current_user()

        if (
            not u
            or u["role"] != "admin"
        ):

            return redirect(
                url_for("login")
            )

        return fn(*args, **kwargs)

    return wrapper


# =========================================================
# HOME / LOGIN
# =========================================================

@app.route("/")
def home():

    u = current_user()

    if not u:

        return redirect(
            url_for("login")
        )

    if u["role"] == "admin":

        return redirect(
            url_for("admin_dashboard")
        )

    return redirect(
        url_for("student_dashboard")
    )


@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    error = None

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        u = q(
            """
            SELECT *
            FROM users
            WHERE username=:u
            """,
            {"u": username},
            True,
            True
        )

        if (
            u
            and check_password_hash(
                u["password_hash"],
                password
            )
        ):

            session.clear()

            session["user_id"] = u["id"]

            return redirect(
                url_for("home")
            )

        error = (
            "Invalid username or password."
        )

    return render_template(
        "login.html",
        error=error
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin")
@admin_required
def admin_dashboard():

    students = q(
        """
        SELECT *
        FROM students
        ORDER BY name
        """,
        fetch=True
    )

    subjects = q(
        """
        SELECT *
        FROM subjects
        ORDER BY name
        """,
        fetch=True
    )

    today = str(date.today())

    present_today = q(
        """
        SELECT COUNT(*) AS c
        FROM attendance a
        JOIN classes c
            ON c.id=a.class_id
        WHERE c.class_date=:d
        AND a.status='Present'
        """,
        {"d": today},
        True,
        True
    )["c"]

    return render_template(
        "admin.html",
        students=students,
        subjects=subjects,
        present_today=present_today
    )


# =========================================================
# STUDENT DASHBOARD
# =========================================================

@app.route("/student")
@login_required
def student_dashboard():

    u = current_user()

    if (
        u["role"] != "student"
        or not u["student_id"]
    ):

        return redirect(
            url_for("admin_dashboard")
        )

    student = q(
        """
        SELECT *
        FROM students
        WHERE id=:id
        """,
        {
            "id": u["student_id"]
        },
        True,
        True
    )

    if not student:

        session.clear()

        return redirect(
            url_for("login")
        )

    rows = q(
        """
        SELECT
            sub.code,
            sub.name,
            c.class_date,
            c.start_time,
            c.duration_minutes,
            a.status,
            a.marked_at
        FROM classes c

        JOIN subjects sub
            ON sub.id = c.subject_id

        LEFT JOIN attendance a
            ON a.class_id = c.id
            AND a.student_id = :sid

        ORDER BY
            c.class_date DESC,
            c.start_time DESC
        """,
        {
            "sid": student["id"]
        },
        fetch=True
    )

    now = india_now().replace(
        tzinfo=None
    )

    history = []

    total = 0
    present = 0

    for r in rows:

        h = dict(r)

        try:

            start = datetime.strptime(
                f'{str(h["class_date"])[:10]} '
                f'{str(h["start_time"])[:5]}',
                "%Y-%m-%d %H:%M"
            )

            duration = int(
                h["duration_minutes"]
                or 60
            )

            end = (
                start
                + timedelta(
                    minutes=duration
                )
            )

        except (
            TypeError,
            ValueError
        ):

            h["status"] = (
                h["status"]
                or "Unknown"
            )

            history.append(h)

            continue

        if h["status"] == "Present":

            display_status = "Present"

            if now >= end:

                total += 1

                present += 1

        elif now < start:

            display_status = "Upcoming"

        elif now < end:

            display_status = "In Progress"

        else:

            display_status = "Absent"

            total += 1

        h["status"] = display_status

        history.append(h)

    pct = (
        round(
            (present / total) * 100,
            1
        )
        if total
        else 0
    )

    return render_template(
        "student.html",
        student=student,
        present=present,
        total=total,
        pct=pct,
        history=history
    )


# =========================================================
# PAGES
# =========================================================

@app.route("/register")
@admin_required
def register_page():

    return render_template(
        "register.html"
    )


@app.route("/attendance")
@login_required
def attendance_page():

    subjects = q(
        """
        SELECT *
        FROM subjects
        ORDER BY name
        """,
        fetch=True
    )

    return render_template(
        "attendance.html",
        subjects=subjects
    )


# =========================================================
# REPORTS
# =========================================================

@app.route("/reports")
@admin_required
def reports():

    now = india_now().replace(
        tzinfo=None
    )

    rows = q(
        """
        SELECT
            s.id,
            s.roll_no,
            s.name,
            s.department,

            c.id AS class_id,
            c.class_date,
            c.start_time,
            c.duration_minutes,

            a.status

        FROM students s

        LEFT JOIN classes c
            ON 1=1

        LEFT JOIN attendance a
            ON a.student_id = s.id
            AND a.class_id = c.id

        ORDER BY
            s.name,
            c.class_date,
            c.start_time
        """,
        fetch=True
    )

    students = {}

    for r in rows:

        d = dict(r)

        sid = d["id"]

        if sid not in students:

            students[sid] = {
                "id": sid,
                "roll_no": d["roll_no"],
                "name": d["name"],
                "department": d["department"],
                "total_classes": 0,
                "present": 0
            }

        if not d["class_id"]:
            continue

        try:

            start = datetime.strptime(
                f'{str(d["class_date"])[:10]} '
                f'{str(d["start_time"])[:5]}',
                "%Y-%m-%d %H:%M"
            )

            duration = int(
                d["duration_minutes"]
                or 60
            )

            end = (
                start
                + timedelta(
                    minutes=duration
                )
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        if now < start:
            continue

        if now < end:
            continue

        students[sid]["total_classes"] += 1

        if d["status"] == "Present":

            students[sid]["present"] += 1

    result = []

    for student in students.values():

        total_classes = int(
            student["total_classes"]
            or 0
        )

        present = int(
            student["present"]
            or 0
        )

        percentage = (

            round(
                (
                    present /
                    total_classes
                ) * 100,
                1
            )

            if total_classes

            else 0

        )

        result.append({

            "id":
                student["id"],

            "roll_no":
                student["roll_no"],

            "name":
                student["name"],

            "department":
                student["department"],

            "total_classes":
                total_classes,

            "present":
                present,

            "percentage":
                percentage

        })

    result.sort(
        key=lambda x:
            x["name"].lower()
    )

    return render_template(
        "reports.html",
        rows=result
    )


# =========================================================
# SUBJECT CREATE
# =========================================================

@app.route(
    "/subjects",
    methods=["POST"]
)
@admin_required
def create_subject():

    data = request.get_json(
        force=True
    ) or {}

    code = str(
        data.get("code", "")
    ).strip()

    name = str(
        data.get("name", "")
    ).strip()

    if not code or not name:

        return jsonify(
            ok=False,
            error=(
                "Subject code and name "
                "are required."
            )
        ), 400

    try:

        q(
            """
            INSERT INTO subjects(
                code,
                name
            )
            VALUES(
   
