import os, json, secrets
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Production: set DATABASE_URL to a PostgreSQL connection string.
# Local fallback is SQLite so the project is still easy to test.
database_url = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if database_url.startswith("postgresql://") and "+psycopg" not in database_url:
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(database_url, pool_pre_ping=True)

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
        for stmt in (SQLITE_SCHEMA if is_sqlite() else SCHEMA).split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        # Demo admin only if none exists.
        count = conn.execute(text("SELECT COUNT(*) FROM users WHERE role='admin'")).scalar()
        if count == 0:
            conn.execute(text(
                "INSERT INTO users(username,password_hash,role) VALUES(:u,:p,'admin')"
            ), {"u": os.environ.get("ADMIN_USER","admin"),
                "p": generate_password_hash(os.environ.get("ADMIN_PASSWORD","admin123"))})

def q(sql, params=None, fetch=False, one=False):
    with engine.begin() as conn:
        result=conn.execute(text(sql), params or {})
        if fetch:
            rows=result.mappings().all()
            return (rows[0] if rows else None) if one else rows
        return result

def current_user():
    uid=session.get("user_id")
    if not uid: return None
    return q("SELECT * FROM users WHERE id=:id", {"id":uid}, True, True)
@app.context_processor
def inject_current_user():
    return {"current_user": current_user}

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user(): return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u=current_user()
        if not u or u["role"]!="admin": return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper

@app.route("/")
def home():
    u=current_user()
    if not u: return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if u["role"]=="admin" else "student_dashboard"))

@app.route("/login", methods=["GET","POST"])
def login():
    error=None
    if request.method=="POST":
        u=q("SELECT * FROM users WHERE username=:u", {"u":request.form.get("username","").strip()}, True, True)
        if u and check_password_hash(u["password_hash"], request.form.get("password","")):
            session.clear(); session["user_id"]=u["id"]
            return redirect(url_for("home"))
        error="Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    students=q("SELECT * FROM students ORDER BY name", fetch=True)
    subjects=q("SELECT * FROM subjects ORDER BY name", fetch=True)
    today=str(date.today())
    present=q("""SELECT COUNT(*) c FROM attendance a
                 JOIN classes c ON c.id=a.class_id
                 WHERE c.class_date=:d AND a.status='Present'""", {"d":today}, True, True)["c"]
    return render_template("admin.html", students=students, subjects=subjects, present_today=present)
@app.route("/student")
@login_required
def student_dashboard():

    u = current_user()

    if u["role"] != "student" or not u["student_id"]:
        return redirect(url_for("admin_dashboard"))


    # Student information
    s = q(
        "SELECT * FROM students WHERE id=:id",
        {"id": u["student_id"]},
        True,
        True
    )

    if not s:
        session.clear()
        return redirect(url_for("login"))


    # Total classes conducted
    total = q(
        "SELECT COUNT(*) AS c FROM classes",
        fetch=True,
        one=True
    )["c"]


    # Classes attended
    present = q(
        """
        SELECT COUNT(*) AS c
        FROM attendance
        WHERE student_id=:sid
        AND status='Present'
        """,
        {"sid": s["id"]},
        True,
        True
    )["c"]


    # Attendance percentage
    pct = (
        round((present / total) * 100, 1)
        if total
        else 0
    )


    # Complete attendance history
    # Includes both PRESENT and ABSENT classes.
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
            ON sub.id = c.subject_id

        LEFT JOIN attendance a
            ON a.class_id = c.id
            AND a.student_id = :sid

        ORDER BY
            c.class_date DESC,
            c.start_time DESC
        """,
        {"sid": s["id"]},
        fetch=True
    )


    return render_template(
        "student.html",
        student=s,
        present=present,
        total=total,
        pct=pct,
        history=history
    )
@app.route("/register")
@admin_required
def register_page(): return render_template("register.html")

@app.route("/attendance")
@login_required
def attendance_page():
    subjects=q("SELECT * FROM subjects ORDER BY name", fetch=True)
    return render_template("attendance.html", subjects=subjects)

@app.route("/reports")
@admin_required
def reports():
    rows = q("""
        SELECT
            s.id,
            s.roll_no,
            s.name,
            s.department,

            COUNT(c.id) AS total_classes,

            COALESCE(
                SUM(
                    CASE
                        WHEN a.status = 'Present' THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS present

        FROM students s

        LEFT JOIN classes c
            ON 1 = 1

        LEFT JOIN attendance a
            ON a.student_id = s.id
            AND a.class_id = c.id

        GROUP BY
            s.id,
            s.roll_no,
            s.name,
            s.department

        ORDER BY s.name
    """, fetch=True)

    result = []

    for r in rows:

        d = dict(r)

        d["percentage"] = (
            round(
                (d["present"] / d["total_classes"]) * 100,
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

@app.route("/subjects", methods=["POST"])
@admin_required
def create_subject():
    data=request.get_json(force=True)
    if not data.get("code") or not data.get("name"):
        return jsonify(ok=False,error="Subject code and name are required."),400
    try:
        q("INSERT INTO subjects(code,name) VALUES(:c,:n)", {"c":data["code"].strip(),"n":data["name"].strip()})
    except IntegrityError:
        return jsonify(ok=False,error="Subject code already exists."),409
    return jsonify(ok=True)

@app.route("/classes", methods=["POST"])
@admin_required
def create_class():

    data = request.get_json(force=True)

    subject_id = data.get("subject_id")
    class_date = data.get("class_date")
    start_time = data.get("start_time")

    if not subject_id or not class_date or not start_time:
        return jsonify(
            ok=False,
            error="Subject, date and time are required."
        ), 400

    try:

        subject_id = int(subject_id)

        subject = q(
            "SELECT id FROM subjects WHERE id=:id",
            {"id": subject_id},
            fetch=True,
            one=True
        )

        if not subject:
            return jsonify(
                ok=False,
                error="Selected subject does not exist."
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
            error="A class for this subject, date and time already exists."
        ), 409

    except Exception as e:

        return jsonify(
            ok=False,
            error=str(e)
        ), 400

@app.route("/api/students", methods=["POST"])
@admin_required
def create_student():
    data=request.get_json(force=True)
    if not data.get("roll_no") or not data.get("name") or not isinstance(data.get("embeddings"),list) or len(data["embeddings"])<1:
        return jsonify(ok=False,error="Roll number, name and at least one face sample are required."),400
    try:
        sid=q("""INSERT INTO students(roll_no,name,email,department,face_embeddings)
                 VALUES(:r,:n,:e,:d,:f) RETURNING id""",
              {"r":data["roll_no"].strip(),"n":data["name"].strip(),"e":data.get("email","").strip(),
               "d":data.get("department","").strip(),"f":json.dumps(data["embeddings"])}, fetch=True, one=True)["id"]
        # SQLite RETURNING is supported on modern SQLite; fallback if needed.
    except Exception:
        try:
            q("""INSERT INTO students(roll_no,name,email,department,face_embeddings)
                 VALUES(:r,:n,:e,:d,:f)""",
              {"r":data["roll_no"].strip(),"n":data["name"].strip(),"e":data.get("email","").strip(),
               "d":data.get("department","").strip(),"f":json.dumps(data["embeddings"])})
            sid=q("SELECT id FROM students WHERE roll_no=:r",{"r":data["roll_no"]},True,True)["id"]
        except IntegrityError:
            return jsonify(ok=False,error="Roll number already exists."),409
    # Create student login from roll number if not already used.
    password=data.get("password") or "student123"
    try:
        q("""INSERT INTO users(username,password_hash,role,student_id)
             VALUES(:u,:p,'student',:sid)""",
          {"u":data["roll_no"].strip(),"p":generate_password_hash(password),"sid":sid})
    except IntegrityError:
        pass
    return jsonify(ok=True, student_id=sid, login_username=data["roll_no"].strip(), temporary_password=password)

@app.route("/api/recognition-data")
@login_required
def recognition_data():
    rows=q("SELECT id,roll_no,name,face_embeddings FROM students", fetch=True)
    return jsonify([{"id":r["id"],"roll_no":r["roll_no"],"name":r["name"],
                     "embeddings":json.loads(r["face_embeddings"])} for r in rows])

@app.route("/api/mark-attendance", methods=["POST"])
@login_required
def mark_attendance():
    data=request.get_json(force=True)
    student_id=int(data.get("student_id",0)); class_id=int(data.get("class_id",0))
    u=current_user()
    if u["role"]=="student" and u["student_id"]!=student_id:
        return jsonify(ok=False,error="You can only mark your own attendance."),403
    s=q("SELECT * FROM students WHERE id=:id",{"id":student_id},True,True)
    c=q("""SELECT c.*,sub.code,sub.name subject_name FROM classes c JOIN subjects sub ON sub.id=c.subject_id
           WHERE c.id=:id""",{"id":class_id},True,True)
    if not s or not c: return jsonify(ok=False,error="Student or class not found."),404
    try:
        q("""INSERT INTO attendance(student_id,class_id,status) VALUES(:s,:c,'Present')""",
          {"s":student_id,"c":class_id})
        return jsonify(ok=True,already=False,message="Attendance recorded.",student=s["name"],
                       roll_no=s["roll_no"],subject=c["subject_name"],date=str(c["class_date"]))
    except IntegrityError:
        return jsonify(ok=True,already=True,message="Attendance already marked for this class.",
                       student=s["name"],roll_no=s["roll_no"],subject=c["subject_name"])

@app.route("/api/classes")
@login_required
def api_classes():
    rows = q("""
        SELECT
            c.id,
            c.subject_id,
            CAST(c.class_date AS TEXT) AS class_date,
            CAST(c.start_time AS TEXT) AS start_time,
            s.code,
            s.name
        FROM classes c
        JOIN subjects s ON s.id = c.subject_id
        ORDER BY c.class_date DESC, c.start_time DESC
    """, fetch=True)

    return jsonify([
        {
            "id": r["id"],
            "subject_id": r["subject_id"],
            "class_date": r["class_date"],
            "start_time": r["start_time"],
            "code": r["code"],
            "name": r["name"]
        }
        for r in rows
    ])

@app.route("/api/attendance")
@admin_required
def api_attendance():
    rows=q("""SELECT s.roll_no,s.name,sub.code subject_code,sub.name subject,
                     c.class_date,c.start_time,a.status,a.marked_at
              FROM attendance a JOIN students s ON s.id=a.student_id
              JOIN classes c ON c.id=a.class_id JOIN subjects sub ON sub.id=c.subject_id
              ORDER BY c.class_date DESC,c.start_time DESC""", fetch=True)
    return jsonify([dict(r) for r in rows])

init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
