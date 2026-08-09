# Real-Time Face Recognition Attendance System — Final MVP

## Features

- Admin login
- Student login
- Student profile/dashboard
- Attendance percentage
- Attendance history
- Admin student registration
- Browser webcam face enrollment
- Multiple face samples per student
- Browser-side face recognition using face-api.js
- Subject management
- Class/session creation
- Subject-wise attendance
- Duplicate attendance prevention per class
- Admin reports
- PostgreSQL-ready deployment
- SQLite fallback for simple testing

## Cloud deployment

The project is designed for a cloud platform such as Render. `render.yaml` defines the web service and a PostgreSQL database.

If deploying manually:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`

Set these environment variables:
- `SECRET_KEY`
- `ADMIN_USER`
- `ADMIN_PASSWORD`
- `DATABASE_URL`

## Default demo admin

If no admin exists, the application creates one using:
- username: `admin`
- password: `admin123`

Change these credentials before real use.

## Face recognition

The browser loads face-api.js and its model files over HTTPS from the public model repository. During enrollment, 3 face samples are recommended. During attendance, the live face is converted to an embedding and compared against the student's registered samples.

A basic similarity threshold is used for this MVP. A production biometric attendance system should add liveness/anti-spoofing, stronger authentication, consent/privacy controls, encrypted biometric storage, audit logs, rate limiting, and appropriate data-retention policies.

## Student flow

1. Admin creates a student.
2. Student's browser camera captures 3 face samples.
3. Face embeddings are stored.
4. A class/session is created for a subject.
5. Student logs in.
6. Student chooses the class and clicks Take Attendance.
7. Webcam opens.
8. Live face is compared to the registered samples.
9. If matched, attendance is recorded once for that class.
10. Student dashboard shows percentage and history.

## Important

The application should be served over HTTPS because browsers generally require a secure context for webcam access. Cloud hosts provide HTTPS on their public domain.
