"""
Medication Adherence Monitoring System
Flask Backend · DynamoDB · AWS SNS
Final Corrected Version for EC2 & SNS Alerts
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
from werkzeug.middleware.proxy_fix import ProxyFix

import config

# ─── App Setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ─── AWS Clients ──────────────────────────────────────────────────────────────
boto_kwargs = dict(region_name=config.AWS_REGION)

if config.AWS_ACCESS_KEY_ID:
    boto_kwargs["aws_access_key_id"] = config.AWS_ACCESS_KEY_ID
    boto_kwargs["aws_secret_access_key"] = config.AWS_SECRET_ACCESS_KEY

dynamodb = boto3.resource("dynamodb", **boto_kwargs)
sns = boto3.client("sns", **boto_kwargs)

users_table = dynamodb.Table(config.USERS_TABLE)
meds_table = dynamodb.Table(config.MEDICATIONS_TABLE)
logs_table = dynamodb.Table(config.LOGS_TABLE)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    salt = "medtrack_salt"
    return hashlib.sha256((pw + salt).encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def send_sns_alert(message: str, subject: str = "MedTrack Alert", email=None):
    """Sends an alert via SNS Topic. Filtered by email attribute if provided."""
    try:
        if not config.SNS_TOPIC_ARN:
            print("[SNS ERROR] SNS_TOPIC_ARN is missing in config.")
            return

        publish_kwargs = {
            "TopicArn": config.SNS_TOPIC_ARN,
            "Message": message,
            "Subject": subject
        }

        if email:
            publish_kwargs["MessageAttributes"] = {
                'email': {'DataType': 'String', 'StringValue': email}
            }

        sns.publish(**publish_kwargs)
        print(f"[SNS SUCCESS] Alert sent to {email if email else 'Topic'}")
    except ClientError as e:
        print(f"[SNS ERROR] {e}")

def today_str():
    return date.today().isoformat()

def now_str():
    return datetime.now().strftime("%H:%M")

# ─── DynamoDB Bootstrap ───────────────────────────────────────────────────────
def ensure_tables():
    existing = {t.name for t in dynamodb.tables.all()}
    def create_if_missing(name, key_schema, attr_defs, gsi=None):
        if name in existing: return
        params = dict(TableName=name, KeySchema=key_schema, AttributeDefinitions=attr_defs, BillingMode="PAY_PER_REQUEST")
        if gsi: params["GlobalSecondaryIndexes"] = gsi
        dynamodb.create_table(**params)
        print(f"[DynamoDB] Created table: {name}")

    create_if_missing(config.USERS_TABLE, [{"AttributeName": "user_id", "KeyType": "HASH"}], 
                      [{"AttributeName": "user_id", "AttributeType": "S"}, {"AttributeName": "email", "AttributeType": "S"}],
                      gsi=[{"IndexName": "email-index", "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}], "Projection": {"ProjectionType": "ALL"}}])
    create_if_missing(config.MEDICATIONS_TABLE, [{"AttributeName": "med_id", "KeyType": "HASH"}],
                      [{"AttributeName": "med_id", "AttributeType": "S"}, {"AttributeName": "user_id", "AttributeType": "S"}],
                      gsi=[{"IndexName": "user-index", "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}], "Projection": {"ProjectionType": "ALL"}}])
    create_if_missing(config.LOGS_TABLE, [{"AttributeName": "log_id", "KeyType": "HASH"}],
                      [{"AttributeName": "log_id", "AttributeType": "S"}, {"AttributeName": "med_id", "AttributeType": "S"}],
                      gsi=[{"IndexName": "med-index", "KeySchema": [{"AttributeName": "med_id", "KeyType": "HASH"}], "Projection": {"ProjectionType": "ALL"}}])

# ─── Scheduler ────────────────────────────────────────────────────────────────
def check_missed_doses():
    try:
        now = datetime.now()
        today = today_str()
        meds = meds_table.scan().get("Items", [])
        for med in meds:
            scheduled_tm = med.get("scheduled_time")
            if not scheduled_tm: continue
            try:
                sched_dt = datetime.strptime(f"{today} {scheduled_tm}", "%Y-%m-%d %H:%M")
            except: continue

            if sched_dt < now and (now - sched_dt).total_seconds() // 60 > config.MISSED_DOSE_WINDOW_MINUTES:
                log_resp = logs_table.query(IndexName="med-index", KeyConditionExpression=Key("med_id").eq(med["med_id"]), FilterExpression=Attr("log_date").eq(today))
                if log_resp.get("Items"): continue

                logs_table.put_item(Item={
                    "log_id": str(uuid.uuid4()), "med_id": med["med_id"], "user_id": med.get("user_id"),
                    "log_date": today, "status": "missed", "created_at": datetime.now().isoformat()
                })
                user = users_table.get_item(Key={"user_id": med["user_id"]}).get("Item", {})
                msg = f"⚠️ MISSED DOSE: {user.get('name')} missed {med.get('name')} scheduled for {scheduled_tm}."
                send_sns_alert(msg, email=user.get("caregiver_email"))
    except Exception as e: print(f"[Scheduler ERROR] {e}")

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index(): return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email, pw = request.form.get("email").lower(), request.form.get("password")
        items = users_table.query(IndexName="email-index", KeyConditionExpression=Key("email").eq(email)).get("Items", [])
        if items and items[0]["password"] == hash_password(pw):
            session.update({"user_id": items[0]["user_id"], "name": items[0]["name"], "email": items[0]["email"]})
            return redirect(url_for("dashboard"))
        flash("Invalid credentials", "error")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name, email, pw = request.form.get("name"), request.form.get("email").lower(), request.form.get("password")
        if users_table.query(IndexName="email-index", KeyConditionExpression=Key("email").eq(email)).get("Items"):
            flash("Email exists", "error"); return render_template("signup.html")
        u_id = str(uuid.uuid4())
        users_table.put_item(Item={"user_id": u_id, "name": name, "email": email, "password": hash_password(pw), "created_at": datetime.now().isoformat()})
        session.update({"user_id": u_id, "name": name, "email": email})
        return redirect(url_for("dashboard"))
    return render_template("signup.html")

@app.route("/dashboard")
@login_required
def dashboard():
    u_id, today = session["user_id"], today_str()
    meds = meds_table.query(IndexName="user-index", KeyConditionExpression=Key("user_id").eq(u_id)).get("Items", [])
    logs = logs_table.scan(FilterExpression=Attr("user_id").eq(u_id) & Attr("log_date").eq(today)).get("Items", [])
    taken_ids = {l["med_id"] for l in logs if l.get("status") == "taken"}
    missed_ids = {l["med_id"] for l in logs if l.get("status") == "missed"}
    total = len(meds)
    taken, missed = len(taken_ids), len(missed_ids)
    return render_template("dashboard.html", meds=meds, taken_ids=taken_ids, missed_ids=missed_ids, total=total, taken=taken, missed=missed, pending=total-taken-missed, pct=round((taken/total)*100) if total else 0, today=today)

@app.route("/caregiver", methods=["GET", "POST"])
@login_required
def caregiver():
    u_id = session["user_id"]
    if request.method == "POST":
        email = request.form.get("caregiver_email", "").strip()
        phone = request.form.get("caregiver_phone", "").strip()
        
        # --- FIX: Subscribe the caregiver email to SNS ---
        if email and config.SNS_TOPIC_ARN:
            try:
                sns.subscribe(TopicArn=config.SNS_TOPIC_ARN, Protocol='email', Endpoint=email)
                flash("Caregiver saved! They MUST confirm the email sent by AWS to receive alerts.", "info")
            except Exception as e:
                print(f"SNS Subscribe Error: {e}")

        users_table.update_item(Key={"user_id": u_id}, UpdateExpression="SET caregiver_email=:e, caregiver_phone=:p", ExpressionAttributeValues={":e": email, ":p": phone})
        flash("Settings saved!", "success")

    user = users_table.get_item(Key={"user_id": u_id}).get("Item", {})
    return render_template("caregiver.html", user=user)

@app.route("/mark_taken/<med_id>", methods=["POST"])
@login_required
def mark_taken(med_id):
    today, u_id = today_str(), session["user_id"]
    if not logs_table.query(IndexName="med-index", KeyConditionExpression=Key("med_id").eq(med_id), FilterExpression=Attr("log_date").eq(today)).get("Items"):
        logs_table.put_item(Item={"log_id": str(uuid.uuid4()), "med_id": med_id, "user_id": u_id, "log_date": today, "taken_time": now_str(), "status": "taken", "created_at": datetime.now().isoformat()})
        flash("Marked as taken!", "success")
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

# Routes for Medications, Logs, and Alerts omitted for brevity but remain the same as your logic
@app.route("/medications")
@login_required
def medications():
    resp = meds_table.query(IndexName="user-index", KeyConditionExpression=Key("user_id").eq(session["user_id"]))
    meds = sorted(resp.get("Items", []), key=lambda m: m.get("scheduled_time", ""))
    return render_template("medications.html", meds=meds)

@app.route("/add_medication", methods=["GET", "POST"])
@login_required
def add_medication():
    if request.method == "POST":
        meds_table.put_item(Item={"med_id": str(uuid.uuid4()), "user_id": session["user_id"], "name": request.form.get("name"), "dosage": request.form.get("dosage"), "scheduled_time": request.form.get("scheduled_time"), "frequency": request.form.get("frequency", "daily"), "notes": request.form.get("notes"), "created_at": datetime.now().isoformat()})
        flash("Medication added!", "success"); return redirect(url_for("medications"))
    return render_template("add_medication.html")

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_tables()
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_missed_doses, "interval", minutes=5)
    scheduler.start()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
