from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, g
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from datetime import datetime, date
from functools import wraps
import calendar as calendar_module
import csv, io, os, json, secrets, smtplib, uuid, time
import boto3
import requests
from email.message import EmailMessage
from urllib.parse import parse_qs, urlparse
from base64 import urlsafe_b64encode
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from learning_content import FIELD_IQ_LEVELS, LEVELS, PITCH_PLAN_LEVELS
from practice_drills import PRACTICE_DRILLS

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower()=="true",
    MAX_CONTENT_LENGTH=200 * 1024 * 1024,
    SEND_FILE_MAX_AGE_DEFAULT=3600,
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

SHOP_URL="https://thetshirtguy.co"
SHOP_COLLECTION_URL=f"{SHOP_URL}/collections/misfits-baseball"
SHOP_PRODUCTS_URL=f"{SHOP_COLLECTION_URL}/products.json?limit=50"
SHOP_CACHE={"expires_at":0,"products":[]}

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

class ScheduleEvent(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    title=db.Column(db.String(160), nullable=False)
    event_type=db.Column(db.String(30), nullable=False)
    starts_on=db.Column(db.Date, nullable=False, index=True)
    start_time=db.Column(db.Time, nullable=False)
    end_time=db.Column(db.Time, nullable=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True, index=True)
    created_by_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow)

class ChatMessage(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False, index=True)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body=db.Column(db.String(1000), nullable=False)
    kind=db.Column(db.String(20), nullable=False, default="text")
    is_deleted=db.Column(db.Boolean, nullable=False, default=False)
    deleted_at=db.Column(db.DateTime, nullable=True)
    pinned_at=db.Column(db.DateTime, nullable=True, index=True)
    pinned_by_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

class ChatPollOption(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    message_id=db.Column(db.Integer, db.ForeignKey("chat_message.id"), nullable=False, index=True)
    text=db.Column(db.String(200), nullable=False)
    position=db.Column(db.Integer, nullable=False, default=0)

class ChatPollVote(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    message_id=db.Column(db.Integer, db.ForeignKey("chat_message.id"), nullable=False, index=True)
    option_id=db.Column(db.Integer, db.ForeignKey("chat_poll_option.id"), nullable=False, index=True)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__=(db.UniqueConstraint("message_id","user_id",name="uq_poll_user_vote"),)

class ChatReadState(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False, index=True)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    last_read_message_id=db.Column(db.Integer, default=0, nullable=False)
    updated_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__=(db.UniqueConstraint("team_id","user_id",name="uq_chat_read_state"),)

class PortalNotification(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True, index=True)
    kind=db.Column(db.String(30), nullable=False)
    title=db.Column(db.String(160), nullable=False)
    body=db.Column(db.String(500), nullable=False)
    target_url=db.Column(db.String(500), nullable=False)
    read_at=db.Column(db.DateTime, nullable=True, index=True)
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

class PushSubscription(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    endpoint=db.Column(db.String(1000), nullable=False, unique=True)
    subscription_json=db.Column(db.Text, nullable=False)
    created_at=db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class AppSetting(db.Model):
    key=db.Column(db.String(100), primary_key=True)
    value=db.Column(db.Text, nullable=False)


class Media(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    owner_user_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    player_id=db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    team_id=db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    workout_id=db.Column(db.Integer, db.ForeignKey("workout.id"), nullable=True, index=True)
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

EVENT_TYPES={
    "game":"Game",
    "tournament":"Tournament",
    "practice":"Practice",
    "power-alley":"Power Alley Practice",
}
TEAM_COLORS=("#38bdf8","#f97316","#a78bfa","#22c55e","#f43f5e","#eab308","#14b8a6","#ec4899")

SEED_EVENTS=[
    ("Power Alley Practice","power-alley","2026-09-28","17:00","19:00"),
    ("Game","game","2026-09-29","17:45",None),
    ("Practice","practice","2026-10-02","17:00","19:00"),
]

FIELD_IQ_QUESTIONS = [
    {"situation":"Halfway depth","outs":0,"runners":"3rd","runner_bases":["third"],"question":"Why might an infield play halfway with a runner on third and fewer than two outs?","answers":["It is the same as standing on the outfield grass.","It guarantees the runner cannot score on any ground ball.","It removes the need for the catcher to communicate.","It balances a possible play at home with better range and a more reliable out at first."],"correct":3,"explanation":"Halfway depth keeps a possible throw home available without giving up as much range as playing all the way in."},
    {"situation":"Double-play depth","outs":0,"runners":"1st","runner_bases":["first"],"question":"With a runner on first and fewer than two outs, why do the middle infielders move to double-play depth?","answers":["To be closer to second base for a faster turn.","To guard both foul lines.","To back up the catcher.","To make an outfield relay shorter."],"correct":0,"explanation":"Shortstop and second baseman shade toward second so they can receive the ball and turn two quickly."},
    {"situation":"Corners in","outs":0,"runners":"1st","runner_bases":["first"],"question":"The batter squares to bunt. What is the first priority for the corner infielders?","answers":["Retreat to the outfield grass.","Charge under control and field the bunt.","Both cover second base.","Wait for the catcher to field every bunt."],"correct":1,"explanation":"The first and third basemen charge under control while teammates rotate to cover the bases."},
    {"situation":"Cutoff to third","outs":1,"runners":"1st","runner_bases":["first"],"question":"A single is hit to right field and the runner tries for third. Who is usually the cutoff?","answers":["Shortstop","First baseman","Catcher","Pitcher"],"correct":1,"explanation":"On a throw from right field toward third, the first baseman commonly lines up as the cutoff while the pitcher backs up third."},
    {"situation":"Cutoff to home","outs":1,"runners":"2nd","runner_bases":["second"],"question":"A base hit goes to left field and the runner from second heads home. Who usually lines up the throw?","answers":["First baseman","Second baseman","Third baseman","Shortstop"],"correct":2,"explanation":"The third baseman is commonly the cutoff on a throw from left field to home while other defenders cover and back up."},
    {"situation":"Passed ball","outs":2,"runners":"3rd","runner_bases":["third"],"question":"A pitch gets past the catcher with a runner on third. Where should the pitcher go?","answers":["Cover home plate","Cover second base","Run to the dugout","Stay on the mound"],"correct":0,"explanation":"The catcher retrieves the ball and the pitcher covers home for a return throw and possible tag."},
    {"situation":"Protect the line","outs":2,"runners":"1st & 2nd","runner_bases":["first","second"],"question":"Late in a close game, why might the corner infielders guard the lines?","answers":["To prevent an extra-base hit down the line.","To start a routine double play.","To make the pitcher throw harder.","To stop a stolen base at second."],"correct":0,"explanation":"Guarding the lines trades some range toward the middle for protection against a damaging extra-base hit."},
    {"situation":"Backup responsibility","outs":1,"runners":"2nd","runner_bases":["second"],"question":"A throw from center field is headed to home plate. What should the first baseman do?","answers":["Stand on first base.","Join the outfielders.","Trail and back up the throw near home.","Cover third base."],"correct":2,"explanation":"Every throw needs a backup. The first baseman should get behind the play to contain an overthrow."},
    {"situation":"Wheel play","outs":0,"runners":"1st & 2nd","runner_bases":["first","second"],"question":"On a wheel play against a bunt, which infielder breaks to cover third?","answers":["Shortstop","Second baseman","First baseman","Pitcher"],"correct":0,"explanation":"The corners charge, the shortstop rotates to third, and the second baseman covers first or second according to the team call."},
    {"situation":"Pitch plan","outs":2,"runners":"2nd & 3rd","runner_bases":["second","third"],"question":"What should shape a pitcher and catcher's plan before the next pitch?","answers":["Only the loudest fan.","The count, hitter, game situation, earlier at-bats, and pitches available today.","The color of the hitter's bat.","Always throwing the same pitch."],"correct":1,"explanation":"Good pitch calling combines the count and game situation with hitter information and the pitches the pitcher can command that day."}
]

PITCH_PLAN_QUESTIONS = [
    {"title":"First-pitch strike","category":"PITCHING & PITCH SELECTION","level":"FOUNDATION","count":"0-0","hitter":"RHH","pitch":"FB","zone_x":50,"zone_y":58,"question":"What is the best goal on the first pitch to a hitter you do not know yet?","answers":["Throw the hardest pitch possible.","Attack a controlled part of the strike zone with a pitch you command.","Waste a pitch above the zone.","Always start with a breaking ball."],"correct":1,"explanation":"A quality 0-0 strike puts the pitcher ahead without requiring a perfect pitch on the edge."},
    {"title":"Protect the plate","category":"COUNT AWARENESS","level":"FOUNDATION","count":"0-2","hitter":"LHH","pitch":"SL","zone_x":77,"zone_y":74,"question":"With an 0-2 count, what makes a good chase pitch?","answers":["It starts near the strike zone and finishes just outside it.","It bounces halfway to the plate.","It must be a fastball down the middle.","It should be as far outside as possible."],"correct":0,"explanation":"The hitter is protecting. A pitch that looks competitive early and finishes just off the plate can earn a chase."},
    {"title":"Fastball-Changeup Tunnel","category":"PITCHING & PITCH SELECTION","level":"ADVANCED","count":"1-1","hitter":"RHH","pitch":"CH","zone_x":67,"zone_y":66,"question":"What makes a fastball and changeup sequence difficult to recognize?","answers":["The arm speed and early flight look alike, while the changeup arrives slower and often finishes lower or with more fade.","The pitcher visibly slows the arm so the hitter knows the changeup is coming.","The changeup starts in a completely different direction from release.","The fastball is thrown without a target."],"correct":0,"explanation":"Matching arm speed and early flight makes the two pitches look alike until the changeup loses speed and moves late."},
    {"title":"Hitter's count","category":"COUNT AWARENESS","level":"INTERMEDIATE","count":"2-0","hitter":"RHH","pitch":"FB","zone_x":50,"zone_y":50,"question":"On 2-0, where should the pitcher focus the fastball?","answers":["A spot the pitcher can command, avoiding the very middle.","Two feet outside.","Directly at the hitter.","Anywhere, because the count does not matter."],"correct":0,"explanation":"The pitcher needs a strike, but should still choose a controlled lane instead of giving the hitter a middle-middle pitch."},
    {"title":"Put-away plan","category":"PITCHING & PITCH SELECTION","level":"INTERMEDIATE","count":"2-2","hitter":"LHH","pitch":"CB","zone_x":34,"zone_y":78,"question":"What should guide a two-strike put-away pitch?","answers":["The pitcher's best controlled weapon and how the hitter has reacted earlier.","Only what the crowd asks for.","Throwing the same location every time.","Avoiding every pitch near the plate."],"correct":0,"explanation":"A good put-away plan matches a pitch the pitcher controls with the hitter's swing, timing, and earlier reactions."},
    {"title":"Green-light strike","category":"COUNT AWARENESS","level":"FOUNDATION","count":"3-0","hitter":"RHH","pitch":"FB","zone_x":50,"zone_y":50,"question":"What is the pitcher's priority on 3-0?","answers":["Throw a quality strike with the most reliable pitch.","Try a brand-new pitch.","Aim at a corner the pitcher rarely hits.","Rush the delivery."],"correct":0,"explanation":"On 3-0, repeat the delivery and use the pitch with the best chance of producing a controlled strike."},
    {"title":"Stay out of the heart","category":"PITCHING & PITCH SELECTION","level":"INTERMEDIATE","count":"3-1","hitter":"LHH","pitch":"FB","zone_x":32,"zone_y":47,"question":"Why is a planned lane important in a 3-1 count?","answers":["The hitter is likely ready to swing, so the pitcher needs a confident strike away from the heart of the plate.","The hitter cannot swing on 3-1.","Every 3-1 pitch must be outside.","Location no longer matters."],"correct":0,"explanation":"The pitcher still needs a strike, but a clear lane helps prevent a predictable middle-middle mistake."},
    {"title":"Runner on third","category":"GAME SITUATION","level":"INTERMEDIATE","count":"1-0","hitter":"RHH","pitch":"FB","zone_x":50,"zone_y":72,"question":"With a runner on third and fewer than two outs, why can a low pitch be useful?","answers":["It can produce a ground ball and keeps the ball away from an easy sacrifice-fly lane.","It guarantees a strikeout.","It makes the runner return to second.","It removes the need to back up home."],"correct":0,"explanation":"A low pitch can encourage contact on the ground, though the defense must still be ready for every result."},
    {"title":"Double-play ball","category":"GAME SITUATION","level":"INTERMEDIATE","count":"0-1","hitter":"LHH","pitch":"SNK","zone_x":44,"zone_y":74,"question":"With a runner on first and fewer than two outs, what result may a sinker low in the zone encourage?","answers":["A ground ball that gives the defense a chance to turn two.","An automatic home run.","A foul ball into the dugout every time.","A stolen base."],"correct":0,"explanation":"A well-located sinker can produce ground contact and a possible double-play opportunity."},
    {"title":"Trust today's pitches","category":"PITCHING & PITCH SELECTION","level":"ADVANCED","count":"1-2","hitter":"RHH","pitch":"CH","zone_x":69,"zone_y":76,"question":"What is the smartest two-strike call when the pitcher's changeup has been well controlled today?","answers":["Use it with fastball arm speed and a target just below or off the zone.","Slow the arm down so it moves more.","Throw it down the middle every time.","Ignore what has worked today."],"correct":0,"explanation":"The best plan uses a pitch the pitcher can execute today, with convincing arm speed and a competitive target."}
]

# ---------------- Helpers ----------------
def current_user():
    if hasattr(g,"current_user"):
        return g.current_user
    uid=session.get("user_id")
    u=db.session.get(User, uid) if uid else None
    g.current_user=u if u and u.is_active else None
    return g.current_user

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
            if u.role != "admin" and u.role not in roles: return redirect(url_for("dashboard"))
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
        import urllib.error
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
                "Content-Type": "application/json",
        "User-Agent": "misfits-player-development/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = "<unable to read response body>"
        app.logger.error("Resend HTTP error %s: %s", e.code, error_body)
        return False
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
    if sent:
        flash(f"Verification email submitted to {u.email}. Check your inbox and spam folder. The link expires in 24 hours.")
    else:
        flash("We could not send the verification email. Please try again later or contact your team admin.")
    return sent

def s3_client():
    endpoint=os.environ.get("S3_ENDPOINT_URL") or None
    return boto3.client("s3",endpoint_url=endpoint,aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),region_name=os.environ.get("S3_REGION","us-east-1"))

def media_bucket(): return os.environ.get("S3_BUCKET","")

def youtube_embed_url(value):
    if not value: return ""
    try: parsed=urlparse(value)
    except ValueError: return ""
    host=(parsed.hostname or "").lower(); video_id=""
    if host in {"youtu.be","www.youtu.be"}:
        video_id=parsed.path.strip("/").split("/",1)[0]
    elif host in {"youtube.com","www.youtube.com","m.youtube.com"}:
        if parsed.path=="/watch": video_id=parse_qs(parsed.query).get("v",[""])[0]
        elif parsed.path.startswith(("/shorts/","/embed/")): video_id=parsed.path.split("/")[2]
    if video_id and all(character.isalnum() or character in "-_" for character in video_id):
        return f"https://www.youtube-nocookie.com/embed/{video_id}"
    return ""

def can_view_media(viewer, media):
    if media.workout_id: return True
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
    games=sum(
        int(g.source.split(":",1)[1])
        if g.source and g.source.startswith("gamechanger:") and g.source.split(":",1)[1].isdigit()
        else 1
        for g in rows
    )
    return {"games":games,"ab":ab,"hits":h,"walks":bb,"runs":s("runs"),"rbi":s("rbi"),"sb":s("sb"),"ip":round(s("ip"),1),"pso":s("pso"),"er":s("er"),"pitches":s("pitches"),"avg":h/ab if ab else 0,"obp":(h+bb)/(ab+bb) if ab+bb else 0}

def team_ids_for(u):
    if u.role=="admin":
        return [team.id for team in Team.query.all()]
    return [m.team_id for m in TeamMembership.query.filter_by(user_id=u.id,approved=True).all()]

def portal_team_ids_for(u):
    if u.role=="admin": return [team.id for team in Team.query.order_by(Team.id).all()]
    team_ids=set(team_ids_for(u))
    if u.role in {"parent", "coach"}:
        if u.role=="parent":
            child_ids=[child.id for child in User.query.filter_by(parent_id=u.id,role="player").all()]
            if child_ids:
                memberships=TeamMembership.query.filter(TeamMembership.user_id.in_(child_ids),TeamMembership.approved==True).all()
                team_ids.update(membership.team_id for membership in memberships)
        roster_team_ids=db.session.query(RosterProfile.team_id).join(RosterFamilyLink,RosterFamilyLink.profile_id==RosterProfile.id).filter(RosterFamilyLink.parent_id==u.id).distinct().all()
        team_ids.update(team_id for (team_id,) in roster_team_ids)
    return sorted(team_ids)

def visible_teams_for(u):
    team_ids=portal_team_ids_for(u)
    return Team.query.filter(Team.id.in_(team_ids)).order_by(Team.age_group,Team.name).all() if team_ids else []

def team_color_map(teams):
    return {team.id:TEAM_COLORS[index % len(TEAM_COLORS)] for index,team in enumerate(sorted(teams,key=lambda row:row.id))}

def can_access_team(u, team_id):
    return u.role=="admin" or team_id in portal_team_ids_for(u)

def shop_category(title):
    value=title.casefold()
    if "uniform" in value or "jersey" in value: return "Uniforms"
    if "hoodie" in value: return "Hoodies"
    if "hat" in value or "cap" in value: return "Hats"
    if "pant" in value: return "Pants"
    return "Fanwear"

def shop_products():
    now=time.time()
    if SHOP_CACHE["products"] and SHOP_CACHE["expires_at"]>now: return SHOP_CACHE["products"],None
    try:
        response=requests.get(SHOP_PRODUCTS_URL,timeout=8,headers={"User-Agent":"MisfitsPlayerPortal/1.0"})
        response.raise_for_status()
        products=[]
        for row in response.json().get("products",[]):
            handle=str(row.get("handle","")).strip()
            title=str(row.get("title","")).strip()
            variants=row.get("variants") or []
            prices=[float(variant["price"]) for variant in variants if variant.get("price")]
            images=row.get("images") or []
            if not handle or not title or not prices: continue
            low,high=min(prices),max(prices)
            products.append({
                "title":title,
                "url":f"{SHOP_URL}/products/{handle}",
                "image":str(images[0].get("src","")).strip() if images else "",
                "price":f"${low:,.2f}" if low==high else f"${low:,.2f} - ${high:,.2f}",
                "available":any(bool(variant.get("available")) for variant in variants),
                "category":shop_category(title),
            })
        SHOP_CACHE.update(products=products,expires_at=now+600)
        return products,None
    except Exception:
        app.logger.exception("Shopify team store feed could not be loaded")
        return SHOP_CACHE["products"],"Live product details are temporarily unavailable."

def team_notification_users(team_id,exclude_user_id=None):
    users=User.query.filter_by(is_active=True).all()
    return [user for user in users if user.id!=exclude_user_id and can_access_team(user,team_id)]

def vapid_keys():
    private_pem=os.environ.get("VAPID_PRIVATE_KEY","").replace("\\n","\n").strip()
    public_key=os.environ.get("VAPID_PUBLIC_KEY","").strip()
    if private_pem and public_key: return private_pem,public_key
    private_setting=db.session.get(AppSetting,"vapid_private_key")
    public_setting=db.session.get(AppSetting,"vapid_public_key")
    if private_setting and public_setting: return private_setting.value,public_setting.value
    private_key=ec.generate_private_key(ec.SECP256R1())
    private_pem=private_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    public_bytes=private_key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    public_key=urlsafe_b64encode(public_bytes).rstrip(b"=").decode()
    db.session.merge(AppSetting(key="vapid_private_key",value=private_pem))
    db.session.merge(AppSetting(key="vapid_public_key",value=public_key))
    db.session.commit()
    return private_pem,public_key

def send_push(user_id,payload):
    subscriptions=PushSubscription.query.filter_by(user_id=user_id).all()
    if not subscriptions: return
    private_key,_=vapid_keys()
    subject=os.environ.get("VAPID_SUBJECT") or f"mailto:{os.environ.get('ADMIN_EMAIL','admin@misfits.local')}"
    for subscription in subscriptions:
        try:
            webpush(subscription_info=json.loads(subscription.subscription_json),data=json.dumps(payload),vapid_private_key=private_key,vapid_claims={"sub":subject},ttl=86400)
        except WebPushException as exc:
            status=getattr(getattr(exc,"response",None),"status_code",None)
            if status in {404,410}: db.session.delete(subscription)
            else: app.logger.warning("Push delivery failed for user %s: %s",user_id,exc)
        except Exception:
            app.logger.exception("Push delivery failed for user %s",user_id)
    db.session.commit()

def notify_team(team_id,kind,title,body,target_url,exclude_user_id=None):
    recipients=team_notification_users(team_id,exclude_user_id)
    notifications=[]
    for recipient in recipients:
        notification=PortalNotification(user_id=recipient.id,team_id=team_id,kind=kind,title=title[:160],body=body[:500],target_url=target_url[:500])
        db.session.add(notification); notifications.append((recipient,notification))
    db.session.commit()
    for recipient,notification in notifications:
        send_push(recipient.id,{"title":notification.title,"body":notification.body,"url":notification.target_url,"tag":f"{kind}-{team_id}"})

def default_calendar_team():
    return Team.query.filter(Team.name.ilike("Misfits%")).order_by(Team.id).first() or Team.query.order_by(Team.id).first()

def can_view_player(viewer, player):
    if viewer.role=="admin": return True
    if viewer.id==player.id: return True
    if viewer.role=="parent" and player.parent_id==viewer.id: return True
    if viewer.role=="coach":
        return bool(set(team_ids_for(viewer)) & set(team_ids_for(player)))
    return False

def initialize_database():
    db.create_all()
    columns={column["name"] for column in inspect(db.engine).get_columns("schedule_event")}
    if "team_id" not in columns:
        db.session.execute(text("ALTER TABLE schedule_event ADD COLUMN team_id INTEGER REFERENCES team(id)"))
        if db.engine.dialect.name=="postgresql":
            db.session.execute(text("ALTER TABLE schedule_event DROP CONSTRAINT IF EXISTS uq_schedule_event"))
        db.session.commit()
    media_columns={column["name"] for column in inspect(db.engine).get_columns("media")}
    if "workout_id" not in media_columns:
        db.session.execute(text("ALTER TABLE media ADD COLUMN workout_id INTEGER REFERENCES workout(id)"))
        db.session.commit()
    chat_columns={column["name"] for column in inspect(db.engine).get_columns("chat_message")}
    chat_migrations={
        "kind":"ALTER TABLE chat_message ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'text'",
        "is_deleted":"ALTER TABLE chat_message ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
        "deleted_at":"ALTER TABLE chat_message ADD COLUMN deleted_at TIMESTAMP",
        "pinned_at":"ALTER TABLE chat_message ADD COLUMN pinned_at TIMESTAMP",
        "pinned_by_id":'ALTER TABLE chat_message ADD COLUMN pinned_by_id INTEGER REFERENCES "user"(id)',
    }
    for column,statement in chat_migrations.items():
        if column not in chat_columns: db.session.execute(text(statement))
    if any(column not in chat_columns for column in chat_migrations): db.session.commit()
    admin_email=os.environ.get("ADMIN_EMAIL","").strip().lower()
    if admin_email:
        designated_admin=User.query.filter_by(email=admin_email).first()
        if designated_admin and designated_admin.role!="admin":
            designated_admin.role="admin"
            db.session.commit()
    seeded_workouts=WORKOUTS+[(category,title,15,"All ages",instructions) for category,title,instructions in PRACTICE_DRILLS]
    existing_workouts={(category,title) for category,title in db.session.query(Workout.category,Workout.title).all()}
    added_workout=False
    for category,title,minutes,level,exercises in seeded_workouts:
        if (category,title) not in existing_workouts:
            db.session.add(Workout(category=category,title=title,minutes=minutes,level=level,exercises=exercises))
            existing_workouts.add((category,title))
            added_workout=True
    if added_workout: db.session.commit()
    seeded_event=False
    calendar_team=default_calendar_team()
    if calendar_team:
        seeded_event=bool(ScheduleEvent.query.filter_by(team_id=None).update({"team_id":calendar_team.id}))
    for title,event_type,day,start,end in SEED_EVENTS:
        starts_on=datetime.strptime(day,"%Y-%m-%d").date()
        start_time=datetime.strptime(start,"%H:%M").time()
        if calendar_team and not ScheduleEvent.query.filter_by(team_id=calendar_team.id,title=title,event_type=event_type,starts_on=starts_on,start_time=start_time).first():
            db.session.add(ScheduleEvent(team_id=calendar_team.id,title=title,event_type=event_type,starts_on=starts_on,start_time=start_time,end_time=datetime.strptime(end,"%H:%M").time() if end else None))
            seeded_event=True
    if seeded_event: db.session.commit()
    db.session.remove()

@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"]="nosniff"
    resp.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data: https://cdn.shopify.com; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'self' https://*.myshopify.com https://*.square.site"
    return resp

# ---------------- Auth ----------------
@app.route("/")
def home(): return redirect(url_for("dashboard")) if current_user() else redirect(url_for("login"))

def registration_player_choices():
    claimed_keys=set(db.session.query(RosterProfile.team_id,RosterProfile.name_key).join(RosterFamilyLink,RosterFamilyLink.profile_id==RosterProfile.id).distinct().all())
    rows=db.session.query(RosterProfile,Team).join(Team,Team.id==RosterProfile.team_id).filter(Team.age_group.in_(("9U","10U","11U","12U"))).order_by(Team.age_group,Team.name,RosterProfile.name_key,RosterProfile.as_of.desc(),RosterProfile.id.desc()).all()
    choices=[]; seen=set()
    for profile,team in rows:
        key=(profile.team_id,profile.name_key)
        if key not in seen and key not in claimed_keys:
            seen.add(key); choices.append((profile,team))
    return choices

@app.route("/register",methods=["GET","POST"])
@limiter.limit("10 per hour")
def register():
    player_choices=registration_player_choices()
    if request.method=="POST":
        role=request.form.get("role","parent")
        if role not in {"player","parent","coach"}: role="parent"
        email=request.form["email"].strip().lower(); password=request.form["password"]
        if len(password)<10: flash("Use a password with at least 10 characters."); return redirect(url_for("register"))
        if User.query.filter_by(email=email).first(): flash("That email already exists."); return redirect(url_for("register"))
        parent_email=""
        team=None
        selected_profile=None
        if role=="player":
            parent_email=request.form.get("parent_email","").strip().lower()
            join_code=request.form.get("join_code","").strip().upper()
            if not parent_email: flash("A parent/guardian email is required for youth player accounts."); return redirect(url_for("register"))
            team=Team.query.filter_by(join_code=join_code).first()
            if not team: flash("A valid team join code is required for player accounts."); return redirect(url_for("register"))
        elif role=="parent" and player_choices:
            try: profile_id=int(request.form.get("profile_id",0) or 0)
            except (TypeError,ValueError): profile_id=0
            selected={profile.id:(profile,choice_team) for profile,choice_team in player_choices}.get(profile_id)
            if not selected:
                flash("Choose your player from the team roster."); return redirect(url_for("register"))
            selected_profile,team=selected
            claimed=db.session.query(RosterFamilyLink.id).join(RosterProfile,RosterProfile.id==RosterFamilyLink.profile_id).filter(RosterProfile.team_id==selected_profile.team_id,RosterProfile.name_key==selected_profile.name_key).first()
            if claimed:
                flash("That player is already linked to a family account. Contact your team admin if this is your player."); return redirect(url_for("register"))
            join_code=request.form.get("join_code","").strip().upper()
            if not join_code or not secrets.compare_digest(join_code,team.join_code):
                flash("The team code does not match the selected player's team."); return redirect(url_for("register"))
        u=User(role=role,name=request.form["name"].strip(),email=email,password_hash=generate_password_hash(password),age_group=request.form.get("age_group","") if role=="player" else "",position=request.form.get("position","") if role=="player" else "",consent_verified=(role!="player"))
        db.session.add(u); db.session.flush()
        if role=="player":
            db.session.add(TeamMembership(team_id=team.id,user_id=u.id,role="player",approved=False))
            cr=ConsentRequest(player_id=u.id,parent_email=parent_email); db.session.add(cr)
        elif role=="parent":
            if selected_profile:
                db.session.add(RosterFamilyLink(parent_id=u.id,profile_id=selected_profile.id))
                for finance_account in FinanceAccount.query.filter_by(team_id=selected_profile.team_id, name_key=selected_profile.name_key).all():
                    if not finance_account.parent_id:
                        finance_account.parent_id = u.id
            approved_requests=ConsentRequest.query.filter_by(parent_email=email).filter(ConsentRequest.approved_at.isnot(None)).all()
            for approved_request in approved_requests:
                player=db.session.get(User,approved_request.player_id)
                if player and player.role=="player" and player.parent_id is None:
                    player.parent_id=u.id
        db.session.commit(); audit("account_created",f"role={role}",u)
        session.clear(); session["user_id"]=u.id
        send_verification(u)
        if role=="player":
            tok=token_for("consent",{"cid":cr.id,"nonce":cr.token_nonce})
            link=app_url(url_for("parent_consent",token=tok))
            sent=send_email(parent_email,"Parent consent for Misfits Player Development",f"Review and approve this player account: {link}\n\nAfter approval, create or sign in to a parent account using this email address to view the player.")
            if not sent: flash("We could not send the parent-consent email. Please contact your team admin.")
        return redirect(url_for("dashboard"))
    return render_template("register.html",player_choices=player_choices)

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
def resend_verification():
    if current_user().email_verified:
        flash("Your email is already verified.")
    else:
        send_verification(current_user())
    return redirect(url_for("dashboard"))

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
        TeamMembership.query.filter_by(user_id=player.id,role="player").update({"approved":True})
        db.session.commit(); audit("parent_consent_approved",f"player_id={player.id}",parent or player)
        message="The player account can now use development tracking features."
        if not parent: message+=" Create a parent account using this email address to view the linked player."
        return render_template("message.html",title="Consent approved",message=message)
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

@app.route("/calendar",methods=["GET","POST"])
@login_required
def team_calendar():
    u=current_user()
    teams=visible_teams_for(u)
    team_by_id={team.id:team for team in teams}
    selected_value=request.form.get("team_id") if request.method=="POST" else request.args.get("team_id")
    master=u.role=="admin" and (not selected_value or selected_value=="all")
    selected_team=None
    if not master:
        try: selected_team=team_by_id.get(int(selected_value)) if selected_value else (teams[0] if teams else None)
        except (TypeError,ValueError): selected_team=None
        if selected_value and not selected_team:
            flash("You do not have access to that team calendar.")
            return redirect(url_for("team_calendar"))
    if request.method=="POST":
        if u.role!="admin":
            flash("Only administrators can add calendar events.")
            return redirect(url_for("team_calendar"))
        try: event_team=db.session.get(Team,int(request.form.get("team_id",0)))
        except (TypeError,ValueError): event_team=None
        if not event_team:
            flash("Choose a team for this event.")
            return redirect(url_for("team_calendar"))
        event_type=request.form.get("event_type","")
        if event_type not in EVENT_TYPES:
            flash("Choose a valid event type.")
            return redirect(url_for("team_calendar"))
        try:
            starts_on=datetime.strptime(request.form.get("starts_on",""),"%Y-%m-%d").date()
            start_time=datetime.strptime(request.form.get("start_time",""),"%H:%M").time()
            end_value=request.form.get("end_time","")
            end_time=datetime.strptime(end_value,"%H:%M").time() if end_value else None
        except ValueError:
            flash("Enter a valid date and time.")
            return redirect(url_for("team_calendar"))
        if end_time and end_time<=start_time:
            flash("The end time must be after the start time.")
            return redirect(url_for("team_calendar"))
        title=request.form.get("title","").strip()[:160] or EVENT_TYPES[event_type]
        if ScheduleEvent.query.filter_by(team_id=event_team.id,title=title,event_type=event_type,starts_on=starts_on,start_time=start_time).first():
            flash("That event is already on the calendar.")
            return redirect(url_for("team_calendar",team_id=event_team.id,year=starts_on.year,month=starts_on.month))
        event=ScheduleEvent(team_id=event_team.id,title=title,event_type=event_type,starts_on=starts_on,start_time=start_time,end_time=end_time,created_by_id=u.id)
        db.session.add(event); db.session.commit(); audit("calendar_event_added",f"event_id={event.id}")
        event_time=start_time.strftime("%I:%M %p").lstrip("0")
        notify_team(event_team.id,"calendar",f"New {EVENT_TYPES[event_type]}: {title}",f"{event_team.name} - {starts_on.strftime('%b %d')} at {event_time}",url_for("team_calendar",team_id=event_team.id,year=starts_on.year,month=starts_on.month),exclude_user_id=u.id)
        flash("Calendar event added.")
        return redirect(url_for("team_calendar",team_id=event_team.id,year=starts_on.year,month=starts_on.month))

    today=date.today()
    try:
        year=int(request.args.get("year",today.year)); month=int(request.args.get("month",today.month))
        if year<2020 or year>2100 or month<1 or month>12: raise ValueError
    except (TypeError,ValueError):
        year,month=today.year,today.month
    weeks=calendar_module.Calendar(firstweekday=6).monthdatescalendar(year,month)
    query=ScheduleEvent.query.filter(ScheduleEvent.starts_on.between(weeks[0][0],weeks[-1][-1]))
    if not master:
        if not selected_team: query=query.filter(text("1=0"))
        else: query=query.filter_by(team_id=selected_team.id)
    rows=query.order_by(ScheduleEvent.starts_on,ScheduleEvent.start_time).all()
    events_by_day={}
    for event in rows: events_by_day.setdefault(event.starts_on,[]).append(event)
    previous=date(year-1,12,1) if month==1 else date(year,month-1,1)
    following=date(year+1,1,1) if month==12 else date(year,month+1,1)
    all_teams=Team.query.order_by(Team.id).all()
    return render_template("calendar.html",user=u,weeks=weeks,events_by_day=events_by_day,year=year,month=month,month_name=calendar_module.month_name[month],previous=previous,following=following,event_types=EVENT_TYPES,today=today,teams=teams,selected_team=selected_team,master=master,team_by_id=team_by_id,team_colors=team_color_map(all_teams))

@app.route("/chats")
@login_required
def team_chats():
    u=current_user(); teams=visible_teams_for(u)
    states={state.team_id:state.last_read_message_id for state in ChatReadState.query.filter_by(user_id=u.id).all()}
    unread={team.id:ChatMessage.query.filter(ChatMessage.team_id==team.id,ChatMessage.id>states.get(team.id,0),ChatMessage.is_deleted==False).count() for team in teams}
    notification_count=PortalNotification.query.filter_by(user_id=u.id,read_at=None).count()
    return render_template("team_chats.html",user=u,teams=teams,team_colors=team_color_map(Team.query.order_by(Team.id).all()),unread=unread,notification_count=notification_count)

@app.route("/teams/<int:team_id>/chat",methods=["GET","POST"])
@login_required
@limiter.limit("60 per minute")
def team_chat(team_id):
    u=current_user(); team=db.session.get(Team,team_id)
    if not team or not can_access_team(u,team_id):
        flash("You do not have access to that team chat.")
        return redirect(url_for("team_chats"))
    if request.method=="POST":
        body=request.form.get("body","").strip()[:1000]
        if not body:
            flash("Enter a message before sending.")
        else:
            message=ChatMessage(team_id=team.id,user_id=u.id,body=body)
            db.session.add(message); db.session.commit(); audit("team_chat_message",f"team_id={team.id},message_id={message.id}")
            notify_team(team.id,"chat",f"New message in {team.name}",f"{u.name}: {body}",url_for("team_chat",team_id=team.id),exclude_user_id=u.id)
        return redirect(url_for("team_chat",team_id=team.id))
    before=request.args.get("before",type=int)
    query=ChatMessage.query.filter_by(team_id=team.id)
    if before: query=query.filter(ChatMessage.id<before)
    page_rows=query.order_by(ChatMessage.id.desc()).limit(101).all()
    has_older=len(page_rows)>100; messages=page_rows[:100][::-1]
    older_before=messages[0].id if has_older and messages else None
    user_ids={message.user_id for message in messages}
    users={member.id:member for member in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    message_ids=[message.id for message in messages]
    options=ChatPollOption.query.filter(ChatPollOption.message_id.in_(message_ids)).order_by(ChatPollOption.position,ChatPollOption.id).all() if message_ids else []
    votes=ChatPollVote.query.filter(ChatPollVote.message_id.in_(message_ids)).all() if message_ids else []
    options_by_message={}
    vote_counts={}; user_votes={vote.message_id:vote.option_id for vote in votes if vote.user_id==u.id}
    for vote in votes: vote_counts[vote.option_id]=vote_counts.get(vote.option_id,0)+1
    for option in options: options_by_message.setdefault(option.message_id,[]).append(option)
    polls={}
    for message_id,poll_options in options_by_message.items():
        total=sum(vote_counts.get(option.id,0) for option in poll_options)
        polls[message_id]={"total":total,"selected":user_votes.get(message_id),"options":[{"row":option,"votes":vote_counts.get(option.id,0),"percent":round(vote_counts.get(option.id,0)*100/total) if total else 0} for option in poll_options]}
    latest_id=ChatMessage.query.with_entities(db.func.max(ChatMessage.id)).filter_by(team_id=team.id).scalar() or 0
    state=ChatReadState.query.filter_by(team_id=team.id,user_id=u.id).first()
    if state: state.last_read_message_id=max(state.last_read_message_id,latest_id); state.updated_at=datetime.utcnow()
    else: db.session.add(ChatReadState(team_id=team.id,user_id=u.id,last_read_message_id=latest_id))
    PortalNotification.query.filter_by(user_id=u.id,team_id=team.id,kind="chat",read_at=None).update({"read_at":datetime.utcnow()})
    db.session.commit()
    pinned=ChatMessage.query.filter(ChatMessage.team_id==team.id,ChatMessage.pinned_at.isnot(None),ChatMessage.is_deleted==False).order_by(ChatMessage.pinned_at.desc()).all()
    colors=team_color_map(Team.query.order_by(Team.id).all())
    notification_count=PortalNotification.query.filter_by(user_id=u.id,read_at=None).count()
    return render_template("team_chat.html",user=u,team=team,messages=messages,users=users,team_color=colors.get(team.id,TEAM_COLORS[0]),polls=polls,pinned=pinned,latest_id=latest_id,older_before=older_before,notification_count=notification_count)

@app.route("/teams/<int:team_id>/chat/poll",methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def team_chat_poll(team_id):
    u=current_user(); team=db.session.get(Team,team_id)
    if not team or not can_access_team(u,team_id): flash("You do not have access to that team chat."); return redirect(url_for("team_chats"))
    question=request.form.get("question","").strip()[:500]
    options=[]
    for value in request.form.get("options","").splitlines():
        cleaned=value.strip()[:200]
        if cleaned and cleaned.casefold() not in {item.casefold() for item in options}: options.append(cleaned)
    if not question or len(options)<2:
        flash("A poll needs a question and at least two different choices."); return redirect(url_for("team_chat",team_id=team_id))
    options=options[:6]
    message=ChatMessage(team_id=team_id,user_id=u.id,body=question,kind="poll")
    db.session.add(message); db.session.flush()
    db.session.add_all([ChatPollOption(message_id=message.id,text=value,position=index) for index,value in enumerate(options)])
    db.session.commit(); audit("team_chat_poll",f"team_id={team.id},message_id={message.id}")
    notify_team(team.id,"chat",f"New poll in {team.name}",f"{u.name}: {question}",url_for("team_chat",team_id=team.id),exclude_user_id=u.id)
    return redirect(url_for("team_chat",team_id=team_id))

@app.route("/teams/<int:team_id>/chat/poll/<int:message_id>/vote",methods=["POST"])
@login_required
def team_chat_vote(team_id,message_id):
    u=current_user(); message=ChatMessage.query.filter_by(id=message_id,team_id=team_id,kind="poll",is_deleted=False).first()
    if not message or not can_access_team(u,team_id): return redirect(url_for("team_chats"))
    option=ChatPollOption.query.filter_by(id=request.form.get("option_id",type=int),message_id=message_id).first()
    if not option: flash("Choose a valid poll option."); return redirect(url_for("team_chat",team_id=team_id))
    vote=ChatPollVote.query.filter_by(message_id=message_id,user_id=u.id).first()
    if vote: vote.option_id=option.id; vote.created_at=datetime.utcnow()
    else: db.session.add(ChatPollVote(message_id=message_id,option_id=option.id,user_id=u.id))
    db.session.commit(); return redirect(url_for("team_chat",team_id=team_id))

@app.route("/teams/<int:team_id>/chat/message/<int:message_id>/delete",methods=["POST"])
@login_required
def team_chat_message_delete(team_id,message_id):
    u=current_user(); message=ChatMessage.query.filter_by(id=message_id,team_id=team_id).first()
    if not message or not can_access_team(u,team_id) or (message.user_id!=u.id and u.role!="admin"):
        flash("You can only delete your own messages."); return redirect(url_for("team_chat",team_id=team_id))
    message.is_deleted=True; message.deleted_at=datetime.utcnow(); message.pinned_at=None; message.pinned_by_id=None; message.body=""
    db.session.commit(); audit("team_chat_message_deleted",f"team_id={team_id},message_id={message_id}")
    return redirect(url_for("team_chat",team_id=team_id))

@app.route("/teams/<int:team_id>/chat/message/<int:message_id>/pin",methods=["POST"])
@role_required("coach","admin")
def team_chat_message_pin(team_id,message_id):
    u=current_user(); message=ChatMessage.query.filter_by(id=message_id,team_id=team_id,is_deleted=False).first()
    if not message or not can_access_team(u,team_id): return redirect(url_for("team_chats"))
    if message.pinned_at: message.pinned_at=None; message.pinned_by_id=None
    else: message.pinned_at=datetime.utcnow(); message.pinned_by_id=u.id
    db.session.commit(); audit("team_chat_message_pin",f"team_id={team_id},message_id={message_id},pinned={bool(message.pinned_at)}")
    return redirect(url_for("team_chat",team_id=team_id))

@app.route("/teams/<int:team_id>/chat/delete",methods=["POST"])
@role_required("admin")
def team_chat_delete(team_id):
    team=db.session.get(Team,team_id)
    if not team or request.form.get("confirm_name","").strip()!=team.name:
        flash("Enter the exact team name to delete the thread."); return redirect(url_for("team_chat",team_id=team_id))
    message_ids=[message_id for (message_id,) in db.session.query(ChatMessage.id).filter_by(team_id=team_id).all()]
    if message_ids:
        ChatPollVote.query.filter(ChatPollVote.message_id.in_(message_ids)).delete(synchronize_session=False)
        ChatPollOption.query.filter(ChatPollOption.message_id.in_(message_ids)).delete(synchronize_session=False)
        ChatMessage.query.filter_by(team_id=team_id).delete()
    ChatReadState.query.filter_by(team_id=team_id).delete(); db.session.commit(); audit("team_chat_deleted",f"team_id={team_id}")
    flash(f"{team.name} chat thread deleted."); return redirect(url_for("team_chats"))

@app.route("/teams/<int:team_id>/chat/feed")
@login_required
def team_chat_feed(team_id):
    if not can_access_team(current_user(),team_id): return jsonify({"error":"forbidden"}),403
    latest=ChatMessage.query.with_entities(db.func.max(ChatMessage.id)).filter_by(team_id=team_id).scalar() or 0
    return jsonify({"latest_id":latest,"changed":latest>request.args.get("after",0,type=int)})

@app.route("/notifications")
@login_required
def notifications():
    rows=PortalNotification.query.filter_by(user_id=current_user().id).order_by(PortalNotification.created_at.desc()).limit(100).all()
    return render_template("notifications.html",user=current_user(),notifications=rows)

@app.route("/notifications/read",methods=["POST"])
@login_required
def notifications_read():
    PortalNotification.query.filter_by(user_id=current_user().id,read_at=None).update({"read_at":datetime.utcnow()}); db.session.commit()
    return redirect(url_for("notifications"))

@app.route("/notifications/<int:notification_id>/open")
@login_required
def notification_open(notification_id):
    notification=PortalNotification.query.filter_by(id=notification_id,user_id=current_user().id).first()
    if not notification: return redirect(url_for("notifications"))
    if not notification.read_at: notification.read_at=datetime.utcnow(); db.session.commit()
    return redirect(notification.target_url)

@app.route("/notifications/unread")
@login_required
def notifications_unread():
    row=PortalNotification.query.filter_by(user_id=current_user().id,read_at=None).order_by(PortalNotification.created_at.desc()).first()
    return jsonify({"count":PortalNotification.query.filter_by(user_id=current_user().id,read_at=None).count(),"latest":{"id":row.id,"title":row.title,"body":row.body,"url":row.target_url} if row else None})

@app.route("/push/public-key")
@login_required
def push_public_key(): return jsonify({"public_key":vapid_keys()[1]})

@app.route("/push/subscribe",methods=["POST"])
@login_required
def push_subscribe():
    data=request.get_json(silent=True) or {}; endpoint=str(data.get("endpoint","")).strip(); keys=data.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth") or len(endpoint)>1000: return jsonify({"error":"invalid subscription"}),400
    subscription=PushSubscription.query.filter_by(endpoint=endpoint).first()
    payload=json.dumps({"endpoint":endpoint,"expirationTime":data.get("expirationTime"),"keys":{"p256dh":keys["p256dh"],"auth":keys["auth"]}})
    if subscription: subscription.user_id=current_user().id; subscription.subscription_json=payload
    else: db.session.add(PushSubscription(user_id=current_user().id,endpoint=endpoint,subscription_json=payload))
    db.session.commit(); return jsonify({"ok":True})

@app.route("/push/unsubscribe",methods=["POST"])
@login_required
def push_unsubscribe():
    endpoint=str((request.get_json(silent=True) or {}).get("endpoint","")).strip()
    PushSubscription.query.filter_by(user_id=current_user().id,endpoint=endpoint).delete(); db.session.commit(); return jsonify({"ok":True})

@app.route("/service-worker.js")
def service_worker():
    response=app.send_static_file("sw.js"); response.headers["Service-Worker-Allowed"]="/"; response.headers["Cache-Control"]="no-cache"; return response

@app.route("/learn")
@login_required
def learn(): return render_template("learn.html", user=current_user())

def learning_session(question_bank):
    requested=request.args.get("level","competitive").strip().lower()
    level=requested if requested in question_bank else "competitive"
    return level, question_bank[level]

@app.route("/practice")
@login_required
def practice():
    level,questions=learning_session(FIELD_IQ_LEVELS)
    return render_template("practice.html",user=current_user(),questions=questions,levels=LEVELS,active_level=level)

@app.route("/pitch-plan")
@login_required
def pitch_plan():
    level,questions=learning_session(PITCH_PLAN_LEVELS)
    return render_template("pitch_plan.html",user=current_user(),questions=questions,levels=LEVELS,active_level=level)

@app.route("/field-guides")
@login_required
def field_guides(): return render_template("field_guides.html", user=current_user())

@app.route("/workouts")
@login_required
def workouts():
    u=current_user(); cat=request.args.get("category","All"); q=Workout.query.filter_by(active=True)
    if cat!="All": q=q.filter_by(category=cat)
    done={x.workout_id for x in WorkoutCompletion.query.filter_by(player_id=u.id,completed_on=date.today()).all()} if u.role=="player" else set()
    return render_template("workouts.html",user=u,workouts=q.all(),category=cat,completed_ids=done)

@app.route("/shop")
@login_required
def team_store():
    products,error=shop_products()
    categories=["All","Uniforms","Fanwear","Hoodies","Hats","Pants"]
    category=request.args.get("category","All").strip().title()
    if category not in categories: category="All"
    visible=products if category=="All" else [product for product in products if product["category"]==category]
    return render_template("shop.html",user=current_user(),products=visible,categories=categories,category=category,error=error,collection_url=SHOP_COLLECTION_URL)

@app.route("/workouts/add",methods=["POST"])
@role_required("admin")
def add_workout():
    category=request.form.get("category","").strip()
    title=request.form.get("title","").strip()
    level=request.form.get("level","All ages").strip() or "All ages"
    exercises=request.form.get("exercises","").strip()
    video_url=request.form.get("video_url","").strip()
    try: minutes=max(1,min(180,int(request.form.get("minutes",15))))
    except (TypeError,ValueError): minutes=15
    allowed_categories={"Throwing","Fielding","Catching","Baserunning","Batting","Pitching"}
    if category not in allowed_categories or not title or not exercises:
        flash("Add a category, title, and coaching instructions."); return redirect(url_for("workouts"))
    if len(title)>160 or len(level)>30 or len(exercises)>5000:
        flash("The drill information is too long."); return redirect(url_for("workouts"))
    if video_url:
        parsed=urlparse(video_url)
        if parsed.scheme!="https" or not parsed.netloc or len(video_url)>500:
            flash("Use a complete HTTPS video link."); return redirect(url_for("workouts"))
    if Workout.query.filter_by(category=category,title=title).first():
        flash("That drill is already in the library."); return redirect(url_for("workouts"))
    workout=Workout(category=category,title=title,minutes=minutes,level=level,exercises=exercises,video_url=video_url)
    db.session.add(workout); db.session.commit(); audit("workout_added",f"workout_id={workout.id}")
    flash("Drill added. You can upload its demonstration video now."); return redirect(url_for("workout_detail",wid=workout.id))

@app.route("/workout/<int:wid>")
@login_required
def workout_detail(wid):
    workout=db.session.get(Workout,wid)
    if not workout or not workout.active: return redirect(url_for("workouts"))
    rows=Media.query.filter_by(workout_id=wid).order_by(Media.created_at.desc()).all()
    videos=[]
    if media_bucket():
        for media in rows:
            url=s3_client().generate_presigned_url("get_object",Params={"Bucket":media_bucket(),"Key":media.object_key},ExpiresIn=3600)
            videos.append((media,url))
    return render_template("workout_detail.html",user=current_user(),workout=workout,videos=videos,storage_ready=bool(media_bucket()),youtube_url=youtube_embed_url(workout.video_url))

@app.route("/workout/<int:wid>/video",methods=["POST"])
@role_required("admin")
@limiter.limit("20 per day")
def workout_video_upload(wid):
    workout=db.session.get(Workout,wid)
    if not workout: flash("Drill not found."); return redirect(url_for("workouts"))
    if not media_bucket(): flash("Private video storage is not configured yet. You can use a YouTube link instead."); return redirect(url_for("workout_detail",wid=wid))
    upload=request.files.get("media_file")
    allowed={"video/mp4":"mp4","video/quicktime":"mov","video/webm":"webm"}
    if not upload or upload.mimetype not in allowed:
        flash("Upload an MP4, MOV, or WebM video."); return redirect(url_for("workout_detail",wid=wid))
    key=f"drills/{wid}/{uuid.uuid4().hex}.{allowed[upload.mimetype]}"
    try:
        s3_client().put_object(Bucket=media_bucket(),Key=key,Body=upload.stream,ContentType=upload.mimetype)
    except Exception:
        app.logger.exception("DRILL VIDEO UPLOAD FAILED: workout_id=%s",wid)
        flash("The video could not be uploaded. Please try again."); return redirect(url_for("workout_detail",wid=wid))
    media=Media(owner_user_id=current_user().id,workout_id=wid,object_key=key,original_name=(upload.filename or "Drill video")[:255],content_type=upload.mimetype)
    db.session.add(media); db.session.commit(); audit("workout_video_uploaded",f"workout_id={wid},media_id={media.id}")
    flash("Drill video uploaded."); return redirect(url_for("workout_detail",wid=wid))

@app.route("/workout/<int:wid>/video-link",methods=["POST"])
@role_required("admin")
def workout_video_link(wid):
    workout=db.session.get(Workout,wid)
    if not workout: flash("Drill not found."); return redirect(url_for("workouts"))
    video_url=request.form.get("video_url","").strip()
    if video_url:
        parsed=urlparse(video_url)
        if parsed.scheme!="https" or not parsed.netloc or len(video_url)>500:
            flash("Use a complete HTTPS video link."); return redirect(url_for("workout_detail",wid=wid))
    workout.video_url=video_url; db.session.commit(); audit("workout_video_link_updated",f"workout_id={wid}")
    flash("Video link updated."); return redirect(url_for("workout_detail",wid=wid))

@app.route("/workout/<int:wid>/video/<int:mid>/delete",methods=["POST"])
@role_required("admin")
def workout_video_delete(wid,mid):
    media=Media.query.filter_by(id=mid,workout_id=wid).first()
    if not media: flash("Video not found."); return redirect(url_for("workout_detail",wid=wid))
    if media_bucket():
        try: s3_client().delete_object(Bucket=media_bucket(),Key=media.object_key)
        except Exception:
            app.logger.exception("DRILL VIDEO DELETE FAILED: media_id=%s",mid)
            flash("The video could not be removed from storage."); return redirect(url_for("workout_detail",wid=wid))
    db.session.delete(media); db.session.commit(); audit("workout_video_deleted",f"workout_id={wid},media_id={mid}")
    flash("Drill video removed."); return redirect(url_for("workout_detail",wid=wid))

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
    tids=team_ids_for(u)
    rows=Team.query.all() if u.role=="admin" else (Team.query.filter(Team.id.in_(tids)).all() if tids else [])
    return render_template("teams.html",user=u,teams=rows,team_colors=team_color_map(Team.query.order_by(Team.id).all()))


@app.route("/teams/<int:team_id>/add-player", methods=["POST"])
@role_required("coach")
def add_player_to_team(team_id):
    flash("Players create their own accounts and join with the team's code shown below.")
    return redirect(url_for("teams"))


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
    u=current_user()
    tids=team_ids_for(u)
    teams=Team.query.filter(Team.id.in_(tids)).order_by(Team.age_group,Team.name).all() if tids else []
    profiles=RosterProfile.query.filter(RosterProfile.team_id.in_(tids)).order_by(
        RosterProfile.team_id,RosterProfile.name_key,RosterProfile.as_of.desc(),RosterProfile.id.desc()
    ).all() if tids else []
    latest={}; profile_ids={}
    for profile in profiles:
        key=(profile.team_id,profile.name_key)
        latest.setdefault(key,profile)
        profile_ids.setdefault(key,[]).append(profile.id)
    all_profile_ids=[profile_id for ids in profile_ids.values() for profile_id in ids]
    family_links=RosterFamilyLink.query.filter(RosterFamilyLink.profile_id.in_(all_profile_ids)).all() if all_profile_ids else []
    player_links=RosterPlayerLink.query.filter(RosterPlayerLink.profile_id.in_(all_profile_ids)).all() if all_profile_ids else []
    user_ids={link.parent_id for link in family_links}|{link.player_id for link in player_links}
    linked_users={user.id:user for user in User.query.filter(User.id.in_(user_ids),User.is_active.is_(True)).all()} if user_ids else {}
    family_by_profile={}
    for link in family_links:
        if link.parent_id in linked_users:
            family_by_profile.setdefault(link.profile_id,set()).add(link.parent_id)
    player_by_profile={link.profile_id:link.player_id for link in player_links if link.player_id in linked_users}
    rows_by_team={team.id:[] for team in teams}
    linked_count=0
    for key,profile in latest.items():
        snapshot_ids=profile_ids[key]
        family_ids={user_id for profile_id in snapshot_ids for user_id in family_by_profile.get(profile_id,set())}
        player_id=next((player_by_profile[profile_id] for profile_id in snapshot_ids if profile_id in player_by_profile),None)
        families=sorted((linked_users[user_id] for user_id in family_ids),key=lambda user:user.name)
        player_account=linked_users.get(player_id)
        completions=WorkoutCompletion.query.filter_by(player_id=player_id).all() if player_id else []
        linked=bool(families or player_account)
        linked_count+=int(linked)
        rows_by_team[profile.team_id].append({
            "profile":profile,
            "families":families,
            "player_account":player_account,
            "linked":linked,
            "workouts":len(completions),
            "minutes":sum(completion.minutes for completion in completions),
        })
    team_rows=[{"team":team,"players":rows_by_team[team.id]} for team in teams]
    return render_template("coach.html",user=u,team_rows=team_rows,total_players=len(latest),linked_count=linked_count)

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
            GameStat.query.filter(GameStat.player_id==p.id, GameStat.source.like("gamechanger%")).delete(synchronize_session=False)
            db.session.add(GameStat(player_id=p.id,played_on=date.today(),opponent="GameChanger season import",ab=int(n(pick(r,"AB","At Bats"))),hits=int(n(pick(r,"H","Hits"))),walks=int(n(pick(r,"BB","Walks"))),runs=int(n(pick(r,"R","Runs"))),rbi=int(n(pick(r,"RBI"))),sb=int(n(pick(r,"SB","Stolen Bases"))),ip=n(pick(r,"IP","Innings Pitched")),pso=int(n(pick(r,"SO","K","Strikeouts"))),er=int(n(pick(r,"ER","Earned Runs"))),pitches=int(n(pick(r,"Pitches","Pitch Count","PC"))),source=f"gamechanger:{max(1, int(n(pick(r, 'GP', 'Games', 'Games Played')) or 1))}")); imported+=1
        db.session.commit(); audit("gamechanger_import",f"rows={imported}; unmatched={len(unmatched)}"); result={"imported":imported,"unmatched":unmatched}
    return render_template("gamechanger.html",user=u,result=result)

# ---------------- Parent ----------------
from roster_profiles import install as install_roster_profiles
RosterProfile = install_roster_profiles(app, db, Team, current_user, team_ids_for, role_required, audit)
from finances import install as install_finances
from family_access import install as install_family_access
FinanceAccount = None
RosterFamilyLink, RosterPlayerLink = install_family_access(app, db, User, Team, TeamMembership, RosterProfile, current_user, role_required, team_ids_for, audit, send_verification, limiter, lambda: FinanceAccount)
FinanceSetting, FinanceAccount, FinanceEntry, PaymentNotice = install_finances(app, db, User, Team, TeamMembership, RosterProfile, RosterFamilyLink, AuditLog, current_user, role_required)

@app.route("/my-players")
@role_required("parent", "coach")
def my_players():
    u=current_user(); linked=User.query.filter_by(parent_id=u.id,role="player").all() if u.role=="parent" else []; return render_template("my_players.html",user=u,linked=linked)

# ---------------- Administration ----------------
@app.route("/admin")
@role_required("admin")
def admin_panel():
    players=User.query.filter_by(role="player").order_by(User.name).all()
    coaches=User.query.filter_by(role="coach").order_by(User.name).all()
    teams=Team.query.order_by(Team.name).all()
    rows=[]
    for player in players:
        memberships=TeamMembership.query.filter_by(user_id=player.id,role="player").all()
        parent=db.session.get(User,player.parent_id) if player.parent_id else None
        consent=ConsentRequest.query.filter_by(player_id=player.id).order_by(ConsentRequest.created_at.desc()).first()
        rows.append((player,memberships,parent,consent))
    coach_rows=[(coach,TeamMembership.query.filter_by(user_id=coach.id,role="coach").all()) for coach in coaches]
    return render_template("admin.html",user=current_user(),rows=rows,coach_rows=coach_rows,teams=teams)

@app.route("/admin/coach/<int:cid>/team",methods=["POST"])
@role_required("admin")
def admin_assign_coach_team(cid):
    coach=db.session.get(User,cid)
    team=db.session.get(Team,int(request.form.get("team_id",0) or 0))
    if not coach or coach.role!="coach" or not team:
        flash("Coach or team not found."); return redirect(url_for("admin_panel"))
    membership=TeamMembership.query.filter_by(team_id=team.id,user_id=coach.id).first()
    if membership:
        membership.role="coach"; membership.approved=True
    else:
        db.session.add(TeamMembership(team_id=team.id,user_id=coach.id,role="coach",approved=True))
    db.session.commit(); audit("admin_assigned_coach_team",f"coach_id={coach.id},team_id={team.id}")
    flash(f"{coach.name} assigned to {team.name}."); return redirect(url_for("admin_panel"))

@app.route("/admin/coach/<int:cid>/team/<int:team_id>/remove",methods=["POST"])
@role_required("admin")
def admin_remove_coach_team(cid,team_id):
    coach=db.session.get(User,cid); team=db.session.get(Team,team_id)
    membership=TeamMembership.query.filter_by(team_id=team_id,user_id=cid,role="coach").first()
    if not coach or coach.role!="coach" or not membership:
        flash("Coach team assignment not found."); return redirect(url_for("admin_panel"))
    db.session.delete(membership); db.session.commit(); audit("admin_removed_coach_team",f"coach_id={cid},team_id={team_id}")
    flash(f"{coach.name} removed from {team.name if team else 'team'}."); return redirect(url_for("admin_panel"))

@app.route("/admin/player/<int:pid>/team",methods=["POST"])
@role_required("admin")
def admin_assign_team(pid):
    player=db.session.get(User,pid)
    team=db.session.get(Team,int(request.form.get("team_id",0) or 0))
    if not player or player.role!="player" or not team:
        flash("Player or team not found."); return redirect(url_for("admin_panel"))
    membership=TeamMembership.query.filter_by(team_id=team.id,user_id=player.id).first()
    if membership:
        membership.role="player"; membership.approved=player.consent_verified
    else:
        db.session.add(TeamMembership(team_id=team.id,user_id=player.id,role="player",approved=player.consent_verified))
    db.session.commit(); audit("admin_assigned_player_team",f"player_id={player.id},team_id={team.id}")
    flash(f"{player.name} assigned to {team.name}."); return redirect(url_for("admin_panel"))

@app.route("/admin/player/<int:pid>/team/<int:team_id>/remove",methods=["POST"])
@role_required("admin")
def admin_remove_team(pid,team_id):
    player=db.session.get(User,pid); team=db.session.get(Team,team_id)
    membership=TeamMembership.query.filter_by(team_id=team_id,user_id=pid,role="player").first()
    if not player or player.role!="player" or not membership:
        flash("Team assignment not found."); return redirect(url_for("admin_panel"))
    db.session.delete(membership); db.session.commit()
    audit("admin_removed_player_team",f"player_id={pid},team_id={team_id}")
    flash(f"{player.name} removed from {team.name if team else 'team'}."); return redirect(url_for("admin_panel"))

@app.route("/admin/player/<int:pid>/status",methods=["POST"])
@role_required("admin")
def admin_player_status(pid):
    player=db.session.get(User,pid)
    if not player or player.role!="player": flash("Player not found."); return redirect(url_for("admin_panel"))
    player.is_active=request.form.get("status")=="activate"; db.session.commit()
    audit("admin_player_status",f"player_id={pid},active={player.is_active}")
    flash(f"{player.name} account {'activated' if player.is_active else 'disabled'}."); return redirect(url_for("admin_panel"))

@app.route("/admin/player/<int:pid>/delete",methods=["POST"])
@role_required("admin")
@limiter.limit("10 per hour")
def admin_delete_player(pid):
    player=db.session.get(User,pid)
    if not player or player.role!="player": flash("Player not found."); return redirect(url_for("admin_panel"))
    if request.form.get("confirm_name","").strip()!=player.name:
        flash("Enter the player's exact name to confirm deletion."); return redirect(url_for("admin_panel"))
    media_rows=Media.query.filter((Media.owner_user_id==player.id)|(Media.player_id==player.id)).all()
    if media_bucket():
        for media in media_rows:
            try: s3_client().delete_object(Bucket=media_bucket(),Key=media.object_key)
            except Exception: app.logger.exception("Unable to remove stored media for deleted player %s",player.id)
    WorkoutCompletion.query.filter_by(player_id=player.id).delete()
    RosterPlayerLink.query.filter_by(player_id=player.id).delete()
    GameStat.query.filter_by(player_id=player.id).delete()
    TeamMembership.query.filter_by(user_id=player.id).delete()
    message_ids=[message_id for (message_id,) in db.session.query(ChatMessage.id).filter_by(user_id=player.id).all()]
    if message_ids:
        ChatPollVote.query.filter(ChatPollVote.message_id.in_(message_ids)).delete(synchronize_session=False)
        ChatPollOption.query.filter(ChatPollOption.message_id.in_(message_ids)).delete(synchronize_session=False)
        ChatMessage.query.filter_by(user_id=player.id).delete()
    ChatPollVote.query.filter_by(user_id=player.id).delete()
    ChatReadState.query.filter_by(user_id=player.id).delete()
    PortalNotification.query.filter_by(user_id=player.id).delete()
    PushSubscription.query.filter_by(user_id=player.id).delete()
    ConsentRequest.query.filter_by(player_id=player.id).delete()
    Media.query.filter((Media.owner_user_id==player.id)|(Media.player_id==player.id)).delete(synchronize_session=False)
    AuditLog.query.filter_by(user_id=player.id).delete()
    User.query.filter_by(parent_id=player.id).update({"parent_id":None})
    player_name=player.name; db.session.delete(player); db.session.commit()
    audit("admin_deleted_player",f"player_id={pid},name={player_name}")
    flash(f"{player_name} account deleted."); return redirect(url_for("admin_panel"))

# ---------------- Private media/video storage ----------------
@app.route("/media",methods=["GET"])
@login_required
def media_library():
    u=current_user(); q=Media.query.order_by(Media.created_at.desc()).all(); rows=[m for m in q if can_view_media(u,m)]
    players=[]
    if u.role=="admin":
        players=User.query.filter_by(role="player").all()
    elif u.role=="coach":
        tids=team_ids_for(u); mids=TeamMembership.query.filter(TeamMembership.team_id.in_(tids),TeamMembership.role=="player").all() if tids else []
        players=[db.session.get(User,m.user_id) for m in mids if db.session.get(User,m.user_id)]
    elif u.role=="parent":
        players=User.query.filter_by(parent_id=u.id,role="player").all()
    return render_template("media.html",user=u,rows=rows,players=players,storage_ready=bool(media_bucket()))

@app.route("/media/upload",methods=["POST"])
@role_required("coach","parent","admin")
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
    try:
        s3_client().put_object(Bucket=media_bucket(),Key=key,Body=f.stream,ContentType=f.mimetype)
    except Exception as e:
        response = getattr(e, "response", None)
        app.logger.error("VIDEO UPLOAD ERROR RESPONSE: %r", response)
        app.logger.exception("VIDEO UPLOAD FAILED: %r", e)
        raise
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
    WorkoutCompletion.query.filter_by(player_id=u.id).delete(); GameStat.query.filter_by(player_id=u.id).delete(); TeamMembership.query.filter_by(user_id=u.id).delete(); ConsentRequest.query.filter_by(player_id=u.id).delete(); RosterPlayerLink.query.filter_by(player_id=u.id).delete(); Media.query.filter((Media.owner_user_id==u.id)|(Media.player_id==u.id)).delete(synchronize_session=False)
    ChatPollVote.query.filter_by(user_id=u.id).delete(); ChatReadState.query.filter_by(user_id=u.id).delete(); PortalNotification.query.filter_by(user_id=u.id).delete(); PushSubscription.query.filter_by(user_id=u.id).delete()
    u.email=f"deleted-{u.id}-{secrets.token_hex(4)}@invalid.local"; u.name="Deleted User"; u.password_hash=generate_password_hash(secrets.token_urlsafe(40)); u.is_active=False
    db.session.commit(); audit("account_deleted",who=u); session.clear(); flash("Account deleted."); return redirect(url_for("login"))

@app.route("/privacy")
def privacy(): return render_template("privacy.html",user=current_user())
@app.route("/terms")
def terms(): return render_template("terms.html",user=current_user())
@app.route("/health")
def health(): return jsonify({"ok":True,"time":datetime.utcnow().isoformat()+"Z"})

with app.app_context():
    initialize_database()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
