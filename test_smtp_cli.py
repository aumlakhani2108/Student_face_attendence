#!/usr/bin/env python3
"""
BioBytes - SMTP Pipeline Diagnostic & Test Utility
Usage:
    python test_smtp_cli.py [optional_recipient_email]

This script tests your SMTP settings defined in .env and attempts to send a real test email.
"""

import os
import sys
from dotenv import load_dotenv

# Ensure .env is always freshly loaded even if environment variables were set in shell
load_dotenv(override=True)

from mailer import get_mail_config, test_smtp_connection

def main():
    print("=" * 60)
    print("BioBytes - SMTP Email Pipeline Diagnostic Tool")
    print("=" * 60)
    
    cfg = get_mail_config()
    print(f"SMTP Host:     {cfg['host'] or '[NOT SET]'}")
    print(f"SMTP Port:     {cfg['port']}")
    print(f"SMTP User:     {cfg['user'] or '[NOT SET]'}")
    print(f"SMTP Password: {'*' * len(cfg['password']) if cfg['password'] else '[NOT SET]'}")
    print(f"Use TLS:       {cfg['use_tls']}")
    print(f"Sender Email:  {cfg['sender']}")
    print("-" * 60)
    
    if not cfg["host"] or not cfg["user"]:
        print("\n[!] WARNING: SMTP is currently unconfigured in .env.")
        print("To send real emails, update your .env file with:")
        print("  SMTP_HOST=smtp.gmail.com")
        print("  SMTP_PORT=465")
        print("  SMTP_USER=aumlakhani2108@gmail.com")
        print("  SMTP_PASSWORD=your_16_char_google_app_password")
        print("  SMTP_USE_TLS=1")
        print("  MAIL_SENDER=aumlakhani2108@gmail.com\n")
        return 1

    recipient = sys.argv[1].strip() if len(sys.argv) > 1 else cfg["user"]
    print(f"\n[*] Testing connection to {cfg['host']}:{cfg['port']}...")
    if recipient:
        print(f"[*] Attempting to deliver test email to: {recipient}")
        
    result = test_smtp_connection(target_email=recipient)
    
    if result["success"]:
        print("\n[SUCCESS] " + result["message"])
        print("\nYour SMTP pipeline is fully operational! OTP codes and attendance receipts will be sent to real inboxes.")
        return 0
    else:
        print("\n[FAILED] " + result["message"])
        print("\nTroubleshooting Tips:")
        print("1. In Google Account, make sure 2-Step Verification is turned ON.")
        print("2. Generate a 16-character App Password at: https://myaccount.google.com/apppasswords")
        print("   - In 'App name', enter 'BioBytes' and click 'Create'.")
        print("   - Copy the 16 characters (e.g., abcd efgh ijkl mnop) into SMTP_PASSWORD in .env.")
        print("3. Ensure the Google account you are logged into in Chrome matches SMTP_USER.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
