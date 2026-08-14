import os
import json
import secrets
import io
import base64

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

import qrcode

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

CREATE TABLE IF NOT EXISTS qr_tokens (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    class_id INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    token VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
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

CREATE TABLE IF NOT EXISTS qr_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
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
@admin_required
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

    if result:

        avg_percentage = round(
            sum(
                r["percentage"]
                for r in result
            )
            / len(result),
            1
        )

    else:

        avg_percentage = 0

    low_attendance_count = sum(
        1
        for r in result
        if r["percentage"] < 75
    )

    return render_template(
        "reports.html",
        rows=result,
        avg_percentage=avg_percentage,
        low_attendance_count=low_attendance_count
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
                :code,
                :name
            )
            """,
            {
                "code": code,
                "name": name
            }
        )

        return jsonify(
            ok=True,
            message="Subject created successfully."
        )

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "A subject with this code "
                "already exists."
            )
        ), 409

    except Exception as e:

        app.logger.exception(
            "Subject creation error"
        )

        return jsonify(
            ok=False,
            error=str(e)
        ), 500


# =========================================================
# SUBJECT EDIT
# =========================================================

@app.route(
    "/subjects/<int:subject_id>",
    methods=["PUT"]
)
@admin_required
def edit_subject(subject_id):

    data = request.get_json(
        force=True
    )

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

        result = q(
            """
            UPDATE subjects
            SET
                code=:code,
                name=:name
            WHERE id=:id
            """,
            {
                "code": code,
                "name": name,
                "id": subject_id
            }
        )

        if result.rowcount == 0:

            return jsonify(
                ok=False,
                error="Subject not found."
            ), 404

    except IntegrityError:

        return jsonify(
            ok=False,
            error="Subject code already exists."
        ), 409

    return jsonify(
        ok=True,
        message="Subject updated successfully."
    )


# =========================================================
# SUBJECT DELETE
# =========================================================

@app.route(
    "/subjects/<int:subject_id>",
    methods=["DELETE"]
)
@admin_required
def delete_subject(subject_id):

    subject = q(
        """
        SELECT *
        FROM subjects
        WHERE id=:id
        """,
        {"id": subject_id},
        True,
        True
    )

    if not subject:

        return jsonify(
            ok=False,
            error="Subject not found."
        ), 404

    try:

        q(
            """
            DELETE FROM attendance
            WHERE class_id IN (
                SELECT id
                FROM classes
                WHERE subject_id=:sid
            )
            """,
            {
                "sid": subject_id
            }
        )

        q(
            """
            DELETE FROM classes
            WHERE subject_id=:sid
            """,
            {
                "sid": subject_id
            }
        )

        q(
            """
            DELETE FROM subjects
            WHERE id=:id
            """,
            {
                "id": subject_id
            }
        )

        return jsonify(
            ok=True,
            message=(
                "Subject, class sessions and "
                "attendance records deleted."
            )
        )

    except Exception as e:

        return jsonify(
            ok=False,
            error=str(e)
        ), 400
# =========================================================
# DAILY TIMETABLE
# =========================================================

@app.route(
    "/classes",
    methods=["POST"]
)
@admin_required
def create_class():

    data = request.get_json(
        force=True
    ) or {}

    try:

        subject_id = int(
            data.get(
                "subject_id",
                0
            )
        )

        duration = int(
            data.get(
                "duration_minutes",
                60
            )
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify(
            ok=False,
            error="Invalid subject or duration."
        ), 400

    start_time = str(
        data.get(
            "start_time",
            ""
        )
    ).strip()[:5]

    if not subject_id or not start_time:

        return jsonify(
            ok=False,
            error=(
                "Subject and start time "
                "are required."
            )
        ), 400

    if duration <= 0 or duration > 480:

        return jsonify(
            ok=False,
            error=(
                "Duration must be between "
                "1 and 480 minutes."
            )
        ), 400

    try:

        datetime.strptime(
            start_time,
            "%H:%M"
        )

    except ValueError:

        return jsonify(
            ok=False,
            error="Start time must be HH:MM."
        ), 400

    if not q(
        """
        SELECT id
        FROM subjects
        WHERE id=:id
        """,
        {
            "id": subject_id
        },
        True,
        True
    ):

        return jsonify(
            ok=False,
            error=(
                "Selected subject "
                "does not exist."
            )
        ), 400

    try:

        q(
            """
            INSERT INTO class_schedules(
                subject_id,
                start_time,
                duration_minutes
            )
            VALUES(
                :sid,
                :t,
                :duration
            )
            """,
            {
                "sid": subject_id,
                "t": start_time,
                "duration": duration
            }
        )

        return jsonify(
            ok=True,
            message=(
                "Daily class schedule "
                "created successfully."
            )
        )

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "A daily schedule already "
                "exists for this subject "
                "and time."
            )
        ), 409


@app.route(
    "/api/class-schedules"
)
@admin_required
def class_schedules():

    rows = q(
        """
        SELECT
            cs.id,
            cs.subject_id,
            CAST(cs.start_time AS TEXT)
                AS start_time,
            cs.duration_minutes,
            s.code,
            s.name
        FROM class_schedules cs
        JOIN subjects s
            ON s.id=cs.subject_id
        ORDER BY cs.start_time
        """,
        fetch=True
    )

    return jsonify(
        [
            {
                "id":
                    r["id"],

                "subject_id":
                    r["subject_id"],

                "start_time":
                    str(r["start_time"]),

                "duration_minutes":
                    int(
                        r["duration_minutes"]
                    ),

                "code":
                    r["code"],

                "name":
                    r["name"]
            }

            for r in rows
        ]
        )
@app.route(
    "/api/class-schedules/<int:schedule_id>",
    methods=["PUT"]
)
@admin_required
def edit_class_schedule(
    schedule_id
):

    data = request.get_json(
        force=True
    ) or {}

    try:

        subject_id = int(
            data.get(
                "subject_id",
                0
            )
        )

        duration = int(
            data.get(
                "duration_minutes",
                60
            )
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify(
            ok=False,
            error=(
                "Invalid subject or "
                "duration."
            )
        ), 400

    start_time = str(
        data.get(
            "start_time",
            ""
        )
    ).strip()[:5]

    if not subject_id or not start_time:

        return jsonify(
            ok=False,
            error=(
                "Subject and start time "
                "are required."
            )
        ), 400

    if duration <= 0 or duration > 480:

        return jsonify(
            ok=False,
            error=(
                "Duration must be between "
                "1 and 480 minutes."
            )
        ), 400

    try:

        datetime.strptime(
            start_time,
            "%H:%M"
        )

    except ValueError:

        return jsonify(
            ok=False,
            error="Start time must be HH:MM."
        ), 400

    if not q(
        """
        SELECT id
        FROM subjects
        WHERE id=:id
        """,
        {
            "id": subject_id
        },
        True,
        True
    ):

        return jsonify(
            ok=False,
            error="Subject not found."
        ), 404

    try:

        result = q(
            """
            UPDATE class_schedules
            SET
                subject_id=:sid,
                start_time=:t,
                duration_minutes=:duration
            WHERE id=:id
            """,
            {
                "sid": subject_id,
                "t": start_time,
                "duration": duration,
                "id": schedule_id
            }
        )

        if result.rowcount == 0:

            return jsonify(
                ok=False,
                error=(
                    "Daily schedule "
                    "not found."
                )
            ), 404

        return jsonify(
            ok=True,
            message=(
                "Daily schedule "
                "updated successfully."
            )
        )

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "Another schedule already "
                "uses this subject and time."
            )
        ), 409


@app.route(
    "/api/class-schedules/<int:schedule_id>",
    methods=["DELETE"]
)
@admin_required
def delete_class_schedule(
    schedule_id
):

    result = q(
        """
        DELETE FROM class_schedules
        WHERE id=:id
        """,
        {
            "id": schedule_id
        }
    )

    if result.rowcount == 0:

        return jsonify(
            ok=False,
            error=(
                "Daily schedule "
                "not found."
            )
        ), 404

    return jsonify(
        ok=True,
        message=(
            "Daily schedule "
            "deleted successfully."
        )
    )


@app.route(
    "/api/today-status"
)
@login_required
def today_status():

    return jsonify(
        get_today_status()
    )


@app.route(
    "/api/holidays"
)
@admin_required
def api_holidays():

    rows = q(
        """
        SELECT
            id,
            CAST(holiday_date AS TEXT)
                AS holiday_date,
            name
        FROM holidays
        ORDER BY holiday_date
        """,
        fetch=True
    )

    return jsonify(
        [
            {
                "id":
                    r["id"],

                "holiday_date":
                    str(r["holiday_date"]),

                "name":
                    r["name"]
            }

            for r in rows
        ]
    )
@app.route(
    "/api/holidays",
    methods=["POST"]
)
@admin_required
def create_holiday():

    data = request.get_json(
        force=True
    ) or {}

    holiday_date = str(
        data.get(
            "holiday_date",
            ""
        )
    ).strip()

    name = str(
        data.get(
            "name",
            ""
        )
    ).strip()

    if not holiday_date or not name:

        return jsonify(
            ok=False,
            error=(
                "Holiday date and name "
                "are required."
            )
        ), 400

    try:

        datetime.strptime(
            holiday_date,
            "%Y-%m-%d"
        )

    except ValueError:

        return jsonify(
            ok=False,
            error=(
                "Holiday date must be "
                "YYYY-MM-DD."
            )
        ), 400

    try:

        q(
            """
            INSERT INTO holidays(
                holiday_date,
                name
            )
            VALUES(
                :d,
                :n
            )
            """,
            {
                "d": holiday_date,
                "n": name
            }
        )

        return jsonify(
            ok=True,
            message="Holiday added successfully."
        )

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "A holiday already exists "
                "for this date."
            )
        ), 409


@app.route(
    "/api/holidays/<int:holiday_id>",
    methods=["DELETE"]
)
@admin_required
def delete_holiday(
    holiday_id
):

    result = q(
        """
        DELETE FROM holidays
        WHERE id=:id
        """,
        {
            "id": holiday_id
        }
    )

    if result.rowcount == 0:

        return jsonify(
            ok=False,
            error="Holiday not found."
        ), 404

    return jsonify(
        ok=True,
        message="Holiday removed successfully."
    )


# =========================================================
# DELETE CLASS SESSION
# =========================================================

@app.route(
    "/classes/<int:class_id>",
    methods=["DELETE"]
)
@admin_required
def delete_class(class_id):

    class_info = q(
        """
        SELECT
            c.id,
            s.code,
            c.class_date
        FROM classes c
        JOIN subjects s
            ON s.id=c.subject_id
        WHERE c.id=:id
        """,
        {
            "id": class_id
        },
        True,
        True
    )

    if not class_info:

        return jsonify(
            ok=False,
            error=(
                "Class session "
                "not found."
            )
        ), 404

    try:

        q(
            """
            DELETE FROM attendance
            WHERE class_id=:cid
            """,
            {
                "cid": class_id
            }
        )

        q(
            """
            DELETE FROM classes
            WHERE id=:id
            """,
            {
                "id": class_id
            }
        )

        return jsonify(
            ok=True,
            message=(
                "Class session and its "
                "attendance records "
                "deleted."
            )
        )

    except Exception as e:

        return jsonify(
            ok=False,
            error=str(e)
        ), 400
# =========================================================
# CREATE STUDENT
# =========================================================

@app.route(
    "/api/students",
    methods=["POST"]
)
@admin_required
def create_student():

    data = request.get_json(
        force=True
    )

    roll_no = str(
        data.get(
            "roll_no",
            ""
        )
    ).strip()

    name = str(
        data.get(
            "name",
            ""
        )
    ).strip()

    embeddings = data.get(
        "embeddings"
    )

    if (
        not roll_no
        or not name
        or not isinstance(
            embeddings,
            list
        )
        or len(embeddings) < 1
    ):

        return jsonify(
            ok=False,
            error=(
                "Roll number, name and "
                "at least one face sample "
                "are required."
            )
        ), 400

    try:

        q(
            """
            INSERT INTO students(
                roll_no,
                name,
                email,
                department,
                face_embeddings
            )
            VALUES(
                :r,
                :n,
                :e,
                :d,
                :f
            )
            """,
            {
                "r": roll_no,
                "n": name,
                "e": str(
                    data.get(
                        "email",
                        ""
                    )
                ).strip(),
                "d": str(
                    data.get(
                        "department",
                        ""
                    )
                ).strip(),
                "f": json.dumps(
                    embeddings
                )
            }
        )

        sid = q(
            """
            SELECT id
            FROM students
            WHERE roll_no=:r
            """,
            {
                "r": roll_no
            },
            True,
            True
        )["id"]

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "Roll number already exists."
            )
        ), 409

    password = (
        data.get("password")
        or "student123"
    )

    try:

        q(
            """
            INSERT INTO users(
                username,
                password_hash,
                role,
                student_id
            )
            VALUES(
                :u,
                :p,
                'student',
                :sid
            )
            """,
            {
                "u": roll_no,
                "p": generate_password_hash(
                    password
                ),
                "sid": sid
            }
        )

    except IntegrityError:

        pass

    return jsonify(
        ok=True,
        student_id=sid,
        login_username=roll_no,
        temporary_password=password
    )


# =========================================================
# EDIT STUDENT
# =========================================================

@app.route(
    "/api/students/<int:student_id>",
    methods=["PUT"]
)
@admin_required
def edit_student(student_id):

    data = request.get_json(
        force=True
    )

    name = str(
        data.get(
            "name",
            ""
        )
    ).strip()

    email = str(
        data.get(
            "email",
            ""
        )
    ).strip()

    department = str(
        data.get(
            "department",
            ""
        )
    ).strip()

    if not name:

        return jsonify(
            ok=False,
            error=(
                "Student name "
                "is required."
            )
        ), 400

    student = q(
        """
        SELECT *
        FROM students
        WHERE id=:id
        """,
        {
            "id": student_id
        },
        True,
        True
    )

    if not student:

        return jsonify(
            ok=False,
            error="Student not found."
        ), 404

    q(
        """
        UPDATE students
        SET
            name=:name,
            email=:email,
            department=:department
        WHERE id=:id
        """,
        {
            "name": name,
            "email": email,
            "department": department,
            "id": student_id
        }
    )

    password = data.get(
        "password",
        ""
    ).strip()

    if password:

        q(
            """
            UPDATE users
            SET password_hash=:p
            WHERE student_id=:sid
            """,
            {
                "p": generate_password_hash(
                    password
                ),
                "sid": student_id
            }
        )

    return jsonify(
        ok=True,
        message="Student updated successfully."
    )
# =========================================================
# DELETE STUDENT
# =========================================================

@app.route(
    "/api/students/<int:student_id>",
    methods=["DELETE"]
)
@admin_required
def delete_student(student_id):

    student = q(
        """
        SELECT *
        FROM students
        WHERE id=:id
        """,
        {
            "id": student_id
        },
        True,
        True
    )

    if not student:

        return jsonify(
            ok=False,
            error="Student not found."
        ), 404

    try:

        q(
            """
            DELETE FROM attendance
            WHERE student_id=:sid
            """,
            {
                "sid": student_id
            }
        )

        q(
            """
            DELETE FROM users
            WHERE student_id=:sid
            """,
            {
                "sid": student_id
            }
        )

        q(
            """
            DELETE FROM students
            WHERE id=:sid
            """,
            {
                "sid": student_id
            }
        )

        return jsonify(
            ok=True,
            message=(
                f"Student {student['name']} "
                "deleted successfully."
            )
        )

    except Exception as e:

        return jsonify(
            ok=False,
            error=str(e)
        ), 400


# =========================================================
# RECOGNITION DATA
# =========================================================

@app.route(
    "/api/recognition-data"
)
@login_required
def recognition_data():

    rows = q(
        """
        SELECT
            id,
            roll_no,
            name,
            face_embeddings
        FROM students
        """,
        fetch=True
    )

    return jsonify(
        [
            {
                "id":
                    r["id"],

                "roll_no":
                    r["roll_no"],

                "name":
                    r["name"],

                "embeddings":
                    json.loads(
                        r["face_embeddings"]
                    )
            }

            for r in rows
        ]
    )


# =========================================================
# MARK ATTENDANCE
# =========================================================

@app.route(
    "/api/mark-attendance",
    methods=["POST"]
)
@admin_required
def mark_attendance():

    data = request.get_json(
        force=True
    )

    student_id = int(
        data.get(
            "student_id",
            0
        )
    )

    class_id = int(
        data.get(
            "class_id",
            0
        )
    )

    u = current_user()

    if (
        u["role"] == "student"
        and u["student_id"]
        != student_id
    ):

        return jsonify(
            ok=False,
            error=(
                "You can only mark "
                "your own attendance."
            )
        ), 403

    student = q(
        """
        SELECT *
        FROM students
        WHERE id=:id
        """,
        {
            "id": student_id
        },
        True,
        True
    )

    class_info = q(
        """
        SELECT
            c.*,
            sub.code,
            sub.name AS subject_name
        FROM classes c
        JOIN subjects sub
            ON sub.id=c.subject_id
        WHERE c.id=:id
        """,
        {
            "id": class_id
        },
        True,
        True
    )

    if not student or not class_info:

        return jsonify(
            ok=False,
            error=(
                "Student or class "
                "not found."
            )
        ), 404

    status = get_today_status()

    if status["is_holiday"]:

        return jsonify(
            ok=False,
            error=(
                "Attendance is not available "
                "today: "
                + status["holiday_name"]
            )
        ), 400

    if (
        str(
            class_info["class_date"]
        )[:10]
        != today_string()
    ):

        return jsonify(
            ok=False,
            error=(
                "Attendance is only "
                "available for today's class."
            )
        ), 400

    try:

        start_dt = parse_class_start(
            class_info
        )

        duration = int(
            class_info[
                "duration_minutes"
            ]
            or 60
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify(
            ok=False,
            error="Invalid class schedule."
        ), 500

    now = india_now().replace(
        tzinfo=None
    )

    end_dt = (
        start_dt
        + timedelta(
            minutes=duration
        )
    )

    if now < start_dt:

        return jsonify(
            ok=False,
            error=(
                "Class has not started yet. "
                f"Please wait until "
                f"{start_dt.strftime('%I:%M %p')}."
            )
        ), 400

    if now >= end_dt:

        return jsonify(
            ok=False,
            error=(
                "This class has already ended. "
                f"Attendance closed at "
                f"{end_dt.strftime('%I:%M %p')}."
            )
        ), 400

    try:

        q(
            """
            INSERT INTO attendance(
                student_id,
                class_id,
                status
            )
            VALUES(
                :s,
                :c,
                'Present'
            )
            """,
            {
                "s": student_id,
                "c": class_id
            }
        )

        return jsonify(
            ok=True,
            already=False,
            message="Attendance recorded.",
            student=student["name"],
            roll_no=student["roll_no"],
            subject=class_info[
                "subject_name"
            ],
            date=str(
                class_info["class_date"]
            )
        )

    except IntegrityError:

        return jsonify(
            ok=True,
            already=True,
            message=(
                "Attendance already "
                "marked for this class."
            ),
            student=student["name"],
            roll_no=student["roll_no"],
            subject=class_info[
                "subject_name"
            ]
    )
# =========================================================
# CLASSES API
# =========================================================

@app.route(
    "/api/classes"
)
@login_required
def api_classes():

    status = get_today_status()

    if status["is_holiday"]:

        return jsonify([])

    ensure_today_sessions()

    rows = q(
        """
        SELECT
            c.id,
            c.subject_id,
            CAST(c.class_date AS TEXT)
                AS class_date,
            CAST(c.start_time AS TEXT)
                AS start_time,
            c.duration_minutes,
            s.code,
            s.name
        FROM classes c
        JOIN subjects s
            ON s.id=c.subject_id
        WHERE c.class_date=:d
        ORDER BY c.start_time
        """,
        {
            "d": today_string()
        },
        fetch=True
    )

    return jsonify(
        [
            {
                "id":
                    r["id"],

                "subject_id":
                    r["subject_id"],

                "class_date":
                    str(
                        r["class_date"]
                    ),

                "start_time":
                    str(
                        r["start_time"]
                    ),

                "duration_minutes":
                    int(
                        r["duration_minutes"]
                        or 60
                    ),

                "code":
                    r["code"],

                "name":
                    r["name"]
            }

            for r in rows
        ]
    )


# =========================================================
# CLASS ATTENDANCE API
# =========================================================

@app.route(
    "/api/class-attendance/<int:class_id>"
)
def class_attendance(class_id):

    try:

        u = current_user()

        if not u:

            return jsonify(
                ok=False,
                error=(
                    "Admin login session "
                    "not found."
                )
            ), 401

        if u["role"] != "admin":

            return jsonify(
                ok=False,
                error="Admin access required."
            ), 403

        class_info = q(
            """
            SELECT
                c.id,
                c.class_date,
                c.start_time,
                c.duration_minutes,
                s.code,
                s.name
            FROM classes c
            JOIN subjects s
                ON s.id=c.subject_id
            WHERE c.id=:id
            """,
            {
                "id": class_id
            },
            True,
            True
        )

        if not class_info:

            return jsonify(
                ok=False,
                error="Class not found."
            ), 404

        students = q(
            """
            SELECT
                s.id,
                s.roll_no,
                s.name,
                s.department,
                COALESCE(
                    a.status,
                    'Absent'
                ) AS status,
                a.marked_at
            FROM students s
            LEFT JOIN attendance a
                ON a.student_id=s.id
                AND a.class_id=:cid
            ORDER BY s.name
            """,
            {
                "cid": class_id
            },
            fetch=True
        )

        return jsonify(
            ok=True,

            class_info={
                "id":
                    class_info["id"],

                "class_date":
                    str(
                        class_info[
                            "class_date"
                        ]
                    ),

                "start_time":
                    str(
                        class_info[
                            "start_time"
                        ]
                    ),

                "duration_minutes":
                    int(
                        class_info[
                            "duration_minutes"
                        ]
                        or 60
                    ),

                "code":
                    class_info["code"],

                "name":
                    class_info["name"]
            },

            students=[
                {
                    "id":
                        s["id"],

                    "roll_no":
                        s["roll_no"],

                    "name":
                        s["name"],

                    "department":
                        s["department"],

                    "status":
                        s["status"],

                    "marked_at":
                        (
                            str(
                                s["marked_at"]
                            )
                            if s[
                                "marked_at"
                            ] is not None
                            else None
                        )
                }

                for s in students
            ]
        )

    except Exception as e:

        app.logger.exception(
            "Class attendance API error"
        )

        return jsonify(
            ok=False,
            error=(
                "Class attendance error: "
                f"{str(e)}"
            )
        ), 500
# =========================================================
# ALL ATTENDANCE API
# =========================================================

@app.route(
    "/api/attendance"
)
@admin_required
def api_attendance():

    rows = q(
        """
        SELECT
            s.roll_no,
            s.name,
            sub.code AS subject_code,
            sub.name AS subject,
            c.class_date,
            c.start_time,
            a.status,
            a.marked_at
        FROM attendance a
        JOIN students s
            ON s.id=a.student_id
        JOIN classes c
            ON c.id=a.class_id
        JOIN subjects sub
            ON sub.id=c.subject_id
        ORDER BY
            c.class_date DESC,
            c.start_time DESC
        """,
        fetch=True
    )

    return jsonify(
        [
            dict(r)
            for r in rows
        ]
    )


# =========================================================
# ADMIN: MANUAL ATTENDANCE OVERRIDE
# =========================================================

@app.route(
    "/api/attendance/override",
    methods=["POST"]
)
@admin_required
def override_attendance():

    data = request.get_json(force=True) or {}

    student_id = int(
        data.get("student_id", 0)
    )

    class_id = int(
        data.get("class_id", 0)
    )

    status = (
        data.get("status") or ""
    ).strip()

    if status not in (
        "Present",
        "Absent"
    ):

        return jsonify(
            ok=False,
            error=(
                "Status must be "
                "Present or Absent."
            )
        ), 400

    student = q(
        "SELECT id FROM students WHERE id=:id",
        {"id": student_id},
        True,
        True
    )

    class_info = q(
        "SELECT id FROM classes WHERE id=:id",
        {"id": class_id},
        True,
        True
    )

    if not student or not class_info:

        return jsonify(
            ok=False,
            error="Student or class not found."
        ), 404

    if status == "Absent":

        q(
            """
            DELETE FROM attendance
            WHERE student_id=:s
                AND class_id=:c
            """,
            {
                "s": student_id,
                "c": class_id
            }
        )

        return jsonify(
            ok=True,
            status="Absent"
        )

    try:

        q(
            """
            INSERT INTO attendance(
                student_id,
                class_id,
                status
            )
            VALUES(
                :s,
                :c,
                'Present'
            )
            """,
            {
                "s": student_id,
                "c": class_id
            }
        )

    except IntegrityError:

        q(
            """
            UPDATE attendance
            SET status='Present'
            WHERE student_id=:s
                AND class_id=:c
            """,
            {
                "s": student_id,
                "c": class_id
            }
        )

    return jsonify(
        ok=True,
        status="Present"
    )


# =========================================================
# QR CODE ATTENDANCE
# =========================================================

QR_EXPIRY_SECONDS = 300


@app.route(
    "/api/qr/generate",
    methods=["POST"]
)
@admin_required
def generate_qr():

    data = request.get_json(force=True) or {}

    subject_id = int(
        data.get("subject_id", 0)
    )

    status = get_today_status()

    if status["is_holiday"]:

        return jsonify(
            ok=False,
            error=(
                "Attendance is not available "
                "today: "
                + (status["holiday_name"] or "Holiday")
            )
        ), 400

    ensure_today_sessions()

    class_info = q(
        """
        SELECT
            c.id,
            sub.code,
            sub.name AS subject_name
        FROM classes c
        JOIN subjects sub
            ON sub.id = c.subject_id
        WHERE c.subject_id = :sid
            AND c.class_date = :d
        """,
        {
            "sid": subject_id,
            "d": today_string()
        },
        True,
        True
    )

    if not class_info:

        return jsonify(
            ok=False,
            error=(
                "No class is scheduled for "
                "this subject today."
            )
        ), 400

    token = secrets.token_urlsafe(24)

    now = india_now().replace(tzinfo=None)

    expires_at = (
        now
        + timedelta(seconds=QR_EXPIRY_SECONDS)
    )

    q(
        """
        INSERT INTO qr_tokens(
            class_id,
            subject_id,
            token,
            expires_at
        )
        VALUES(
            :c,
            :s,
            :t,
            :e
        )
        """,
        {
            "c": class_info["id"],
            "s": subject_id,
            "t": token,
            "e": expires_at.isoformat()
        }
    )

    qr_img = qrcode.make(token)

    buffer = io.BytesIO()

    qr_img.save(buffer, format="PNG")

    qr_data_uri = (
        "data:image/png;base64,"
        + base64.b64encode(
            buffer.getvalue()
        ).decode("ascii")
    )

    return jsonify(
        ok=True,
        token=token,
        qr_image=qr_data_uri,
        subject_code=class_info["code"],
        subject_name=class_info["subject_name"],
        expires_in=QR_EXPIRY_SECONDS
    )


@app.route(
    "/api/qr/mark-attendance",
    methods=["POST"]
)
@login_required
def qr_mark_attendance():

    u = current_user()

    if (
        u["role"] != "student"
        or not u["student_id"]
    ):

        return jsonify(
            ok=False,
            error=(
                "Only students can mark "
                "attendance via QR code."
            )
        ), 403

    data = request.get_json(force=True) or {}

    token = (
        data.get("token") or ""
    ).strip()

    if not token:

        return jsonify(
            ok=False,
            error="Missing QR code."
        ), 400

    qr_row = q(
        """
        SELECT *
        FROM qr_tokens
        WHERE token = :t
        """,
        {"t": token},
        True,
        True
    )

    if not qr_row:

        return jsonify(
            ok=False,
            error=(
                "Invalid or unrecognized "
                "QR code."
            )
        ), 404

    expires_at = qr_row["expires_at"]

    if isinstance(expires_at, str):

        expires_at = datetime.fromisoformat(
            expires_at
        )

    now = india_now().replace(tzinfo=None)

    if now > expires_at:

        return jsonify(
            ok=False,
            error=(
                "This QR code has expired. "
                "Ask your teacher to generate "
                "a new one."
            )
        ), 400

    student_id = u["student_id"]
    class_id = qr_row["class_id"]

    student = q(
        """
        SELECT *
        FROM students
        WHERE id=:id
        """,
        {"id": student_id},
        True,
        True
    )

    class_info = q(
        """
        SELECT
            c.*,
            sub.code,
            sub.name AS subject_name
        FROM classes c
        JOIN subjects sub
            ON sub.id=c.subject_id
        WHERE c.id=:id
        """,
        {"id": class_id},
        True,
        True
    )

    if not student or not class_info:

        return jsonify(
            ok=False,
            error="Student or class not found."
        ), 404

    try:

        q(
            """
            INSERT INTO attendance(
                student_id,
                class_id,
                status
            )
            VALUES(
                :s,
                :c,
                'Present'
            )
            """,
            {
                "s": student_id,
                "c": class_id
            }
        )

        return jsonify(
            ok=True,
            already=False,
            message="Attendance recorded.",
            student=student["name"],
            roll_no=student["roll_no"],
            subject=class_info["subject_name"]
        )

    except IntegrityError:

        return jsonify(
            ok=True,
            already=True,
            message=(
                "Attendance already "
                "marked for this class."
            ),
            student=student["name"],
            roll_no=student["roll_no"],
            subject=class_info["subject_name"]
        )


# =========================================================
# START
# =========================================================

init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        ),
        debug=False
    )
