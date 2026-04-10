"""
Standalone missed-dose reminder script.
Can be run as a cron job:  */5 * * * * python /path/to/cron/reminder.py

Or use the built-in APScheduler inside app.py (default).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
from datetime import datetime, date

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

import config

# ─── AWS clients ──────────────────────────────────────────
boto_kwargs = dict(region_name=config.AWS_REGION)
if config.AWS_ACCESS_KEY_ID:
    boto_kwargs["aws_access_key_id"]     = config.AWS_ACCESS_KEY_ID
    boto_kwargs["aws_secret_access_key"] = config.AWS_SECRET_ACCESS_KEY

dynamodb    = boto3.resource("dynamodb", **boto_kwargs)
sns_client  = boto3.client("sns", **boto_kwargs)

users_table = dynamodb.Table(config.USERS_TABLE)
meds_table  = dynamodb.Table(config.MEDICATIONS_TABLE)
logs_table  = dynamodb.Table(config.LOGS_TABLE)


def send_alert(message: str):
    if not config.SNS_TOPIC_ARN:
        print(f"[SNS SKIP] {message}")
        return
    try:
        sns_client.publish(
            TopicArn=config.SNS_TOPIC_ARN,
            Message=message,
            Subject="Missed Dose Alert – MedTrack",
        )
        print("[SNS] Alert sent.")
    except ClientError as e:
        print(f"[SNS ERROR] {e}")


def check_missed():
    now   = datetime.now()
    today = date.today().isoformat()

    resp = meds_table.scan()
    meds = resp.get("Items", [])

    for med in meds:
        med_id = med["med_id"]
        sched  = med.get("scheduled_time", "")
        if not sched:
            continue
        try:
            sched_dt = datetime.strptime(f"{today} {sched}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        minutes_late = (now - sched_dt).total_seconds() / 60
        if not (config.MISSED_DOSE_WINDOW_MINUTES <= minutes_late <= config.MISSED_DOSE_WINDOW_MINUTES + 10):
            continue

        existing = logs_table.query(
            IndexName="med-index",
            KeyConditionExpression=Key("med_id").eq(med_id),
            FilterExpression=Attr("log_date").eq(today),
        )
        if existing.get("Items"):
            continue

        logs_table.put_item(Item={
            "log_id":     str(uuid.uuid4()),
            "med_id":     med_id,
            "user_id":    med.get("user_id", ""),
            "log_date":   today,
            "taken_time": "",
            "status":     "missed",
            "created_at": now.isoformat(),
        })

        user_resp = users_table.get_item(Key={"user_id": med.get("user_id", "")})
        user = user_resp.get("Item", {})
        msg  = (
            f"⚠️ MISSED DOSE ALERT\n\n"
            f"Patient : {user.get('name', 'Unknown')}\n"
            f"Medicine: {med.get('name', '')} {med.get('dosage', '')}\n"
            f"Scheduled: {sched}\n"
            f"Date    : {today}\n\n"
            f"Please follow up with the patient immediately."
        )
        send_alert(msg)
        print(f"[Reminder] Missed: {med.get('name')} for {user.get('name')}")


if __name__ == "__main__":
    print(f"[Reminder] Running at {datetime.now().strftime('%H:%M:%S')}")
    check_missed()
    print("[Reminder] Done.")