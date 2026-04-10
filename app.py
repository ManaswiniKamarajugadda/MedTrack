"""
Medication Adherence Monitoring System
Flask Backend  ·  DynamoDB  ·  AWS SNS
"""

import uuid, hashlib, json
from datetime import datetime, date
from functools import wraps

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from apscheduler.schedulers.background import BackgroundScheduler

import config

# ─── App Setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# ─── AWS Clients ──────────────────────────────────────────────────────────────
boto_kwargs = dict(region_name=config.AWS_REGION)
if config.AWS_ACCESS_KEY_ID:
    boto_kwargs["aws_access_key_id"]     = config.AWS_ACCESS_KEY_ID
    boto_kwargs["aws_secret_access_key"] = config.AWS_SECRET_ACCESS_KEY

dynamodb = boto3.resource("dynamodb", **boto_kwargs)
sns      = boto3.client("sns", **boto_kwargs)

users_table = dynamodb.Table(config.USERS_TABLE)
meds_table  = dynamodb.Table(config.MEDICATIONS_TABLE)
logs_table  = dynamodb.Table(config.LOGS_TABLE)


# ─── Helpers ──────────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def send_sns_alert(message: str, subject: str = "MedTrack Alert"):
    if not config.SNS_TOPIC_ARN:
        print(f"[SNS SKIP] No ARN configured. Message: {message}")
        return
    try:
        sns.publish(TopicArn=config.SNS_TOPIC_ARN,
                    Message=message, Subject=subject)
    except ClientError as e:
        print(f"[SNS ERROR] {e}")

def today_str():
    return date.today().isoformat()

def now_str():
    return datetime.now().strftime("%H:%M")


# ─── DynamoDB Table Bootstrap (create if not exist) ───────────────────────────
def ensure_tables():
    existing = {t.name for t in dynamodb.tables.all()}

    def create_if_missing(name, key_schema, attr_defs, gsi=None):
        if name in existing:
            return
        params = dict(
            TableName=name,
            KeySchema=key_schema,
            AttributeDefinitions=attr_defs,
            BillingMode="PAY_PER_REQUEST",
        )
        if gsi:
            params["GlobalSecondaryIndexes"] = gsi
        dynamodb.create_table(**params)
        print(f"[DynamoDB] Created table: {name}")

    # Users
    create_if_missing(
        config.USERS_TABLE,
        [{"AttributeName": "user_id", "KeyType": "HASH"}],
        [{"AttributeName": "user_id", "AttributeType": "S"},
         {"AttributeName": "email",   "AttributeType": "S"}],
        gsi=[{
            "IndexName": "email-index",
            "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )

    # Medications
    create_if_missing(
        config.MEDICATIONS_TABLE,
        [{"AttributeName": "med_id",  "KeyType": "HASH"}],
        [{"AttributeName": "med_id",  "AttributeType": "S"},
         {"AttributeName": "user_id", "AttributeType": "S"}],
        gsi=[{
            "IndexName": "user-index",
            "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )

    # Logs
    create_if_missing(
        config.LOGS_TABLE,
        [{"AttributeName": "log_id", "KeyType": "HASH"}],
        [{"AttributeName": "log_id",  "AttributeType": "S"},
         {"AttributeName": "med_id",  "AttributeType": "S"}],
        gsi=[{
            "IndexName": "med-index",
            "KeySchema": [{"AttributeName": "med_id", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )


# ─── Missed-Dose Scheduler ────────────────────────────────────────────────────
def check_missed_doses():
    """Runs every 5 minutes; marks overdue meds as missed & sends SNS alerts."""
    try:
        now   = datetime.now()
        today = today_str()
        resp  = meds_table.scan()
        meds  = resp.get("Items", [])

        for med in meds:
            med_id       = med["med_id"]
            scheduled_tm = med.get("scheduled_time", "")
            if not scheduled_tm:
                continue

            # Build scheduled datetime for today
            try:
                sched_dt = datetime.strptime(f"{today} {scheduled_tm}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue

            window = config.MISSED_DOSE_WINDOW_MINUTES
            if not (sched_dt < now and (now - sched_dt).seconds // 60 <= window + 5):
                continue

            # Check if a log already exists for today
            log_resp = logs_table.query(
                IndexName="med-index",
                KeyConditionExpression=Key("med_id").eq(med_id),
                FilterExpression=Attr("log_date").eq(today),
            )
            if log_resp.get("Items"):
                continue   # already logged (taken or missed)

            # Create missed log
            logs_table.put_item(Item={
                "log_id":       str(uuid.uuid4()),
                "med_id":       med_id,
                "user_id":      med.get("user_id", ""),
                "log_date":     today,
                "taken_time":   "",
                "status":       "missed",
                "created_at":   datetime.now().isoformat(),
            })

            # Fetch user & caregiver info for alert
            user_resp = users_table.get_item(Key={"user_id": med.get("user_id", "")})
            user      = user_resp.get("Item", {})
            msg = (
                f"⚠️ MISSED DOSE ALERT\n\n"
                f"Patient : {user.get('name', 'Unknown')}\n"
                f"Medicine: {med.get('name', '')} {med.get('dosage', '')}\n"
                f"Scheduled: {scheduled_tm}\n"
                f"Date    : {today}\n\n"
                f"Please follow up with the patient."
            )
            send_sns_alert(msg, subject="Missed Dose Alert – MedTrack")
            print(f"[Scheduler] Missed dose logged: {med.get('name')} for {user.get('name')}")

    except Exception as e:
        print(f"[Scheduler ERROR] {e}")


# ─── AUTH Routes ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        resp  = users_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email),
        )
        items = resp.get("Items", [])
        if items and items[0]["password"] == hash_password(password):
            u = items[0]
            session["user_id"] = u["user_id"]
            session["name"]    = u["name"]
            session["email"]   = u["email"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Check duplicate
        existing = users_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email),
        )
        if existing.get("Items"):
            flash("Email already registered.", "error")
            return render_template("signup.html")

        user_id = str(uuid.uuid4())
        users_table.put_item(Item={
            "user_id":    user_id,
            "name":       name,
            "email":      email,
            "password":   hash_password(password),
            "caregiver_email": "",
            "caregiver_phone": "",
            "created_at": datetime.now().isoformat(),
        })
        session["user_id"] = user_id
        session["name"]    = name
        session["email"]   = email
        return redirect(url_for("dashboard"))
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── DASHBOARD ────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    today   = today_str()

    # Get all meds for this user
    resp = meds_table.query(
        IndexName="user-index",
        KeyConditionExpression=Key("user_id").eq(user_id),
    )
    meds = resp.get("Items", [])

    # Today's logs
    log_resp = logs_table.scan(
        FilterExpression=Attr("user_id").eq(user_id) & Attr("log_date").eq(today)
    )
    logs      = log_resp.get("Items", [])
    taken_ids = {l["med_id"] for l in logs if l.get("status") == "taken"}
    missed_ids= {l["med_id"] for l in logs if l.get("status") == "missed"}

    total   = len(meds)
    taken   = len(taken_ids)
    missed  = len(missed_ids)
    pending = total - taken - missed
    pct     = round((taken / total) * 100) if total else 0

    return render_template(
        "dashboard.html",
        meds=meds, taken_ids=taken_ids, missed_ids=missed_ids,
        total=total, taken=taken, missed=missed, pending=pending, pct=pct,
        today=today,
    )


# ─── MEDICATIONS ──────────────────────────────────────────────────────────────
@app.route("/medications")
@login_required
def medications():
    resp = meds_table.query(
        IndexName="user-index",
        KeyConditionExpression=Key("user_id").eq(session["user_id"]),
    )
    meds = sorted(resp.get("Items", []), key=lambda m: m.get("scheduled_time", ""))
    return render_template("medications.html", meds=meds)

@app.route("/add_medication", methods=["GET", "POST"])
@login_required
def add_medication():
    if request.method == "POST":
        med_id = str(uuid.uuid4())
        meds_table.put_item(Item={
            "med_id":         med_id,
            "user_id":        session["user_id"],
            "name":           request.form.get("name", "").strip(),
            "dosage":         request.form.get("dosage", "").strip(),
            "scheduled_time": request.form.get("scheduled_time", ""),
            "frequency":      request.form.get("frequency", "daily"),
            "notes":          request.form.get("notes", "").strip(),
            "created_at":     datetime.now().isoformat(),
        })
        flash("Medication added successfully!", "success")
        return redirect(url_for("medications"))
    return render_template("add_medication.html")

@app.route("/edit_medication/<med_id>", methods=["GET", "POST"])
@login_required
def edit_medication(med_id):
    resp = meds_table.get_item(Key={"med_id": med_id})
    med  = resp.get("Item")
    if not med or med["user_id"] != session["user_id"]:
        flash("Medication not found.", "error")
        return redirect(url_for("medications"))

    if request.method == "POST":
        meds_table.update_item(
            Key={"med_id": med_id},
            UpdateExpression="SET #n=:n, dosage=:d, scheduled_time=:t, frequency=:f, notes=:no",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={
                ":n":  request.form.get("name", "").strip(),
                ":d":  request.form.get("dosage", "").strip(),
                ":t":  request.form.get("scheduled_time", ""),
                ":f":  request.form.get("frequency", "daily"),
                ":no": request.form.get("notes", "").strip(),
            },
        )
        flash("Medication updated.", "success")
        return redirect(url_for("medications"))
    return render_template("add_medication.html", med=med, edit=True)

@app.route("/delete_medication/<med_id>", methods=["POST"])
@login_required
def delete_medication(med_id):
    resp = meds_table.get_item(Key={"med_id": med_id})
    med  = resp.get("Item")
    if med and med["user_id"] == session["user_id"]:
        meds_table.delete_item(Key={"med_id": med_id})
        flash("Medication deleted.", "success")
    return redirect(url_for("medications"))


# ─── MARK TAKEN ───────────────────────────────────────────────────────────────
@app.route("/mark_taken/<med_id>", methods=["POST"])
@login_required
def mark_taken(med_id):
    today = today_str()
    # Avoid duplicates
    existing = logs_table.query(
        IndexName="med-index",
        KeyConditionExpression=Key("med_id").eq(med_id),
        FilterExpression=Attr("log_date").eq(today),
    )
    if not existing.get("Items"):
        logs_table.put_item(Item={
            "log_id":     str(uuid.uuid4()),
            "med_id":     med_id,
            "user_id":    session["user_id"],
            "log_date":   today,
            "taken_time": now_str(),
            "status":     "taken",
            "created_at": datetime.now().isoformat(),
        })
    return redirect(url_for("dashboard"))


# ─── LOGS ─────────────────────────────────────────────────────────────────────
@app.route("/logs")
@login_required
def logs():
    resp = logs_table.scan(
        FilterExpression=Attr("user_id").eq(session["user_id"])
    )
    all_logs = sorted(resp.get("Items", []),
                      key=lambda l: l.get("created_at", ""), reverse=True)

    # Attach med names
    med_cache = {}
    for log in all_logs:
        mid = log.get("med_id", "")
        if mid not in med_cache:
            mr = meds_table.get_item(Key={"med_id": mid})
            med_cache[mid] = mr.get("Item", {})
        log["med_name"] = med_cache[mid].get("name", "Unknown")

    return render_template("logs.html", logs=all_logs)


# ─── CAREGIVER ────────────────────────────────────────────────────────────────
@app.route("/caregiver", methods=["GET", "POST"])
@login_required
def caregiver():
    user_id = session["user_id"]
    if request.method == "POST":
        email = request.form.get("caregiver_email", "").strip()
        phone = request.form.get("caregiver_phone", "").strip()
        users_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET caregiver_email=:e, caregiver_phone=:p",
            ExpressionAttributeValues={":e": email, ":p": phone},
        )
        flash("Caregiver settings saved!", "success")

    resp = users_table.get_item(Key={"user_id": user_id})
    user = resp.get("Item", {})
    return render_template("caregiver.html", user=user)


# ─── ALERTS ───────────────────────────────────────────────────────────────────
@app.route("/alerts")
@login_required
def alerts():
    resp = logs_table.scan(
        FilterExpression=Attr("user_id").eq(session["user_id"])
                       & Attr("status").eq("missed")
    )
    missed_logs = sorted(resp.get("Items", []),
                         key=lambda l: l.get("created_at", ""), reverse=True)

    med_cache = {}
    for log in missed_logs:
        mid = log.get("med_id", "")
        if mid not in med_cache:
            mr = meds_table.get_item(Key={"med_id": mid})
            med_cache[mid] = mr.get("Item", {})
        log["med_name"] = med_cache[mid].get("name", "Unknown")
        log["dosage"]   = med_cache[mid].get("dosage", "")

    return render_template("alerts.html", missed_logs=missed_logs)


# ─── API ──────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def api_stats():
    user_id = session["user_id"]
    today   = today_str()

    resp = meds_table.query(
        IndexName="user-index",
        KeyConditionExpression=Key("user_id").eq(user_id),
    )
    meds  = resp.get("Items", [])
    total = len(meds)

    log_resp = logs_table.scan(
        FilterExpression=Attr("user_id").eq(user_id) & Attr("log_date").eq(today)
    )
    logs   = log_resp.get("Items", [])
    taken  = sum(1 for l in logs if l.get("status") == "taken")
    missed = sum(1 for l in logs if l.get("status") == "missed")
    pct    = round((taken / total) * 100) if total else 0

    return jsonify(total=total, taken=taken, missed=missed,
                   pending=total-taken-missed, pct=pct)


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_tables()

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_missed_doses, "interval", minutes=5)
    scheduler.start()

    app.run(debug=True, use_reloader=False)