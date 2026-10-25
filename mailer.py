import datetime
import logging
import os
import secrets
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("attendance_mailer")


def generate_otp(length=6):
    """Generates a secure numeric OTP."""
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def send_otp_email(to_email, otp_code, purpose="Sign In"):
    subject = f"Your PresentSync Verification Code: {otp_code}"
    body_text = f"""Hello,

Your verification code for {purpose} is:

    {otp_code}

This code is valid for 10 minutes. If you did not request this code, please ignore this email.

Regards,
PresentSync Attendance System
"""
    body_html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f5f5f7; margin: 0; padding: 24px; color: #1d1d1f; }}
    .card {{ background: #ffffff; max-width: 480px; margin: 0 auto; border-radius: 14px; overflow: hidden; border: 1px solid #e5e5ea; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
    .header {{ background: #0071e3; color: #ffffff; padding: 20px 24px; text-align: center; }}
    .header h2 {{ margin: 0; font-size: 20px; }}
    .content {{ padding: 28px 24px; text-align: center; }}
    .otp-box {{ display: inline-block; background: #f2f2f7; letter-spacing: 8px; font-size: 32px; font-weight: 700; color: #0071e3; padding: 14px 28px; border-radius: 10px; margin: 20px 0; border: 1px solid #d2d2d7; }}
    .note {{ color: #86868b; font-size: 13px; line-height: 1.5; margin-top: 16px; }}
    .footer {{ padding: 14px 24px; background: #fafafc; font-size: 11px; color: #86868b; text-align: center; border-top: 1px solid #f2f2f7; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2>PresentSync Security Verification</h2>
    </div>
    <div class="content">
      <p style="margin: 0; font-size: 15px;">Use the code below to complete your <strong>{purpose}</strong>:</p>
      <div class="otp-box">{otp_code}</div>
      <p class="note">This code will expire in <strong>10 minutes</strong>.<br>If you did not request this verification, please contact your administrator immediately.</p>
    </div>
    <div class="footer">
      Automated verification sent by PresentSync Digital Facial Attendance System.
    </div>
  </div>
</body>
</html>"""
    return send_email(to_email, subject, body_text, body_html)


def get_mail_config():
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "use_tls": os.environ.get("SMTP_USE_TLS", "1") == "1",
        "sender": os.environ.get("MAIL_SENDER", "").strip()
        or os.environ.get("SMTP_USER", "attendance@school.edu"),
    }


def send_email(to_email, subject, body_text, body_html=None):
    cfg = get_mail_config()
    if not cfg["host"] or not cfg["user"]:
        logger.info(
            "[SIMULATED EMAIL] To: %s | Subject: %s | Host/User unconfigured in .env\nContent:\n%s",
            to_email,
            subject,
            body_text,
        )
        return True, "Simulated (SMTP credentials not configured in .env)"

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg["sender"]
        msg["To"] = to_email
        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15) as server:
                server.login(cfg["user"], cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
                if cfg["use_tls"]:
                    server.starttls()
                if cfg["user"] and cfg["password"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(msg)

        logger.info("Successfully sent email to %s", to_email)
        return True, "Sent"
    except Exception as exc:
        logger.warning("Failed to send email to %s: %s", to_email, exc)
        return False, str(exc)


def create_lecture_email_content(student_name, class_name, faculty, class_date, timing, marked_time):
    subject = f"Attendance Confirmed: {class_name} ({class_date})"
    body_text = f"""Hello {student_name},

Your attendance for the following lecture has been successfully recorded:

Lecture / Class: {class_name}
Date: {class_date}
Scheduled Time: {timing}
Faculty: {faculty}
Time Recorded: {marked_time}
Status: PRESENT

This is an automated confirmation sent upon the completion of the lecture.

Regards,
Attendance Management System
"""

    body_html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f5f5f7; margin: 0; padding: 24px; color: #1d1d1f; }}
    .card {{ background: #ffffff; max-width: 540px; margin: 0 auto; border-radius: 14px; overflow: hidden; border: 1px solid #e5e5ea; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
    .header {{ background: #0071e3; color: #ffffff; padding: 20px 24px; }}
    .header {{ background: #0071e3; color: #ffffff; padding: 20px 24px; }}
    .header h2 {{ margin: 0 0 4px 0; font-size: 20px; }}
    .header p {{ margin: 0; font-size: 13px; opacity: 0.9; }}
    .content {{ padding: 24px; }}
    .badge {{ display: inline-block; background: #e8f5e9; color: #2e7d32; font-weight: 600; padding: 4px 10px; border-radius: 20px; font-size: 12px; margin-bottom: 16px; }}
    .details {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    .details td {{ padding: 8px 0; font-size: 14px; border-bottom: 1px solid #f2f2f7; }}
    .details td.label {{ color: #86868b; width: 40%; font-weight: 500; }}
    .details td.value {{ color: #1d1d1f; font-weight: 600; }}
    .footer {{ padding: 16px 24px; background: #fafafc; font-size: 12px; color: #86868b; text-align: center; border-top: 1px solid #f2f2f7; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2>Attendance Confirmation</h2>
      <p>Digital Facial Recognition Attendance System</p>
    </div>
    <div class="content">
      <div class="badge">&#10003; PRESENT RECORDED</div>
      <p>Hello <strong>{student_name}</strong>,</p>
      <p>Your attendance was recorded for today's lecture. Here are the session details:</p>
      <table class="details">
        <tr><td class="label">Class / Lecture</td><td class="value">{class_name}</td></tr>
        <tr><td class="label">Date</td><td class="value">{class_date}</td></tr>
        <tr><td class="label">Scheduled Time</td><td class="value">{timing}</td></tr>
        <tr><td class="label">Faculty</td><td class="value">{faculty}</td></tr>
        <tr><td class="label">Marked At (UTC)</td><td class="value">{marked_time}</td></tr>
      </table>
    </div>
    <div class="footer">
      Automated message delivered after completion of the lecture. Please do not reply.
    </div>
  </div>
</body>
</html>"""
    return subject, body_text, body_html


def dispatch_post_lecture_emails(class_id, connect_db_func):
    """
    Sends attendance confirmation emails to all students present in `class_id`
    whose emails have not yet been sent.
    """
    with connect_db_func() as conn:
        class_row = conn.execute(
            "SELECT name, class_date, class_time, end_time, faculty FROM classes WHERE id = ?",
            (class_id,),
        ).fetchone()
        if not class_row:
            logger.warning("Cannot dispatch emails: Class ID %s not found.", class_id)
            return {"sent": 0, "failed": 0, "skipped": 0}

        class_name = class_row["name"]
        class_date = class_row["class_date"]
        timing = f"{class_row['class_time']} - {class_row['end_time']}"
        faculty = class_row["faculty"]

        records = conn.execute(
            """SELECT a.id, a.timestamp, s.id as student_id, s.name, s.email
               FROM attendance a
               JOIN students s ON s.id = a.student_id
               WHERE a.class_id = ? AND (a.email_sent IS NULL OR a.email_sent = 0)""",
            (class_id,),
        ).fetchall()

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for row in records:
        student_email = (row["email"] or "").strip()
        if not student_email:
            skipped_count += 1
            continue

        subj, txt, html = create_lecture_email_content(
            student_name=row["name"],
            class_name=class_name,
            faculty=faculty,
            class_date=class_date,
            timing=timing,
            marked_time=row["timestamp"],
        )

        success, _ = send_email(student_email, subj, txt, html)
        if success:
            sent_count += 1
            with connect_db_func() as conn:
                conn.execute(
                    "UPDATE attendance SET email_sent = 1, email_sent_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
        else:
            failed_count += 1

    logger.info(
        "Dispatched lecture emails for class %s: %s sent, %s failed, %s skipped (no email).",
        class_id,
        sent_count,
        failed_count,
        skipped_count,
    )
    return {"sent": sent_count, "failed": failed_count, "skipped": skipped_count}
