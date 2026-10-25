import io
import os
import sqlite3
import threading

import cv2
import numpy as np
import pytest

os.environ.setdefault("SECRET_KEY", "test-session-key-that-is-long-enough")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-long-enough")

import app as attendance_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(attendance_app, "DB_PATH", str(tmp_path / "attendance.db"))
    monkeypatch.setattr(attendance_app, "DATASET_DIR", str(tmp_path / "dataset"))
    monkeypatch.setattr(attendance_app, "TRAIN_STATUS_FILE", str(tmp_path / "train_status.json"))
    attendance_app.app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        ADMIN_USERNAME="test-admin",
        ADMIN_PASSWORD="test-password-long-enough",
    )
    attendance_app.training_lock = threading.Lock()
    attendance_app.init_db()
    with attendance_app.app.test_client() as test_client:
        yield test_client


def login(client):
    res = client.post(
        "/login",
        data={"username": "test-admin", "password": "test-password-long-enough", "login_type": "admin"},
    )
    with client.session_transaction() as sess:
        pending = sess.get("pending_otp")
    if pending and "dev_otp" in pending:
        return client.post("/verify-otp", data={"otp": pending["dev_otp"]})
    return res


def student_login(client, username, password):
    res = client.post(
        "/login",
        data={"username": username, "password": password, "login_type": "student"},
    )
    with client.session_transaction() as sess:
        pending = sess.get("pending_otp")
    if pending and "dev_otp" in pending:
        return client.post("/verify-otp", data={"otp": pending["dev_otp"]})
    return res


def active_class_times():
    now = attendance_app.datetime.datetime.now()
    start = now - attendance_app.datetime.timedelta(minutes=5)
    end = now + attendance_app.datetime.timedelta(minutes=55)
    if start.date() < now.date():
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if end.date() > now.date():
        end = now.replace(hour=23, minute=59, second=0, microsecond=0)
    return start.strftime("%H:%M"), end.strftime("%H:%M")


def test_admin_login_protects_pages_and_apis(client):
    assert client.get("/").status_code == 302
    assert client.get("/students").status_code == 401
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert b"Attendly" in login_page.data
    assert b'id="username"' in login_page.data
    assert b'id="password"' in login_page.data
    assert login(client).status_code == 302
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b'id="analysisPeriod"' in dashboard.data
    assert b'id="analysisClass"' in dashboard.data
    assert b'id="analysisCohort"' in dashboard.data
    assert b'id="analysisSection"' in dashboard.data
    assert b'id="studentAnalysisRows"' in dashboard.data
    assert client.get("/students").status_code == 200


def test_student_enrollment_and_face_upload(client):
    login(client)
    response = client.post(
        "/add_student",
        data={"name": "Ada Lovelace", "roll": "42", "class": "CS", "sec": "A", "reg_no": "R-42"},
    )
    assert response.status_code == 200
    student_id = response.json["student_id"]

    ok, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
    assert ok
    image_bytes = encoded.tobytes()
    upload = client.post(
        "/upload_face",
        data={
            "student_id": str(student_id),
            "images[]": [
                (io.BytesIO(image_bytes), "first.jpg"),
                (io.BytesIO(image_bytes), "second.jpg"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    assert upload.json == {"saved": 2, "storage": "database"}
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT image FROM student_faces WHERE student_id = ? ORDER BY id", (student_id,)
        ).fetchall()
    assert [row[0] for row in rows] == [image_bytes, image_bytes]
    assert not os.path.exists(os.path.join(attendance_app.DATASET_DIR, str(student_id)))
    students = client.get("/students").json["students"]
    assert students[0]["name"] == "Ada Lovelace"
    assert students[0]["roll"] == "42"


def test_face_upload_rejects_unknown_student(client):
    login(client)
    ok, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
    assert ok
    response = client.post(
        "/upload_face",
        data={"student_id": "999", "images[]": (io.BytesIO(encoded.tobytes()), "face.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 404
    assert response.json["error"] == "Student not found."


def test_face_upload_rejects_invalid_image_bytes(client):
    login(client)
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        student_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",)).lastrowid
    response = client.post(
        "/upload_face",
        data={"student_id": str(student_id), "images[]": (io.BytesIO(b"not an image"), "face.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM student_faces").fetchone()[0] == 0


def test_legacy_face_files_migrate_to_database(client, tmp_path):
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        student_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",)).lastrowid
    folder = tmp_path / "dataset" / str(student_id)
    folder.mkdir(parents=True)
    ok, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
    assert ok
    image_path = folder / "legacy.jpg"
    image_path.write_bytes(encoded.tobytes())

    attendance_app.migrate_dataset_images()

    assert not image_path.exists()
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        image = conn.execute("SELECT image FROM student_faces WHERE student_id = ?", (student_id,)).fetchone()[0]
    assert image == encoded.tobytes()


def test_deleting_student_removes_database_photos(client):
    login(client)
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        student_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",)).lastrowid
        conn.execute(
            "INSERT INTO student_faces (student_id, image, created_at) VALUES (?, ?, ?)",
            (student_id, b"stored image", "2026-01-01T00:00:00+00:00"),
        )

    response = client.delete(f"/students/{student_id}")

    assert response.status_code == 200
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM student_faces").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0


def test_recognition_records_attendance_once_per_utc_day(client, monkeypatch):
    login(client)
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        cursor = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",))
        student_id = cursor.lastrowid

    monkeypatch.setattr(attendance_app, "extract_embedding_for_image", lambda stream: [0.1, 0.2])
    monkeypatch.setattr(attendance_app, "load_model_if_exists", lambda: object())
    monkeypatch.setattr(attendance_app, "predict_with_model", lambda model, embedding: (student_id, 0.9))

    def recognize():
        return client.post(
            "/recognize_face",
            data={"image": (io.BytesIO(b"test image"), "face.jpg")},
            content_type="multipart/form-data",
        )

    first = recognize()
    second = recognize()
    assert first.status_code == 200
    assert first.json["recognized"] is True
    assert first.json["already_marked"] is False
    assert second.status_code == 200
    assert second.json["already_marked"] is True
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1


def test_classes_track_class_specific_attendance_and_show_daily_total(client, monkeypatch):
    login(client)
    class_date = attendance_app.datetime.datetime.now().date().isoformat()
    class_time, end_time = active_class_times()
    first_class = client.post(
        "/classes",
        data={
            "name": "Computer Science",
            "class_date": class_date,
            "class_time": class_time,
            "end_time": end_time,
            "faculty": "Ada Lovelace",
        },
    )
    second_class = client.post(
        "/classes",
        data={
            "name": "Mathematics",
            "class_date": class_date,
            "class_time": class_time,
            "end_time": end_time,
            "faculty": "Grace Hopper",
        },
    )
    assert first_class.status_code == 302
    assert second_class.status_code == 302
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        first_class_id, second_class_id = [
            row[0] for row in conn.execute("SELECT id FROM classes ORDER BY id")
        ]
        student_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",)).lastrowid

    monkeypatch.setattr(attendance_app, "extract_embedding_for_image", lambda stream: [0.1, 0.2])
    monkeypatch.setattr(attendance_app, "load_model_if_exists", lambda: object())
    monkeypatch.setattr(attendance_app, "predict_with_model", lambda model, embedding: (student_id, 0.9))

    def recognize(class_id):
        return client.post(
            "/recognize_face",
            data={
                "class_id": str(class_id),
                "image": (io.BytesIO(b"test image"), "face.jpg"),
            },
            content_type="multipart/form-data",
        )

    first_attendance = recognize(first_class_id)
    duplicate_attendance = recognize(first_class_id)
    second_class_attendance = recognize(second_class_id)
    assert first_attendance.json["already_marked"] is False
    assert first_attendance.json["present_count"] == 1
    assert duplicate_attendance.json["already_marked"] is True
    assert duplicate_attendance.json["present_count"] == 1
    assert second_class_attendance.json["already_marked"] is False
    assert second_class_attendance.json["present_count"] == 1
    assert first_attendance.json["class_id"] == first_class_id
    assert second_class_attendance.json["class_id"] == second_class_id

    classes_page = client.get("/classes")
    assert classes_page.status_code == 200
    assert b"Computer Science" in classes_page.data
    assert b"Mathematics" in classes_page.data
    assert class_date.encode() in classes_page.data
    assert classes_page.data.count(b"<td>1</td>") == 2
    mark_page = client.get("/mark_attendance")
    assert mark_page.status_code == 200
    assert f'value="{first_class_id}" data-attended-count="1"'.encode() in mark_page.data
    assert b"Attendance open" in classes_page.data
    records_page = client.get("/attendance_record")
    assert b"Computer Science" in records_page.data
    assert b"Mathematics" in records_page.data
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(DISTINCT class_id) FROM attendance WHERE student_id = ?",
            (student_id,),
        ).fetchone()[0] == 2


def test_recognition_rejects_classes_outside_their_attendance_window(client, monkeypatch):
    login(client)
    today = attendance_app.datetime.datetime.now().date()
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        past_class_id = conn.execute(
            """INSERT INTO classes (name, class_date, class_time, end_time, faculty, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Past class", (today - attendance_app.datetime.timedelta(days=1)).isoformat(),
             "09:00", "10:00", "Ada", "2026-01-01T00:00:00+00:00"),
        ).lastrowid
        future_class_id = conn.execute(
            """INSERT INTO classes (name, class_date, class_time, end_time, faculty, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Future class", (today + attendance_app.datetime.timedelta(days=1)).isoformat(),
             "09:00", "10:00", "Ada", "2026-01-01T00:00:00+00:00"),
        ).lastrowid

    classes_page = client.get("/classes")
    assert b"Past - read-only" in classes_page.data
    mark_page = client.get("/mark_attendance")
    assert f'value="{past_class_id}" data-attended-count="0" disabled'.encode() in mark_page.data
    assert f'value="{future_class_id}" data-attended-count="0" disabled'.encode() in mark_page.data
    assert b'id="startMarkBtn" class="btn btn-primary" disabled' in mark_page.data

    monkeypatch.setattr(
        attendance_app,
        "extract_embedding_for_image",
        lambda stream: pytest.fail("Recognition must stop before face processing."),
    )
    for class_id, expected_error in (
        (past_class_id, "window for this class has closed"),
        (future_class_id, "has not opened yet"),
    ):
        response = client.post(
            "/recognize_face",
            data={
                "class_id": str(class_id),
                "image": (io.BytesIO(b"test image"), "face.jpg"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 403
        assert expected_error in response.json["error"]


def test_attendance_records_can_be_filtered_and_downloaded_by_class(client):
    login(client)
    class_date = attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc).date().isoformat()
    client.post(
        "/classes",
        data={
            "name": "Computer Science",
            "class_date": class_date,
            "class_time": "09:30",
            "end_time": "10:30",
            "faculty": "Ada Lovelace",
        },
    )
    client.post(
        "/classes",
        data={
            "name": "Mathematics",
            "class_date": class_date,
            "class_time": "11:00",
            "end_time": "12:00",
            "faculty": "Grace Hopper",
        },
    )
    today = attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc).isoformat()
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        class_ids = [row[0] for row in conn.execute("SELECT id FROM classes ORDER BY id")]
        conn.executemany(
            "INSERT INTO attendance (student_id, name, timestamp, class_id) VALUES (?, ?, ?, ?)",
            [
                (1, "Ada Student", today, class_ids[0]),
                (2, "Grace Student", today, class_ids[1]),
            ],
        )

    records = client.get(f"/attendance_record?class_id={class_ids[0]}")
    assert records.status_code == 200
    assert b"Ada Student" in records.data
    assert b"Grace Student" not in records.data
    assert b"Download Class CSV" in records.data

    download = client.get(f"/download_csv?class_id={class_ids[0]}")
    assert download.status_code == 200
    assert download.headers["Content-Disposition"] == f"attachment; filename=attendance_class_{class_ids[0]}.csv"
    csv_content = download.get_data(as_text=True)
    assert "Ada Student" in csv_content
    assert "Computer Science" in csv_content
    assert "Grace Student" not in csv_content

    all_download = client.get("/download_csv")
    assert "Ada Student" in all_download.get_data(as_text=True)
    assert "Grace Student" in all_download.get_data(as_text=True)


def test_attendance_analysis_returns_student_and_class_totals(client):
    login(client)
    class_date = attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc).date().isoformat()
    client.post(
        "/classes",
        data={
            "name": "Computer Science",
            "class_date": class_date,
            "class_time": "09:30",
            "end_time": "10:30",
            "faculty": "Ada Lovelace",
        },
    )
    client.post(
        "/classes",
        data={
            "name": "Mathematics",
            "class_date": class_date,
            "class_time": "11:00",
            "end_time": "12:00",
            "faculty": "Grace Hopper",
        },
    )
    timestamp = attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc).isoformat()
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        class_ids = [row[0] for row in conn.execute("SELECT id FROM classes ORDER BY id")]
        ada_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",)).lastrowid
        grace_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Grace",)).lastrowid
        conn.executemany(
            "INSERT INTO attendance (student_id, name, timestamp, class_id) VALUES (?, ?, ?, ?)",
            [
                (ada_id, "Ada", timestamp, class_ids[0]),
                (ada_id, "Ada", timestamp, class_ids[1]),
                (grace_id, "Grace", timestamp, class_ids[0]),
            ],
        )

    response = client.get("/attendance_analysis")

    assert response.status_code == 200
    assert response.json["students"][0]["id"] == ada_id
    assert response.json["students"][0]["attendance_count"] == 2
    assert response.json["students"][0]["sessions_attended"] == 2
    assert response.json["students"][0]["last_attended"] == timestamp[:10]
    assert response.json["students"][1]["id"] == grace_id
    assert response.json["students"][1]["attendance_count"] == 1
    assert [item["id"] for item in response.json["classes"]] == class_ids
    assert [item["attendance_count"] for item in response.json["classes"]] == [2, 1]
    assert response.json["summary"] == {
        "student_count": 2,
        "attendance_count": 3,
        "session_count": 2,
        "period": "30",
    }


def test_dashboard_analytics_filters_period_class_cohort_and_section(client):
    login(client)
    today = attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc).date()
    client.post(
        "/classes",
        data={
            "name": "Computer Science",
            "class_date": today.isoformat(),
            "class_time": "09:30",
            "end_time": "10:30",
            "faculty": "Ada Lovelace",
        },
    )
    timestamp = attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc).isoformat()
    previous_week_timestamp = (
        attendance_app.datetime.datetime.now(attendance_app.datetime.timezone.utc)
        - attendance_app.datetime.timedelta(days=10)
    ).isoformat()
    old_timestamp = (today - attendance_app.datetime.timedelta(days=40)).isoformat()
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        class_id = conn.execute("SELECT id FROM classes").fetchone()[0]
        ada_id = conn.execute(
            "INSERT INTO students (name, roll, class, section) VALUES (?, ?, ?, ?)",
            ("Ada", "1", "CS", "A"),
        ).lastrowid
        grace_id = conn.execute(
            "INSERT INTO students (name, roll, class, section) VALUES (?, ?, ?, ?)",
            ("Grace", "2", "CS", "B"),
        ).lastrowid
        conn.executemany(
            "INSERT INTO attendance (student_id, name, timestamp, class_id) VALUES (?, ?, ?, ?)",
            [
                (ada_id, "Ada", timestamp, class_id),
                (ada_id, "Ada", previous_week_timestamp, class_id),
                (grace_id, "Grace", timestamp, class_id),
                (ada_id, "Ada", old_timestamp, class_id),
            ],
        )

    query = f"/attendance_analysis?period=30&class_id={class_id}&cohort=CS&section=A"
    response = client.get(query)
    assert response.status_code == 200
    assert [student["id"] for student in response.json["students"]] == [ada_id]
    assert response.json["students"][0]["attendance_count"] == 2
    assert response.json["students"][0]["sessions_attended"] == 1
    assert response.json["students"][0]["last_7_days"] == 1
    assert response.json["students"][0]["previous_7_days"] == 1
    assert response.json["summary"] == {
        "student_count": 1,
        "attendance_count": 2,
        "session_count": 1,
        "period": "30",
    }
    assert len(response.json["classes"]) == 1
    assert response.json["classes"][0]["attendance_count"] == 1

    recent_only = client.get(f"/attendance_analysis?period=7&class_id={class_id}&cohort=CS&section=A")
    assert recent_only.status_code == 200
    assert recent_only.json["students"][0]["attendance_count"] == 1
    assert recent_only.json["students"][0]["previous_7_days"] == 1

    stats = client.get(f"/attendance_stats?period=30&class_id={class_id}&cohort=CS&section=A")
    assert stats.status_code == 200
    assert sum(stats.json["counts"]) == 2
    assert client.get("/attendance_analysis?period=invalid").status_code == 400


def test_class_creation_requires_valid_date(client):
    login(client)
    response = client.post(
        "/classes",
        data={
            "name": "Computer Science",
            "class_date": "not-a-date",
            "class_time": "09:30",
            "end_time": "10:30",
            "faculty": "Ada",
        },
    )
    assert response.status_code == 200
    assert b"valid date and start/end times" in response.data
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 0


def test_class_end_time_must_be_after_start_time(client):
    login(client)
    today = attendance_app.datetime.datetime.now().date().isoformat()
    response = client.post(
        "/classes",
        data={
            "name": "Computer Science",
            "class_date": today,
            "class_time": "10:00",
            "end_time": "10:00",
            "faculty": "Ada",
        },
    )
    assert response.status_code == 200
    assert b"end after start" in response.data
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 0


def test_init_db_adds_end_time_for_existing_classes(client, tmp_path, monkeypatch):
    legacy_db = tmp_path / "legacy-classes.db"
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            """CREATE TABLE classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                class_time TEXT NOT NULL,
                faculty TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO classes (name, class_time, faculty, created_at)
               VALUES ('Legacy class', '09:30', 'Ada', '2026-01-01T00:00:00+00:00')"""
        )

    monkeypatch.setattr(attendance_app, "DB_PATH", str(legacy_db))
    attendance_app.init_db()

    with sqlite3.connect(legacy_db) as conn:
        assert conn.execute(
            "SELECT class_date, end_time FROM classes WHERE name = 'Legacy class'"
        ).fetchone() == ("2026-01-01", "10:30:00")


def test_init_db_migrates_legacy_daily_attendance_guard(client, tmp_path, monkeypatch):
    legacy_db = tmp_path / "legacy-attendance.db"
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            """CREATE TABLE attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER,
                name TEXT,
                timestamp TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE attendance_daily (
                student_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                PRIMARY KEY (student_id, attendance_date)
            )"""
        )
        conn.execute(
            "INSERT INTO attendance (student_id, name, timestamp) VALUES (1, 'Ada', '2026-10-24T08:00:00+00:00')"
        )
        conn.execute("INSERT INTO attendance_daily (student_id, attendance_date) VALUES (1, '2026-10-24')")

    monkeypatch.setattr(attendance_app, "DB_PATH", str(legacy_db))
    attendance_app.init_db()

    with sqlite3.connect(legacy_db) as conn:
        assert conn.execute("SELECT class_id FROM attendance_daily").fetchone()[0] == 0
        conn.execute(
            "INSERT INTO attendance_daily (student_id, attendance_date, class_id) VALUES (1, '2026-10-24', 3)"
        )
        assert conn.execute("SELECT COUNT(*) FROM attendance_daily").fetchone()[0] == 2
        assert conn.execute("SELECT class_id FROM attendance").fetchone()[0] is None


def test_training_reads_face_images_from_database(client, monkeypatch, tmp_path):
    import model as face_model

    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        student_id = conn.execute("INSERT INTO students (name) VALUES (?)", ("Ada",)).lastrowid
        conn.execute(
            "INSERT INTO student_faces (student_id, image, created_at) VALUES (?, ?, ?)",
            (student_id, b"encoded image", "2026-01-01T00:00:00+00:00"),
        )
    monkeypatch.setattr(face_model, "MODEL_PATH", str(tmp_path / "trained.npz"))
    monkeypatch.setattr(face_model, "create_face_models", lambda: (object(), object()))
    monkeypatch.setattr(
        face_model, "decode_image_bytes", lambda image_bytes: np.zeros((32, 32, 3), dtype=np.uint8)
    )
    monkeypatch.setattr(face_model, "embedding_from_image", lambda image, detector, recognizer: np.array([1.0, 0.0]))

    face_model.train_model_background(attendance_app.DB_PATH)

    with np.load(tmp_path / "trained.npz", allow_pickle=False) as trained:
        assert trained["labels"].tolist() == [student_id]
        assert trained["embeddings"].shape == (1, 2)


def test_training_finishes_with_terminal_status(client, monkeypatch):
    def successful_training(dataset_dir, callback):
        callback(100, "Training complete.")

    monkeypatch.setattr(attendance_app, "train_model_background", successful_training)
    attendance_app.training_lock.acquire()
    attendance_app.write_train_status(
        {"running": True, "progress": 0, "status": "running", "message": "Starting training."}
    )

    attendance_app.run_training()

    status = attendance_app.read_train_status()
    assert status["running"] is False
    assert status["status"] == "completed"


def test_training_failure_is_reported_and_lock_released(client, monkeypatch):
    def failed_training(dataset_dir, callback):
        raise ValueError("No usable faces")

    monkeypatch.setattr(attendance_app, "train_model_background", failed_training)
    attendance_app.training_lock.acquire()
    attendance_app.write_train_status(
        {"running": True, "progress": 0, "status": "running", "message": "Starting training."}
    )

    attendance_app.run_training()

    status = attendance_app.read_train_status()
    assert status["running"] is False
    assert status["status"] == "error"
    assert "No usable faces" in status["message"]
    assert attendance_app.training_lock.acquire(blocking=False)
    attendance_app.training_lock.release()


def test_student_registration_with_email_and_password(client):
    login(client)
    res = client.post(
        "/add_student",
        data={
            "name": "Jane Doe",
            "roll": "IT101",
            "class": "IT",
            "sec": "A",
            "reg_no": "REG-101",
            "email": "jane@example.com",
            "password": "securepassword123",
        },
    )
    assert res.status_code == 200
    student_id = res.json["student_id"]
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    assert row["name"] == "Jane Doe"
    assert row["email"] == "jane@example.com"
    assert attendance_app.check_password_hash(row["password_hash"], "securepassword123")


def test_student_login_and_access_control(client):
    login(client)
    res = client.post(
        "/add_student",
        data={
            "name": "John Smith",
            "roll": "IT102",
            "class": "IT",
            "sec": "B",
            "reg_no": "REG-102",
            "email": "john@example.com",
            "password": "johnpassword",
        },
    )
    student_id = res.json["student_id"]
    client.post("/logout")

    # Attempt student login with wrong password
    bad_login = client.post("/login", data={"username": "IT102", "password": "wrongpassword", "login_type": "student"})
    assert bad_login.status_code == 200
    assert b"Invalid roll number or password" in bad_login.data

    # Successful student login with roll number and OTP verification
    good_login = student_login(client, "IT102", "johnpassword")
    assert good_login.status_code == 302
    assert good_login.headers["Location"].endswith("/student/dashboard")

    # Access student dashboard
    dash = client.get("/student/dashboard")
    assert dash.status_code == 200
    assert b"John Smith" in dash.data
    assert b"IT102" in dash.data

    # Student cannot access admin routes
    admin_page = client.get("/classes")
    assert admin_page.status_code == 302
    assert "/login" in admin_page.headers["Location"]


def test_student_timetable(client):
    login(client)
    student_res = client.post(
        "/add_student",
        data={"name": "Alice Bob", "roll": "IT103", "email": "alice@example.com", "password": "pass"},
    )
    student_id = student_res.json["student_id"]

    now = attendance_app.datetime.datetime.now()
    client.post(
        "/classes",
        data={
            "name": "Cloud Computing",
            "class_date": now.date().isoformat(),
            "class_time": "14:00",
            "end_time": "15:00",
            "faculty": "Dr. Turing",
            "room": "Room 303",
        },
    )
    client.post("/logout")

    student_login(client, "IT103", "pass")
    tt = client.get("/student/timetable")
    assert tt.status_code == 200
    assert b"Cloud Computing" in tt.data
    assert b"Dr. Turing" in tt.data
    assert b"Room 303" in tt.data


def test_class_completion_and_email_dispatch(client, monkeypatch):
    login(client)
    res = client.post(
        "/add_student",
        data={"name": "Eve Student", "roll": "IT104", "email": "eve@example.com", "password": "pass"},
    )
    student_id = res.json["student_id"]

    now = attendance_app.datetime.datetime.now()
    client.post(
        "/classes",
        data={
            "name": "Data Structures",
            "class_date": now.date().isoformat(),
            "class_time": "10:00",
            "end_time": "11:00",
            "faculty": "Prof. Knuth",
            "room": "Hall B",
        },
    )
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        class_id = conn.execute("SELECT id FROM classes ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO attendance (student_id, name, timestamp, class_id) VALUES (?, ?, ?, ?)",
            (student_id, "Eve Student", now.isoformat(), class_id),
        )

    sent_emails = []
    def mock_send(to_email, subj, text, html=None):
        sent_emails.append({"to": to_email, "subject": subj})
        return True, "Sent"

    monkeypatch.setattr("mailer.send_email", mock_send)

    from mailer import dispatch_post_lecture_emails
    result = dispatch_post_lecture_emails(class_id, attendance_app.connect_db)
    assert result["sent"] == 1
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "eve@example.com"
    assert "Data Structures" in sent_emails[0]["subject"]

    # Verify class complete route updates status
    comp_res = client.post(f"/classes/{class_id}/complete")
    assert comp_res.status_code == 302
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        status = conn.execute("SELECT status FROM classes WHERE id = ?", (class_id,)).fetchone()[0]
    assert status == "completed"


def test_pdf_export_routes(client):
    login(client)
    now = attendance_app.datetime.datetime.now()
    client.post(
        "/classes",
        data={
            "name": "Machine Learning",
            "class_date": now.date().isoformat(),
            "class_time": "10:00",
            "end_time": "11:00",
            "faculty": "Dr. Ng",
        },
    )
    admin_pdf = client.get("/download_pdf")
    assert admin_pdf.status_code == 200
    assert admin_pdf.mimetype == "application/pdf"
    assert admin_pdf.data.startswith(b"%PDF-")

    res = client.post(
        "/add_student",
        data={"name": "PDF Student", "roll": "IT105", "email": "pdf@example.com", "password": "pass"},
    )
    student_id = res.json["student_id"]
    client.post("/logout")

    student_login(client, "IT105", "pass")
    student_pdf = client.get("/student/download_pdf")
    assert student_pdf.status_code == 200
    assert student_pdf.mimetype == "application/pdf"
    assert student_pdf.data.startswith(b"%PDF-")


def test_unknown_face_flag_in_recognition(client, monkeypatch):
    login(client)
    class_time, end_time = active_class_times()
    class_date = attendance_app.datetime.datetime.now().date().isoformat()
    client.post(
        "/classes",
        data={
            "name": "Networks",
            "class_date": class_date,
            "class_time": class_time,
            "end_time": end_time,
            "faculty": "Prof. Cerf",
        },
    )
    with sqlite3.connect(attendance_app.DB_PATH) as conn:
        class_id = conn.execute("SELECT id FROM classes ORDER BY id DESC LIMIT 1").fetchone()[0]

    monkeypatch.setattr(attendance_app, "extract_embedding_for_image", lambda stream: [0.1, 0.2])
    monkeypatch.setattr(attendance_app, "load_model_if_exists", lambda: object())
    # Low similarity: unknown face
    monkeypatch.setattr(attendance_app, "predict_with_model", lambda model, embedding: (999, 0.15))

    res = client.post(
        "/recognize_face",
        data={
            "class_id": str(class_id),
            "image": (io.BytesIO(b"dummy image"), "test.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.json["recognized"] is False
    assert res.json["unknown_face"] is True


def test_admin_login_with_otp_flow(client):
    # Bad credentials stay on login page
    bad = client.post("/login", data={"username": "test-admin", "password": "wrongpassword", "login_type": "admin"})
    assert bad.status_code == 200
    assert b"Invalid administrator username or password" in bad.data

    # Valid credentials trigger OTP and redirect to /verify-otp
    initiate = client.post("/login", data={"username": "test-admin", "password": "test-password-long-enough", "login_type": "admin"})
    assert initiate.status_code == 302
    assert "/verify-otp" in initiate.headers["Location"]

    # Verify-otp page renders
    page = client.get("/verify-otp")
    assert page.status_code == 200
    assert b"Two-Factor Verification" in page.data
    assert b"Resend Code" in page.data

    # Submit wrong OTP code
    bad_code = client.post("/verify-otp", data={"otp": "000000"})
    assert bad_code.status_code == 200
    assert b"Incorrect verification code" in bad_code.data

    # Submit valid OTP code
    with client.session_transaction() as sess:
        dev_otp = sess["pending_otp"]["dev_otp"]

    verified = client.post("/verify-otp", data={"otp": dev_otp})
    assert verified.status_code == 302
    assert verified.headers["Location"].endswith("/")

    # Admin dashboard is now accessible
    dash = client.get("/")
    assert dash.status_code == 200


def test_student_login_with_otp_flow(client):
    login(client)
    res = client.post(
        "/add_student",
        data={"name": "Sita Raman", "roll": "IT201", "email": "sita@example.com", "password": "sitapassword123"},
    )
    assert res.status_code == 200
    client.post("/logout")

    # Step 1: Submit credentials
    step1 = client.post("/login", data={"username": "IT201", "password": "sitapassword123", "login_type": "student"})
    assert step1.status_code == 302
    assert "/verify-otp" in step1.headers["Location"]

    # Step 2: Retrieve OTP and verify
    with client.session_transaction() as sess:
        otp = sess["pending_otp"]["dev_otp"]

    step2 = client.post("/verify-otp", data={"otp": otp})
    assert step2.status_code == 302
    assert step2.headers["Location"].endswith("/student/dashboard")

    # Access student dashboard
    sdash = client.get("/student/dashboard")
    assert sdash.status_code == 200
    assert b"Sita Raman" in sdash.data


def test_admin_creates_another_admin_with_email_otp(client):
    # Log in as primary admin
    login(client)

    # 1. View /manage_admins
    manage_page = client.get("/manage_admins")
    assert manage_page.status_code == 200
    assert b"Administrator Management" in manage_page.data
    assert b"test-admin" in manage_page.data

    # 2. Initiate creation of a new admin
    initiate = client.post(
        "/manage_admins",
        data={
            "action": "initiate",
            "username": "prof_sharma",
            "email": "sharma@college.edu",
            "password": "sharmapassword123",
            "confirm_password": "sharmapassword123",
        },
    )
    assert initiate.status_code == 302
    assert "/manage_admins?verify=1" in initiate.headers["Location"]

    # Verify mode is visible
    vpage = client.get("/manage_admins?verify=1")
    assert vpage.status_code == 200
    assert b"Verify New Administrator" in vpage.data
    assert b"sharma@college.edu" in vpage.data

    # 3. Submit OTP verification code
    with client.session_transaction() as sess:
        new_admin_otp = sess["pending_new_admin"]["dev_otp"]

    verify_res = client.post("/manage_admins", data={"action": "verify", "otp": new_admin_otp})
    assert verify_res.status_code == 302
    assert "/manage_admins?created=1" in verify_res.headers["Location"]

    # Confirm new admin is listed
    updated_page = client.get("/manage_admins?created=1")
    assert updated_page.status_code == 200
    assert b"prof_sharma" in updated_page.data
    assert b"sharma@college.edu" in updated_page.data

    # Log out existing admin
    client.post("/logout")

    # 4. New admin logs in with credentials and OTP
    step1 = client.post(
        "/login",
        data={"username": "prof_sharma", "password": "sharmapassword123", "login_type": "admin"},
    )
    assert step1.status_code == 302
    assert "/verify-otp" in step1.headers["Location"]

    with client.session_transaction() as sess:
        sharma_otp = sess["pending_otp"]["dev_otp"]

    step2 = client.post("/verify-otp", data={"otp": sharma_otp})
    assert step2.status_code == 302

    # New admin has full admin access to dashboard
    new_admin_dash = client.get("/")
    assert new_admin_dash.status_code == 200


def test_unauthorized_users_cannot_access_manage_admins(client):
    # Unauthenticated visitor redirected to login
    unauth = client.get("/manage_admins")
    assert unauth.status_code == 302
    assert "/login" in unauth.headers["Location"]

    # Student user cannot access manage_admins
    login(client)
    client.post(
        "/add_student",
        data={"name": "Bob Student", "roll": "IT202", "email": "bob@example.com", "password": "bobpass"},
    )
    client.post("/logout")

    student_login(client, "IT202", "bobpass")
    student_try = client.get("/manage_admins")
    assert student_try.status_code == 302
    assert "/login" in student_try.headers["Location"]

