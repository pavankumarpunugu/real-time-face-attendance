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
    UNIQUE(subject_id, class_date, start_time)
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
    UNIQUE(subject_id, class_date, start_time)
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
        {"id": u["student_id"]},
        True,
        True
    )

    if not student:

        session.clear()

        return redirect(
            url_for("login")
        )

    total = q(
        """
        SELECT COUNT(*) AS c
        FROM classes
        """,
        fetch=True,
        one=True
    )["c"]

    present = q(
        """
        SELECT COUNT(*) AS c
        FROM attendance
        WHERE student_id=:sid
        AND status='Present'
        """,
        {
            "sid": student["id"]
        },
        True,
        True
    )["c"]

    pct = (
        round(
            (present / total) * 100,
            1
        )
        if total
        else 0
    )

    history = q(
        """
        SELECT
            sub.code,
            sub.name,
            c.class_date,
            c.start_time,
            COALESCE(
                a.status,
                'Absent'
            ) AS status,
            a.marked_at
        FROM classes c
        JOIN subjects sub
            ON sub.id=c.subject_id
        LEFT JOIN attendance a
            ON a.class_id=c.id
            AND a.student_id=:sid
        ORDER BY
            c.class_date DESC,
            c.start_time DESC
        """,
        {
            "sid": student["id"]
        },
        fetch=True
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

    rows = q(
        """
        SELECT
            s.id,
            s.roll_no,
            s.name,
            s.department,
            COUNT(c.id) AS total_classes,
            COALESCE(
                SUM(
                    CASE
                        WHEN a.status='Present'
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS present
        FROM students s
        LEFT JOIN classes c
            ON 1=1
        LEFT JOIN attendance a
            ON a.student_id=s.id
            AND a.class_id=c.id
        GROUP BY
            s.id,
            s.roll_no,
            s.name,
            s.department
        ORDER BY s.name
        """,
        fetch=True
    )

    result = []

    for r in rows:

        d = dict(r)

        d["percentage"] = (
            round(
                (
                    d["present"]
                    /
                    d["total_classes"]
                ) * 100,
                1
            )
            if d["total_classes"]
            else 0
        )

        result.append(d)

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

        q(
            """
            INSERT INTO subjects(
                code,
                name
            )
            VALUES(
                :c,
                :n
            )
            """,
            {
                "c": code,
                "n": name
            }
        )

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "Subject code already exists."
            )
        ), 409

    return jsonify(
        ok=True,
        message="Subject created successfully."
    )


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
            error=(
                "Subject code already exists."
            )
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
# CREATE CLASS
# =========================================================

@app.route(
    "/classes",
    methods=["POST"]
)
@admin_required
def create_class():

    data = request.get_json(
        force=True
    )

    subject_id = data.get(
        "subject_id"
    )

    class_date = data.get(
        "class_date"
    )

    start_time = data.get(
        "start_time"
    )

    if (
        not subject_id
        or not class_date
        or not start_time
    ):

        return jsonify(
            ok=False,
            error=(
                "Subject, date and time "
                "are required."
            )
        ), 400

    try:

        subject_id = int(
            subject_id
        )

        subject = q(
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
        )

        if not subject:

            return jsonify(
                ok=False,
                error=(
                    "Selected subject "
                    "does not exist."
                )
            ), 400

        q(
            """
            INSERT INTO classes(
                subject_id,
                class_date,
                start_time
            )
            VALUES(
                :sid,
                :d,
                :t
            )
            """,
            {
                "sid": subject_id,
                "d": class_date,
                "t": start_time
            }
        )

        return jsonify(
            ok=True,
            message="Class created successfully."
        )

    except IntegrityError:

        return jsonify(
            ok=False,
            error=(
                "A class for this subject, "
                "date and time already exists."
            )
        ), 409

    except Exception as e:

        return jsonify(
            ok=False,
            error=str(e)
        ), 400


# =========================================================
# DELETE CLASS
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
            s.name,
            c.class_date,
            c.start_time
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
            error="Class session not found."
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
                "attendance records deleted."
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
        data.get("roll_no", "")
    ).strip()

    name = str(
        data.get("name", "")
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
        data.get("name", "")
    ).strip()

    email = str(
        data.get("email", "")
    ).strip()

    department = str(
        data.get("department", "")
    ).strip()

    if not name:

        return jsonify(
            ok=False,
            error="Student name is required."
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
                "id": r["id"],
                "roll_no": r["roll_no"],
                "name": r["name"],
                "embeddings": json.loads(
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
@login_required
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


    # =====================================================
    # CHECK CLASS TIMING
    # =====================================================

    try:

        india = ZoneInfo(
            "Asia/Kolkata"
        )

        now = datetime.now(
            india
        )


        class_date_str = str(
            class_info["class_date"]
        )


        start_time_str = str(
            class_info["start_time"]
        )


        # Convert HH:MM to HH:MM:SS
        if len(
            start_time_str.split(":")
        ) == 2:

            start_time_str += ":00"


        class_start = datetime.strptime(
            f"{class_date_str} {start_time_str}",
            "%Y-%m-%d %H:%M:%S"
        ).replace(
            tzinfo=india
        )


        # Class duration = 1 hour
        class_end = (
            class_start
            + timedelta(hours=1)
        )


        # -------------------------------------------------
        # BEFORE CLASS
        # -------------------------------------------------

        if now < class_start:

            return jsonify(
                ok=False,
                error=(
                    "Class has not "
                    "started yet. "
                    f"Please wait until "
                    f"{class_start.strftime('%I:%M %p')}."
                )
            ), 403


        # -------------------------------------------------
        # AFTER CLASS
        # -------------------------------------------------

        if now >= class_end:

            return jsonify(
                ok=False,
                error=(
                    "This class has "
                    "already ended. "
                    "Attendance is no "
                    "longer available."
                )
            ), 403


    except Exception as e:

        app.logger.exception(
            "Class timing validation failed"
        )

        return jsonify(
            ok=False,
            error=(
                "Unable to verify "
                "class timing."
            )
        ), 500


    # =====================================================
    # RECORD ATTENDANCE
    # =====================================================

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
            subject=class_info["subject_name"],
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
            subject=class_info["subject_name"]
        )

# =========================================================
# CLASSES API
# =========================================================

# =========================================================
# CLASSES API
# =========================================================

@app.route(
    "/api/classes"
)
@login_required
def api_classes():

    try:

        # -------------------------------------------------
        # INDIA DATE
        # -------------------------------------------------

        india = ZoneInfo(
            "Asia/Kolkata"
        )

        today = datetime.now(
            india
        ).date()


        # -------------------------------------------------
        # FIND ALL FIXED CLASS TIMINGS
        #
        # Each unique subject + start time
        # becomes a daily recurring class.
        # -------------------------------------------------

        schedules = q(
            """
            SELECT DISTINCT
                subject_id,
                start_time
            FROM classes
            ORDER BY
                start_time ASC
            """,
            fetch=True
        )


        # -------------------------------------------------
        # CREATE TODAY'S SESSION AUTOMATICALLY
        # -------------------------------------------------

        for schedule in schedules:

            try:

                q(
                    """
                    INSERT INTO classes(
                        subject_id,
                        class_date,
                        start_time
                    )
                    SELECT
                        :subject_id,
                        :class_date,
                        :start_time
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM classes
                        WHERE
                            subject_id=:subject_id
                            AND class_date=:class_date
                            AND start_time=:start_time
                    )
                    """,
                    {
                        "subject_id":
                            schedule["subject_id"],

                        "class_date":
                            today,

                        "start_time":
                            schedule["start_time"]
                    }
                )

            except IntegrityError:

                # Another request may have created
                # the same session at the same time.
                pass


        # -------------------------------------------------
        # RETURN TODAY'S CLASSES
        # -------------------------------------------------

        rows = q(
            """
            SELECT
                c.id,
                c.subject_id,

                CAST(
                    c.class_date AS TEXT
                ) AS class_date,

                CAST(
                    c.start_time AS TEXT
                ) AS start_time,

                s.code,
                s.name

            FROM classes c

            JOIN subjects s
                ON s.id=c.subject_id

            WHERE c.class_date=:today

            ORDER BY
                c.start_time ASC
            """,
            {
                "today": today
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

                    "code":
                        r["code"],

                    "name":
                        r["name"]
                }

                for r in rows
            ]
        )


    except Exception as e:

        app.logger.exception(
            "Classes API error"
        )

        return jsonify(
            ok=False,
            error=str(e)
        ), 500
# =========================================================
# CLASS ATTENDANCE API
# =========================================================

@app.route(
    "/api/class-attendance/<int:class_id>"
)
def class_attendance(class_id):

    try:
        # This API must return JSON, never an HTML login redirect.
        u = current_user()

        if not u:
            return jsonify(
                ok=False,
                error="Admin login session not found."
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
                "id": class_info["id"],
                "class_date": str(class_info["class_date"]),
                "start_time": str(class_info["start_time"]),
                "code": class_info["code"],
                "name": class_info["name"]
            },
            students=[
                {
                    "id": s["id"],
                    "roll_no": s["roll_no"],
                    "name": s["name"],
                    "department": s["department"],
                    "status": s["status"],
                    "marked_at": (
                        str(s["marked_at"])
                        if s["marked_at"] is not None
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
            error=f"Class attendance error: {str(e)}"
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
