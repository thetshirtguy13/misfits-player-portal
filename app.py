from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from datetime import datetime, date
from functools import wraps
import csv, io, os, json, secrets, smtplib, uuid
import boto3
from email.message import EmailMessage

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower()=="true",
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
)
db_url = os.environ.get("DATABASE_URL", "sqlite:///misfits.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
migrate = Migrate(app, db)
limiter = Limiter(get_remote_address, app=app, default_limits=["300 per day", "100 per hour"], storage_uri="memory://")
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

# ---------------- Models ----------------
class User(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    role=db.Column(db.String(20), nullable=False, default="player")
    name=db.Column(db.String(120), nullable=False)
    email=db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash=db.Column(db.String(255), nullable=False)
    age_group=db.Column(db.String(20), default="")
    position=db.Column(db.String(50), default="")
    email_verified=db.Column(db.Boolean, default=False)
    consent_verified=db.Column(db.Boolean, default=False)
    parent_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    is_active=db.Column(db.Boolean, default=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at=db.Column(db.DateTime, nullable=True)

class Organization(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    name=db.Column(db.String(120), nullable=False)
    owner_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class Team(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    organization_id=db.Column(db.Integer, db.ForeignKey("organization.id"), nullable=False)
    name=db.Column(db.String(120), nullable=False)
    age_group=db.Column(db.String(20), default="")
    join_code=db.Column(db.String(16), unique=True, nullable=False)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class TeamMembership(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role=db.Column(db.String(20), nullable=False, default="player")
    approved=db.Column(db.Boolean, default=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__=(db.UniqueConstraint("team_id","user_id",name="uq_team_user"),)

class ConsentRequest(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    player_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    parent_email=db.Column(db.String(180), nullable=False)
    token_nonce=db.Column(db.String(64), nullable=False, default=lambda: secrets.token_urlsafe(24))
    approved_at=db.Column(db.DateTime, nullable=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class Workout(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    category=db.Column(db.String(30), nullable=False)
    title=db.Column(db.String(160), nullable=False)
    minutes=db.Column(db.Integer, default=30)
    level=db.Column(db.String(30), default="All ages")
    exercises=db.Column(db.Text, nullable=False)
    video_url=db.Column(db.String(500), default="")
    active=db.Column(db.Boolean, default=True)

class WorkoutCompletion(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    player_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    workout_id=db.Column(db.Integer, db.ForeignKey("workout.id"), nullable=False)
    completed_on=db.Column(db.Date, default=date.today)
    minutes=db.Column(db.Integer, default=0)
    notes=db.Column(db.Text, default="")
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class GameStat(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    player_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    played_on=db.Column(db.Date, default=date.today)
    opponent=db.Column(db.String(120), default="")
    ab=db.Column(db.Integer, default=0); hits=db.Column(db.Integer, default=0); walks=db.Column(db.Integer, default=0)
    runs=db.Column(db.Integer, default=0); rbi=db.Column(db.Integer, default=0); sb=db.Column(db.Integer, default=0)
    ip=db.Column(db.Float, default=0); pso=db.Column(db.Integer, default=0); er=db.Column(db.Integer, default=0); pitches=db.Column(db.Integer, default=0)
    source=db.Column(db.String(30), default="manual")
    created_at=db.Column(db.DateTime, default=datetime.utcnow)


class Media(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    owner_user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    player_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    object_key=db.Column(db.String(500), nullable=False)
    original_name=db.Column(db.String(255), nullable=False)
    content_type=db.Column(db.String(120), nullable=False)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class AuditLog(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action=db.Column(db.String(80), nullable=False)
    detail=db.Column(db.Text, default="")
    ip_address=db.Column(db.String(64), default="")
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

WORKOUTS=[
("Pitching","Arm Care & Throwing Foundation",25,"All ages","Dynamic warm-up — 5 min\nWrist flips — 25\nOne-knee throws — 25\nRocker throws — 20\nEasy catch — 10 min\nArm-care cooldown — 5 min"),
("Pitching","Pitching Mechanics Day",30,"10U+","Full-body warm-up — 5 min\nBalance holds — 3 x 20 sec\nStride-line dry reps — 15\nTowel drill — 3 x 8\nLow-intensity flat-ground throws — 15\nCooldown — 5 min"),
("Catching","Receiving & Blocking",30,"All ages","Catcher mobility — 5 min\nQuiet glove receiving — 3 x 20\nTennis-ball receives — 2 x 20\nDry blocking reps — 3 x 10\nShort-toss blocks — 3 x 8\nRecovery footwork — 2 x 10"),
("Catching","Footwork & Throwing",25,"10U+","Exchange drill — 3 x 15\nRight-left footwork — 3 x 10\nThrow to target — 15\nBunt fielding footwork — 10\nCooldown mobility — 5 min"),
("Batting","Tee Work Fundamentals",30,"All ages","Movement warm-up — 5 min\nStance/load mirror reps — 15\nMiddle tee — 25\nInside pitch tee — 20\nOutside pitch tee — 20\nQuality finish swings — 10"),
("Batting","Contact & Bat Speed",25,"10U+","Dry swings — 15\nTop-hand/bottom-hand — 15 each\nShort-bat/choke-up — 20\nFront toss/self toss — 30\nTwo-strike approach — 15"),
("Fielding","Ground Ball Fundamentals",30,"All ages","Athletic warm-up — 5 min\nReady-position reps — 15\nForehand ground balls — 20\nBackhands — 20\nFunnel and footwork — 20\nThrow to target — 15"),
("Fielding","Outfield Footwork & Fly Balls",30,"All ages","Drop steps — 3 x 8 each side\nAngle routes — 3 x 6\nSelf-toss fly balls — 20\nGround ball approach — 15\nCrow-hop throws — 15\nBackup responsibility review — 5 min")]

# ---------------- Helpers ----------------
def current_user():
    uid=session.get("user_id")
    u=db.session.get(User, uid) if uid else None
    return u if u and u.is_active else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not current_user(): return redirect(url_for("login"))
        return fn(*a,**kw)
    return wrapper

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a,**kw):
            u=current_user()
            if not u: return redirect(url_for("login"))
            if u.role not in roles: return redirect(url_for("dashboard"))
            return fn(*a,**kw)
        return wrapper
    return deco

def audit(action, detail="", who=None):
    u=who or current_user()
    db.session.add(AuditLog(user_id=u.id if u else None, action=action, detail=detail[:1500], ip_address=request.remote_addr or ""))
    db.session.commit()

def send_email(to, subject, body):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        app.logger.warning("RESEND_API_KEY is not configured")
        return False
    try:
        import urllib.request
        payload = json.dumps({
            "from": os.environ.get("MAIL_FROM", "onboarding@resend.dev"),
            "to": [to],
            "subject": subject,
            "text": body
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:
        app.logger.exception("Resend email failed")
        return False

def token_for(kind, payload):
    return serializer.dumps({"kind":kind, **payload})

def load_token(token, kind, max_age=3600):
    try:
        data=serializer.loads(token,max_age=max_age)
        return data if data.get("kind")==kind else None
    except (BadSignature, SignatureExpired): return None

def app_url(path):
    base=os.environ.get("APP_BASE_URL", request.url_root.rstrip("/"))
    return base.rstrip("/")+path

def send_verification(u):
    tok=token_for("verify",{"uid":u.id})
    link=app_url(url_for("verify_email",token=tok))
    sent=send_email(u.email,"Verify your Misfits account",f"Verify your email: {link}\n\nIf you did not create this account, ignore this message.")
    if not sent: flash("Development verification link: "+link)

def s3_client():
    endpoint=os.environ.get("S3_ENDPOINT_URL") or None
    return boto3.client("s3",endpoint_url=endpoint,aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),region_name=os.environ.get("S3_REGION","us-east-1"))

def media_bucket(): return os.environ.get("S3_BUCKET","")

def can_view_media(viewer, media):
    if viewer.id==media.owner_user_id or viewer.id==media.player_id: return True
    if media.player_id:
        player=db.session.get(User,media.player_id)
        if player and can_view_player(viewer,player): return True
    if media.team_id and media.team_id in team_ids_for(viewer): return True
    return False

def player_totals(pid):
    rows=GameStat.query.filter_by(player_id=pid).all()
    def s(k): return sum(getattr(r,k) or 0 for r in rows)
    ab,h,bb=s("ab"),s("hits"),s("walks")
    return {"games":len(rows),"ab":ab,"hits":h,"walks":bb,"runs":s("runs"),"rbi":s("rbi"),"sb":s("sb"),"ip":round(s("ip"),1),"pso":s("pso"),"er":s("er"),"pitches":s("pitches"),"avg":h/ab if ab else 0,"obp":(h+bb)/(ab+bb) if ab+bb else 0}

def team_ids_for(u):
    return [m.team_id for m in TeamMembership.query.filter_by(user_id=u.id,approved=True).all()]

def can_view_player(viewer, player):
    if viewer.id==player.id: return True
    if viewer.role=="parent" and player.parent_id==viewer.id: return True
    if viewer.role=="coach":
        return bool(set(team_ids_for(viewer)) & set(team_ids_for(player)))
    return False

@app.before_request
def bootstrap():
    db.create_all()
    if Workout.query.count()==0:
        for c,t,m,l,e in WORKOUTS: db.session.add(Workout(category=c,title=t,minutes=m,level=l,exercises=e))
        db.session.commit()

@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"]="nosniff"
    resp.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'self' https://*.myshopify.com https://*.square.site"
    return resp

# ---------------- Auth ----------------
@app.route("/")
def home(): return redirect(url_for("dashboard")) if current_user() else redirect(url_for("login"))

@app.route("/register",methods=["GET","POST"])
@limiter.limit("10 per hour")
def register():
    if request.method=="POST":
        role=request.form.get("role","player")
        if role not in {"player","parent","coach"}: role="player"
        email=request.form["email"].strip().lower(); password=request.form["password"]
        if len(password)<10: flash("Use a password with at least 10 characters."); return redirect(url_for("register"))
        if User.query.filter_by(email=email).first(): flash("That email already exists."); return redirect(url_for("register"))
        u=User(role=role,name=request.form["name"].strip(),email=email,password_hash=generate_password_hash(password),age_group=request.form.get("age_group","") if role=="player" else "",position=request.form.get("position","") if role=="player" else "",consent_verified=(role!="player"))
        db.session.add(u); db.session.commit(); audit("account_created",f"role={role}",u); send_verification(u)
        if role=="player":
            pe=request.form.get("parent_email","").strip().lower()
            if not pe: flash("A parent/guardian email is required for youth player accounts."); db.session.delete(u); db.session.commit(); return redirect(url_for("register"))
            cr=ConsentRequest(player_id=u.id,parent_email=pe); db.session.add(cr); db.session.commit()
            tok=token_for("consent",{"cid":cr.id,"nonce":cr.token_nonce})
            link=app_url(url_for("parent_consent",token=tok))
            sent=send_email(pe,"Parent consent for Misfits Player Development",f"Review and approve this player account: {link}")
            if not sent: flash("Development parent-consent link: "+link)
        session.clear(); session["user_id"]=u.id; return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login",methods=["GET","POST"])
@limiter.limit("10 per minute")
def login():
    if request.method=="POST":
        email=request.form["email"].strip().lower(); u=User.query.filter_by(email=email).first()
        if not u or not check_password_hash(u.password_hash,request.form["password"]): audit("login_failed",f"email={email}"); flash("Incorrect email or password."); return redirect(url_for("login"))
        if not u.is_active: flash("This account is disabled."); return redirect(url_for("login"))
        session.clear(); session["user_id"]=u.id; session.permanent=True; u.last_login_at=datetime.utcnow(); db.session.commit(); audit("login_success")
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout",methods=["POST"])
@login_required
def logout(): audit("logout"); session.clear(); return redirect(url_for("login"))

@app.route("/verify/<token>")
def verify_email(token):
    data=load_token(token,"verify",86400)
    if not data: flash("Verification link is invalid or expired."); return redirect(url_for("login"))
    u=db.session.get(User,data["uid"])
    if u: u.email_verified=True; db.session.commit(); audit("email_verified",who=u); flash("Email verified.")
    return redirect(url_for("dashboard"))

@app.route("/resend-verification",methods=["POST"])
@login_required
@limiter.limit("3 per hour")
def resend_verification(): send_verification(current_user()); flash("Verification message sent if email is configured."); return redirect(url_for("dashboard"))

@app.route("/forgot-password",methods=["GET","POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method=="POST":
        u=User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if u:
            tok=token_for("reset",{"uid":u.id,"ph":u.password_hash[-20:]}); link=app_url(url_for("reset_password",token=tok))
            sent=send_email(u.email,"Reset your Misfits password",f"Reset your password: {link}\nThis link expires in 1 hour.")
            if not sent: flash("Development reset link: "+link)
        flash("If that account exists, password-reset instructions were sent.")
        return redirect(url_for("login"))
    return render_template("forgot.html")

@app.route("/reset-password/<token>",methods=["GET","POST"])
def reset_password(token):
    data=load_token(token,"reset",3600)
    if not data: flash("Reset link is invalid or expired."); return redirect(url_for("forgot_password"))
    u=db.session.get(User,data["uid"])
    if not u or u.password_hash[-20:]!=data.get("ph"): flash("Reset link is no longer valid."); return redirect(url_for("forgot_password"))
    if request.method=="POST":
        pw=request.form["password"]
        if len(pw)<10: flash("Use at least 10 characters."); return redirect(request.url)
        u.password_hash=generate_password_hash(pw); db.session.commit(); audit("password_reset",who=u); flash("Password updated."); return redirect(url_for("login"))
    return render_template("reset.html")

@app.route("/parent-consent/<token>",methods=["GET","POST"])
def parent_consent(token):
    data=load_token(token,"consent",7*86400)
    cr=db.session.get(ConsentRequest,data["cid"]) if data else None
    if not cr or cr.token_nonce!=data.get("nonce"): return render_template("message.html",title="Consent link expired",message="Ask the player to request a new parent-consent invitation."),400
    player=db.session.get(User,cr.player_id)
    if request.method=="POST":
        cr.approved_at=datetime.utcnow(); player.consent_verified=True
        parent=User.query.filter_by(email=cr.parent_email,role="parent").first()
        if parent: player.parent_id=parent.id
        db.session.commit(); audit("parent_consent_approved",f"player_id={player.id}",parent or player)
        return render_template("message.html",title="Consent approved",message="The player account can now use development tracking features.")
    return render_template("consent.html",player=player,parent_email=cr.parent_email)

# ---------------- Main app ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    u=current_user(); comps=WorkoutCompletion.query.filter_by(player_id=u.id).order_by(WorkoutCompletion.completed_on.desc()).all() if u.role=="player" else []
    linked=User.query.filter_by(parent_id=u.id,role="player").all() if u.role=="parent" else []
    memberships=TeamMembership.query.filter_by(user_id=u.id,approved=True).all()
    teams=[db.session.get(Team,m.team_id) for m in memberships]
    return render_template("dashboard.html",user=u,totals=player_totals(u.id) if u.role=="player" else None,completions=comps,linked=linked,teams=teams)

@app.route("/workouts")
@login_required
def workouts():
    u=current_user(); cat=request.args.get("category","All"); q=Workout.query.filter_by(active=True)
    if cat!="All": q=q.filter_by(category=cat)
    done={x.workout_id for x in WorkoutCompletion.query.filter_by(player_id=u.id,completed_on=date.today()).all()} if u.role=="player" else set()
    return render_template("workouts.html",user=u,workouts=q.all(),category=cat,completed_ids=done)

@app.route("/workout/<int:wid>/complete",methods=["POST"])
@role_required("player")
def complete_workout(wid):
    u=current_user()
    if not u.email_verified or not u.consent_verified: flash("Verify email and parent consent before recording progress."); return redirect(url_for("dashboard"))
    w=db.session.get(Workout,wid)
    if w and not WorkoutCompletion.query.filter_by(player_id=u.id,workout_id=wid,completed_on=date.today()).first():
        db.session.add(WorkoutCompletion(player_id=u.id,workout_id=wid,minutes=w.minutes,notes=request.form.get("notes","")[:1000])); db.session.commit(); audit("workout_completed",f"workout_id={wid}")
    return redirect(url_for("workouts"))

@app.route("/stats",methods=["GET","POST"])
@role_required("player")
def stats():
    u=current_user()
    if request.method=="POST":
        if not u.email_verified or not u.consent_verified: flash("Verify email and parent consent first."); return redirect(url_for("dashboard"))
        def i(k): return int(float(request.form.get(k,0) or 0))
        row=GameStat(player_id=u.id,played_on=datetime.strptime(request.form["played_on"],"%Y-%m-%d").date(),opponent=request.form.get("opponent","")[:120],ab=i("ab"),hits=i("hits"),walks=i("walks"),runs=i("runs"),rbi=i("rbi"),sb=i("sb"),ip=float(request.form.get("ip",0) or 0),pso=i("pso"),er=i("er"),pitches=i("pitches"))
        db.session.add(row); db.session.commit(); audit("game_stat_added",f"stat_id={row.id}"); flash("Game saved."); return redirect(url_for("stats"))
    rows=GameStat.query.filter_by(player_id=u.id).order_by(GameStat.played_on.desc()).all()
    return render_template("stats.html",user=u,rows=rows,totals=player_totals(u.id),today=date.today().isoformat())

@app.route("/player/<int:pid>")
@login_required
def player_detail(pid):
    viewer=current_user(); p=db.session.get(User,pid)
    if not p or p.role!="player" or not can_view_player(viewer,p): return redirect(url_for("dashboard"))
    cs=WorkoutCompletion.query.filter_by(player_id=p.id).order_by(WorkoutCompletion.completed_on.desc()).all(); gs=GameStat.query.filter_by(player_id=p.id).order_by(GameStat.played_on.desc()).all()
    return render_template("player_detail.html",user=viewer,player=p,completions=cs,rows=gs,totals=player_totals(p.id))

# ---------------- Teams & coach ----------------
@app.route("/teams",methods=["GET","POST"])
@role_required("coach")
def teams():
    u=current_user()
    if request.method=="POST":
        org=Organization.query.filter_by(owner_id=u.id).first()
        if not org: org=Organization(name=request.form.get("organization","Misfits Baseball")[:120],owner_id=u.id); db.session.add(org); db.session.flush()
        team=Team(organization_id=org.id,name=request.form["name"][:120],age_group=request.form.get("age_group","")[:20],join_code=secrets.token_hex(3).upper()); db.session.add(team); db.session.flush(); db.session.add(TeamMembership(team_id=team.id,user_id=u.id,role="coach")); db.session.commit(); audit("team_created",f"team_id={team.id}"); return redirect(url_for("teams"))
    tids=team_ids_for(u); rows=Team.query.filter(Team.id.in_(tids)).all() if tids else []
    return render_template("teams.html",user=u,teams=rows)

@app.route("/join-team",methods=["POST"])
@role_required("player")
def join_team():
    u=current_user(); team=Team.query.filter_by(join_code=request.form["join_code"].strip().upper()).first()
    if not team: flash("Team code not found."); return redirect(url_for("dashboard"))
    if not TeamMembership.query.filter_by(team_id=team.id,user_id=u.id).first(): db.session.add(TeamMembership(team_id=team.id,user_id=u.id,role="player",approved=u.consent_verified)); db.session.commit(); audit("team_joined",f"team_id={team.id}")
    flash("Team joined." if u.consent_verified else "Team request saved; parent consent is still required."); return redirect(url_for("dashboard"))

@app.route("/coach")
@role_required("coach")
def coach():
    u=current_user(); tids=team_ids_for(u); mids=TeamMembership.query.filter(TeamMembership.team_id.in_(tids),TeamMembership.role=="player",TeamMembership.approved==True).all() if tids else []
    players=[db.session.get(User,m.user_id) for m in mids]; seen=set(); data=[]
    for p in players:
        if not p or p.id in seen: continue
        seen.add(p.id); cs=WorkoutCompletion.query.filter_by(player_id=p.id).all(); data.append((p,len(cs),sum(x.minutes for x in cs),player_totals(p.id)))
    return render_template("coach.html",user=u,data=data)

@app.route("/gamechanger",methods=["GET","POST"])
@role_required("coach")
@limiter.limit("20 per hour")
def gamechanger():
    u=current_user(); result=None
    if request.method=="POST":
        f=request.files.get("csv_file")
        if not f or not f.filename.lower().endswith(".csv"): flash("Choose a CSV file."); return redirect(url_for("gamechanger"))
        reader=csv.DictReader(io.StringIO(f.read().decode("utf-8-sig",errors="ignore"))); imported=0; unmatched=[]
        def pick(row,*names):
            m={k.lower().replace(" ","").replace("_",""):v for k,v in row.items()}
            for n in names:
                key=n.lower().replace(" ","").replace("_","")
                if key in m and str(m[key]).strip()!="": return m[key]
            return ""
        def n(v):
            try:return float(str(v).replace("%","").replace(",",""))
            except:return 0
        tids=team_ids_for(u); mids=TeamMembership.query.filter(TeamMembership.team_id.in_(tids),TeamMembership.role=="player").all() if tids else []
        players=[db.session.get(User,m.user_id) for m in mids]
        key=lambda s:"".join(ch for ch in s.lower() if ch.isalnum())
        for r in reader:
            name=pick(r,"Player","Player Name","Name","Athlete") or (str(pick(r,"First Name","First"))+" "+str(pick(r,"Last Name","Last"))).strip()
            p=next((x for x in players if x and key(x.name)==key(name)),None)
            if not p: unmatched.append(name); continue
            db.session.add(GameStat(player_id=p.id,played_on=date.today(),opponent="GameChanger season import",ab=int(n(pick(r,"AB","At Bats"))),hits=int(n(pick(r,"H","Hits"))),walks=int(n(pick(r,"BB","Walks"))),runs=int(n(pick(r,"R","Runs"))),rbi=int(n(pick(r,"RBI"))),sb=int(n(pick(r,"SB","Stolen Bases"))),ip=n(pick(r,"IP","Innings Pitched")),pso=int(n(pick(r,"SO","K","Strikeouts"))),er=int(n(pick(r,"ER","Earned Runs"))),pitches=int(n(pick(r,"Pitches","Pitch Count","PC"))),source="gamechanger")); imported+=1
        db.session.commit(); audit("gamechanger_import",f"rows={imported}; unmatched={len(unmatched)}"); result={"imported":imported,"unmatched":unmatched}
    return render_template("gamechanger.html",user=u,result=result)

# ---------------- Parent ----------------
@app.route("/my-players")
@role_required("parent")
def my_players():
    u=current_user(); linked=User.query.filter_by(parent_id=u.id,role="player").all(); return render_template("my_players.html",user=u,linked=linked)

# ---------------- Private media/video storage ----------------
@app.route("/media",methods=["GET"])
@login_required
def media_library():
    u=current_user(); q=Media.query.order_by(Media.created_at.desc()).all(); rows=[m for m in q if can_view_media(u,m)]
    players=[]
    if u.role=="coach":
        tids=team_ids_for(u); mids=TeamMembership.query.filter(TeamMembership.team_id.in_(tids),TeamMembership.role=="player").all() if tids else []
        players=[db.session.get(User,m.user_id) for m in mids if db.session.get(User,m.user_id)]
    return render_template("media.html",user=u,rows=rows,players=players,storage_ready=bool(media_bucket()))

@app.route("/media/upload",methods=["POST"])
@role_required("coach","parent")
@limiter.limit("20 per day")
def media_upload():
    if not media_bucket(): flash("Private video storage is not configured yet."); return redirect(url_for("media_library"))
    f=request.files.get("media_file")
    allowed={"video/mp4","video/quicktime","video/webm"}
    if not f or f.mimetype not in allowed: flash("Upload MP4, MOV, or WebM video only."); return redirect(url_for("media_library"))
    player_id=int(request.form.get("player_id",0) or 0) or None
    if player_id:
        player=db.session.get(User,player_id)
        if not player or not can_view_player(current_user(),player): flash("You are not authorized for that player."); return redirect(url_for("media_library"))
    ext={"video/mp4":"mp4","video/quicktime":"mov","video/webm":"webm"}[f.mimetype]
    key=f"private/{current_user().id}/{uuid.uuid4().hex}.{ext}"
    s3_client().upload_fileobj(f,media_bucket(),key,ExtraArgs={"ContentType":f.mimetype})
    m=Media(owner_user_id=current_user().id,player_id=player_id,object_key=key,original_name=(f.filename or "video")[:255],content_type=f.mimetype); db.session.add(m); db.session.commit(); audit("media_uploaded",f"media_id={m.id}"); flash("Private video uploaded."); return redirect(url_for("media_library"))

@app.route("/media/<int:mid>")
@login_required
def media_view(mid):
    m=db.session.get(Media,mid)
    if not m or not can_view_media(current_user(),m): return redirect(url_for("media_library"))
    url=s3_client().generate_presigned_url("get_object",Params={"Bucket":media_bucket(),"Key":m.object_key},ExpiresIn=900)
    audit("media_viewed",f"media_id={m.id}"); return redirect(url)

# ---------------- Privacy & account controls ----------------
@app.route("/account")
@login_required
def account(): return render_template("account.html",user=current_user())

@app.route("/account/export")
@login_required
@limiter.limit("5 per day")
def export_data():
    u=current_user(); payload={"user":{"name":u.name,"email":u.email,"role":u.role,"age_group":u.age_group,"position":u.position,"created_at":u.created_at.isoformat()},"workouts":[],"stats":[],"teams":[]}
    if u.role=="player":
        payload["workouts"]=[{"date":c.completed_on.isoformat(),"workout_id":c.workout_id,"minutes":c.minutes,"notes":c.notes} for c in WorkoutCompletion.query.filter_by(player_id=u.id).all()]
        payload["stats"]=[{"date":g.played_on.isoformat(),"opponent":g.opponent,"ab":g.ab,"hits":g.hits,"walks":g.walks,"runs":g.runs,"rbi":g.rbi,"sb":g.sb,"ip":g.ip,"pso":g.pso,"er":g.er,"pitches":g.pitches,"source":g.source} for g in GameStat.query.filter_by(player_id=u.id).all()]
    payload["teams"]=[{"team_id":m.team_id,"role":m.role} for m in TeamMembership.query.filter_by(user_id=u.id).all()]
    bio=io.BytesIO(json.dumps(payload,indent=2).encode()); audit("data_exported"); return send_file(bio,mimetype="application/json",as_attachment=True,download_name="misfits-account-data.json")

@app.route("/account/delete",methods=["POST"])
@login_required
@limiter.limit("3 per day")
def delete_account():
    u=current_user()
    if not check_password_hash(u.password_hash,request.form.get("password","")): flash("Password did not match."); return redirect(url_for("account"))
    if u.role=="parent": User.query.filter_by(parent_id=u.id).update({"parent_id":None})
    WorkoutCompletion.query.filter_by(player_id=u.id).delete(); GameStat.query.filter_by(player_id=u.id).delete(); TeamMembership.query.filter_by(user_id=u.id).delete(); ConsentRequest.query.filter_by(player_id=u.id).delete(); Media.query.filter((Media.owner_user_id==u.id)|(Media.player_id==u.id)).delete(synchronize_session=False)
    u.email=f"deleted-{u.id}-{secrets.token_hex(4)}@invalid.local"; u.name="Deleted User"; u.password_hash=generate_password_hash(secrets.token_urlsafe(40)); u.is_active=False
    db.session.commit(); audit("account_deleted",who=u); session.clear(); flash("Account deleted."); return redirect(url_for("login"))

@app.route("/privacy")
def privacy(): return render_template("privacy.html",user=current_user())
@app.route("/terms")
def terms(): return render_template("terms.html",user=current_user())
@app.route("/health")
def health(): return jsonify({"ok":True,"time":datetime.utcnow().isoformat()+"Z"})

if __name__=="__main__":
    with app.app_context(): db.create_all()
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
