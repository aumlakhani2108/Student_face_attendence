---
title: BioBytes Student Face Attendance
emoji: 📸
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Digital Facial Recognition Attendance System (BioBytes)

## Description

A local, browser-based student attendance application built with Python, Flask, SQLite, OpenCV YuNet, and OpenCV SFace. An administrator enrolls students using a webcam, trains a face-matching model, and records attendance through live camera recognition.

This is an educational/demo application. Face recognition can produce false matches or miss a student; evaluate it carefully before using it for consequential decisions. Obtain consent before collecting biometric data.

## Features

- Dual Role Authentication with Two-Factor Email OTP Verification:
  - **Segmented Landing Page**: Clean tabbed interface separating **Student Login** and **Admin Login**. Student self-registration is closed for campus security; only authorized administrators can enroll students with biometric face capture.
  - **Two-Factor Email OTP**: Every sign-in (both student and administrator) is validated with a time-sensitive 6-digit one-time password dispatched to the user's verified email.
  - **Admin-Creates-Admin Authorization**: Existing administrators can authorize and invite new administrators via `/manage_admins`. The new administrator account is verified via a 6-digit email OTP before activation.
  - **Student Portal**: Students log in with Roll Number / Email, password, and email OTP to access their personal attendance history, timetable, and downloadable PDF transcript.
- Student Enrollment with name, roll, registration number, class, section, email, and password.
- Laptop webcam selector for enrollment and live attendance; camera selection helps avoid phone cameras linked through Windows.
- Captured enrollment images stored as image BLOBs in the local SQLite database.
- YuNet face detection and SFace embeddings for face matching.
- Real-time Unknown Face Detection: Displays visual thumbnails and alerts for unregistered faces in live sessions.
- Background model training with visible progress and terminal success/error status.
- Live attendance with at most one attendance record per student per UTC calendar day, or per class session.
- Admin-created classes with a name, scheduled date, start and end times, faculty, room/hall, and live attendance count.
- Server-enforced attendance windows with an explicit **"End Lecture & Send Emails"** trigger.
- Automated Post-Lecture Email Notifications: Automatically dispatches attendance confirmation emails to all present students upon lecture completion.
- Lecture Timetable for Students: Interactive timetable displaying scheduled, live, and completed classes with individual attendance status.
- Official PDF & CSV Export: Class attendance sheets and personalized student transcripts generated with ReportLab.
- Dashboard analytics with 7/30/90-day or all-time filters, scheduled-class/cohort/section filters, class and daily charts, student attendance trends, session totals, and latest-attendance insights.
- Deleting a student removes their profile, attendance, photos, and trained embedding.

## How the Project Works

1. **Enroll:** An administrator saves a student's details (including email and password). The browser requests webcam permission and captures up to 50 JPEG frames from the selected camera. The frames are validated and saved to the `student_faces` SQLite table.
2. **Train:** The training process reads the stored photos, detects and aligns faces with YuNet, computes SFace embeddings, averages the embeddings per student, and writes the searchable model to `face_embeddings.npz`.
3. **Recognize:** The attendance page captures webcam frames and sends them to Flask. The server creates an SFace embedding and compares it with enrolled student embeddings using cosine similarity. If an unrecognized face appears, it is flagged and displayed in real time.
4. **Record attendance:** A match above the configured similarity threshold is recorded only while the selected class is in its scheduled window and only once per student per class session.
5. **Complete Lecture & Notify:** The teacher concludes the lecture via "End Lecture & Send Emails", which triggers automated confirmation emails to all students recorded as present.
6. **Review & Student Portal:** Administrators export reports as CSV or PDF. Students log in (with roll/email, password, and email OTP) to check their lecture timetable, attendance progress, and download their official transcript.
7. **Admin Management:** Administrators can invite other administrators (`/manage_admins`), with mandatory email OTP verification before activation.

### Workflow

```text
Login Screen (PresentSync / Attendly)
    ├── Tab 1: Student Login (Roll No / Email + Password)
    │     -> 2-Factor Email OTP Verification
    │     -> View Lecture Timetable (Upcoming, Live, Attended, Absent)
    │     -> View Personal Attendance Log & Overall Percentage
    │     -> Export Official Attendance Transcript (PDF)
    └── Tab 2: Admin Login (Admin Username / Email + Password)
          -> 2-Factor Email OTP Verification
          -> Manage Administrators & Invite New Admins (Verified via Email OTP)
          -> Add Student & Capture Face Samples (50 frames)
          -> Train Face Recognizer (YuNet + SFace)
          -> Create Lecture Session (Date, Times, Faculty, Room)
          -> Start Live Attendance (Detects Known vs Unknown Faces)
          -> End Lecture -> Automatically Dispatches Attendance Emails
          -> View Analytics & Export PDF / CSV
```

## Project Structure

```text
.
|-- app.py                       Flask routes, dual-role auth, SQLite operations
|-- model.py                     YuNet/SFace embedding extraction and training
|-- mailer.py                    Automated post-lecture email notification engine
|-- pdf_export.py                Official PDF report and student transcript generator
|-- download_models.py           Downloads the OpenCV Zoo model weights
|-- requirements.txt             Python dependencies
|-- .env.example                 Example configuration with SMTP settings
|-- attendance.db                Local student, attendance, and enrollment-photo data
|-- models/                      Downloaded detector and recognizer weights
|-- templates/                   Flask HTML pages (Admin & Student portals)
|-- static/css/                  Stylesheets
|-- static/js/                   Dashboard and webcam interaction
`-- tests/                       Automated tests
```

Generated data, secrets, downloaded weights, and virtual environments are local-only and excluded from Git. Student images are sensitive biometric data; protect database backups and set an appropriate retention policy.

## Tests

### Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## How to Run

### First-time setup (Windows PowerShell)

From this project directory, create the virtual environment, install dependencies, and download face-model weights:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe download_models.py
```

Create a private `.env` file in the project directory (or copy `.env.example`):

```powershell
$secret = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
@"
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-private-password-at-least-12-characters
SECRET_KEY=$secret
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=1
MAIL_SENDER=your-email@gmail.com
"@ | Set-Content -Encoding ascii .env
```

### Start the app

Whenever you want to run the app, from this project directory execute:

```powershell
.\.venv\Scripts\python.exe -m flask --app app run --host 0.0.0.0 --port 5000      
```

Open <http://127.0.0.1:5000> in the browser:
- Sign in as **Admin** using the `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`.
- Sign in as a **Student** using their **Roll Number or Email** and **Password** (set during enrollment).

## Deploy to Render

1. **Connect Repository**: Push this repository to GitHub/GitLab and connect your Render account.
2. **Create Blueprint**: In the Render Dashboard, select **New +** -> **Blueprint**, and select this repository. Render will automatically parse [`render.yaml`](file:///D:/Information%20Technology/5th/Minor%20Projects/Student_face_attendence/render.yaml).
3. **Fill Environment Variables**: Fill in the required variables in the dashboard (`ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`, and optional SMTP credentials). `SECRET_KEY` is generated automatically.
4. **Deploy & Launch**: Click **Apply**. Once deployed, open your `https://<service-name>.onrender.com` URL:
   - **Webcam Access**: Modern browsers require HTTPS (or localhost) to permit webcam access for enrollment and live attendance capture.
   - **Persistent Storage**: The configured 1 GB Persistent Disk mounted at `/data` (`attendance-data`) retains `attendance.db`, face photos, and trained embeddings across redeploys and restarts. Note that without a persistent disk, local data is wiped on redeploy.
   - **Single Instance**: SQLite and local filesystem storage require running exactly **1 instance** (`--workers 1 --threads 4`). Do not scale horizontally to multiple instances.


