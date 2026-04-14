import os
from dotenv import load_dotenv

load_dotenv()

# ─── Flask ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please")

# ─── AWS ──────────────────────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION            = os.getenv("AWS_REGION", "us-east-1")

# ─── DynamoDB table names ─────────────────────────────────────────────────────
USERS_TABLE       = "medtrack_users"
MEDICATIONS_TABLE = "medtrack_medications"
LOGS_TABLE        = "medtrack_logs"

# ─── SNS ──────────────────────────────────────────────────────────────────────
# In config.py
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-south-1:276483282936:medtrack")
# ─── Scheduler ────────────────────────────────────────────────────────────────
MISSED_DOSE_WINDOW_MINUTES = 30   # how many minutes after scheduled time = "missed"
