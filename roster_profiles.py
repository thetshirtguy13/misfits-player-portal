"""Team-owned development profiles, separate from family login accounts."""
import csv
import io
import math
from datetime import date
from flask import abort, flash, redirect, render_template, request, url_for


def install(app, db, Team, current_user, team_ids_for, role_required, audit):
    class RosterProfile(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
        name = db.Column(db.String(120), nullable=False)
        name_key = db.Column(db.String(120), nullable=False)
        jersey = db.Column(db.String(12), default='')
        season = db.Column(db.String(80), nullable=False)
        as_of = db.Column(db.Date, nullable=False)
        stats = db.Column(db.JSON, nullable=False, default=dict)
        focus = db.Column(db.String(500), default='')
        notes = db.Column(db.Text, default='')
        __table_args__ = (db.UniqueConstraint('team_id', 'name_key', 'season', name='uq_roster_season'),)

    fields = ('GP', 'PA', 'AB', 'H', 'AVG', 'OBP', 'OPS', 'SLG', 'SB', 'BB', 'R', 'RBI')

    @app.route('/roster', methods=['GET', 'POST'])
    @role_required('coach')
    def development_roster():
        u = current_user()
        tids = team_ids_for(u)
        teams = Team.query.filter(Team.id.in_(tids)).order_by(Team.name).all()
        team_id = request.values.get('team_id', type=int)
        if team_id is None and teams:
            team_id = teams[0].id
        if team_id is not None and team_id not in tids:
            abort(403)
        if request.method == 'POST':
            if team_id is None:
                abort(400)
            f = request.files.get('csv_file')
            season = request.form.get('season', '').strip()[:80]
            try:
                as_of = date.fromisoformat(request.form.get('as_of', ''))
                pasted = request.form.get('csv_text', '').strip()
                if not season or (not pasted and (not f or not f.filename.lower().endswith('.csv'))):
                    raise ValueError('Choose or paste a CSV, season, and snapshot date.')
                text = pasted.encode('utf-8') if pasted else f.read(1000001)
                if len(text) > 1000000:
                    raise ValueError('Use a CSV smaller than 1 MB.')
                reader = csv.DictReader(io.StringIO(text.decode('utf-8-sig')))
                if not reader.fieldnames or 'Player' not in reader.fieldnames:
                    raise ValueError('The CSV needs a Player column and statistic headers such as GP, PA, AB, H, AVG, OBP, OPS, SLG, SB.')
                pending = []
                seen = set()
                for row in reader:
                    name = (row.get('Player') or '').strip()
                    if not name or name.lower() in {'team', 'totals', 'total'}:
                        continue
                    key = ' '.join(name.casefold().split())
                    if len(name) > 120 or key in seen:
                        raise ValueError('Player names must be unique within this file and under 121 characters.')
                    seen.add(key)
                    stats = {}
                    for field in fields:
                        value = (row.get(field) or '').strip()
                        if not value:
                            continue
                        number = float(value)
                        if not math.isfinite(number) or number < 0:
                            raise ValueError('Statistics must be finite, nonnegative numbers.')
                        if field not in {'AVG', 'OBP', 'OPS', 'SLG'} and not number.is_integer():
                            raise ValueError('Counting statistics must be whole numbers.')
                        if field in {'AVG', 'OBP'} and number > 1:
                            raise ValueError('AVG and OBP must be between 0 and 1.')
                        stats[field] = number if field in {'AVG', 'OBP', 'OPS', 'SLG'} else int(number)
                    if stats.get('H', 0) > stats.get('AB', stats.get('H', 0)):
                        raise ValueError('Hits cannot exceed at-bats.')
                    existing = RosterProfile.query.filter_by(team_id=team_id, name_key=key, season=season).first()
                    if existing and as_of < existing.as_of:
                        raise ValueError('This snapshot is older than an existing player record. Use the current snapshot date.')
                    pending.append((existing, name, key, (row.get('Jersey') or '').strip()[:12], stats))
                if not pending:
                    raise ValueError('No player rows were found.')
                for existing, name, key, jersey, stats in pending:
                    profile = existing or RosterProfile(team_id=team_id, name=name, name_key=key, season=season)
                    profile.name, profile.jersey, profile.as_of, profile.stats = name, jersey, as_of, stats
                    db.session.add(profile)
                db.session.commit()
                audit('development_roster_import', f'team_id={team_id}; rows={len(pending)}')
                flash(f'{len(pending)} player profiles updated. Season snapshots replace prior totals; they are not added together.')
            except (ValueError, UnicodeError, csv.Error) as exc:
                db.session.rollback()
                flash(str(exc))
            return redirect(url_for('development_roster', team_id=team_id))
        profiles = RosterProfile.query.filter_by(team_id=team_id).order_by(RosterProfile.season.desc(), RosterProfile.name).all() if team_id else []
        return render_template('development_roster.html', user=u, teams=teams, team_id=team_id, profiles=profiles, fields=fields, today=date.today().isoformat())

    @app.route('/roster/<int:pid>/development', methods=['POST'])
    @role_required('coach')
    def update_development_profile(pid):
        profile = db.session.get(RosterProfile, pid)
        if not profile or profile.team_id not in team_ids_for(current_user()):
            abort(403)
        profile.focus = request.form.get('focus', '').strip()[:500]
        profile.notes = request.form.get('notes', '').strip()[:4000]
        db.session.commit()
        audit('development_profile_updated', f'profile_id={pid}')
        flash('Development notes saved.')
        return redirect(url_for('development_roster', team_id=profile.team_id))

    return RosterProfile
