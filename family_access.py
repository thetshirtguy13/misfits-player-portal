"""Admin-approved parent access to imported rosters without creating child logins."""
from datetime import date
from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError


def install(app, db, User, Team, TeamMembership, RosterProfile, current_user, role_required, team_ids_for, audit, send_verification, limiter):
    class RosterFamilyLink(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
        profile_id = db.Column(db.Integer, db.ForeignKey('roster_profile.id'), nullable=False, index=True)
        __table_args__ = (db.UniqueConstraint('parent_id', 'profile_id', name='uq_roster_family'),)

    class RosterPlayerLink(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        profile_id = db.Column(db.Integer, db.ForeignKey('roster_profile.id'), nullable=False, unique=True, index=True)
        player_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    @app.context_processor
    def family_context():
        u = current_user()
        profiles = []
        if u and u.role == 'parent':
            direct = RosterProfile.query.join(RosterFamilyLink, RosterFamilyLink.profile_id == RosterProfile.id).filter(RosterFamilyLink.parent_id == u.id).all()
            child_ids = [child.id for child in User.query.filter_by(parent_id=u.id, role='player').all()]
            account = RosterProfile.query.join(RosterPlayerLink, RosterPlayerLink.profile_id == RosterProfile.id).filter(RosterPlayerLink.player_id.in_(child_ids)).all() if child_ids else []
            profiles = latest_profiles(direct + account)
        elif u and u.role == 'player':
            profiles = latest_profiles(RosterProfile.query.join(RosterPlayerLink, RosterPlayerLink.profile_id == RosterProfile.id).filter(RosterPlayerLink.player_id == u.id).all())
        return {'family_roster_profiles': profiles}

    def latest_profiles(profiles):
        latest = {}
        for profile in sorted(profiles, key=lambda item: (item.as_of, item.id), reverse=True):
            latest.setdefault((profile.team_id, profile.name_key), profile)
        return sorted(latest.values(), key=lambda profile: profile.name)

    def logical_profiles(profile):
        return RosterProfile.query.filter_by(team_id=profile.team_id, name_key=profile.name_key).all()

    @app.route('/admin/rosters')
    @role_required('admin')
    def admin_rosters():
        teams = Team.query.order_by(Team.age_group, Team.name).all()
        team_id = request.args.get('team_id', type=int) or (teams[0].id if teams else None)
        if team_id and not any(team.id == team_id for team in teams):
            abort(404)
        profiles = RosterProfile.query.filter_by(team_id=team_id).order_by(RosterProfile.name_key, RosterProfile.as_of.desc(), RosterProfile.id.desc()).all() if team_id else []
        latest = {}
        for profile in profiles:
            latest.setdefault(profile.name_key, profile)
        profile_ids = [profile.id for profile in profiles]
        player_links = RosterPlayerLink.query.filter(RosterPlayerLink.profile_id.in_(profile_ids)).all() if profile_ids else []
        family_links = RosterFamilyLink.query.filter(RosterFamilyLink.profile_id.in_(profile_ids)).all() if profile_ids else []
        player_by_profile = {link.profile_id: db.session.get(User, link.player_id) for link in player_links}
        parents_by_profile = {}
        for link in family_links:
            parent = db.session.get(User, link.parent_id)
            if parent:
                parents_by_profile.setdefault(link.profile_id, []).append(parent)
        rows = []
        for profile in latest.values():
            snapshots = [item for item in profiles if item.name_key == profile.name_key]
            account = next((player_by_profile.get(item.id) for item in snapshots if player_by_profile.get(item.id)), None)
            parents = {parent.id: parent for item in snapshots for parent in parents_by_profile.get(item.id, [])}
            rows.append({'profile': profile, 'account': account, 'parents': sorted(parents.values(), key=lambda parent: parent.name)})
        players = User.query.filter_by(role='player', is_active=True).order_by(User.name).all()
        newest = max(profiles, key=lambda profile: (profile.as_of, profile.id)) if profiles else None
        return render_template('admin_rosters.html', user=current_user(), teams=teams, team_id=team_id, rows=rows, players=players,
            default_season=newest.season if newest else 'Fall 2026', default_as_of=(newest.as_of if newest else date.today()).isoformat())

    @app.route('/admin/rosters/add', methods=['POST'])
    @role_required('admin')
    def admin_roster_add():
        team = db.session.get(Team, request.form.get('team_id', type=int))
        full_name = ' '.join(request.form.get('name', '').split())[:120]
        jersey = request.form.get('jersey', '').strip()[:12]
        season = request.form.get('season', '').strip()[:80]
        try:
            as_of = date.fromisoformat(request.form.get('as_of', ''))
        except ValueError:
            as_of = None
        if not team or len(full_name.split()) < 2 or not season or not as_of:
            flash('Choose a team and enter the player\'s full name, season, and snapshot date.')
            return redirect(url_for('admin_rosters', team_id=team.id if team else None))
        name_key = full_name.casefold()
        if RosterProfile.query.filter_by(team_id=team.id, name_key=name_key, season=season).first():
            flash(f'{full_name} is already on this team roster for {season}.')
            return redirect(url_for('admin_rosters', team_id=team.id))
        profile = RosterProfile(team_id=team.id, name=full_name, name_key=name_key, jersey=jersey, season=season, as_of=as_of, stats={})
        db.session.add(profile)
        db.session.commit()
        audit('roster_profile_added', f'team_id={team.id}; profile_id={profile.id}')
        flash(f'{full_name} added to {team.name}.')
        return redirect(url_for('admin_rosters', team_id=team.id))

    @app.route('/admin/rosters/<int:pid>/name', methods=['POST'])
    @role_required('admin')
    def admin_roster_name(pid):
        profile = db.session.get(RosterProfile, pid)
        full_name = ' '.join(request.form.get('name', '').split())[:120]
        jersey = request.form.get('jersey', '').strip()[:12]
        if not profile or len(full_name.split()) < 2:
            flash('Enter the player\'s first and last name.')
            return redirect(url_for('admin_rosters', team_id=profile.team_id if profile else None))
        old_key = profile.name_key
        new_key = full_name.casefold()
        snapshots = logical_profiles(profile)
        try:
            for snapshot in snapshots:
                snapshot.name = full_name
                snapshot.name_key = new_key
                snapshot.jersey = jersey
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('That full name already exists on this team roster.')
            return redirect(url_for('admin_rosters', team_id=profile.team_id))
        audit('roster_profile_renamed', f'team_id={profile.team_id}; old_key={old_key}; new_key={new_key}')
        flash(f'Roster profile updated for {full_name}.')
        return redirect(url_for('admin_rosters', team_id=profile.team_id))

    @app.route('/admin/rosters/<int:pid>/player-account', methods=['POST'])
    @role_required('admin')
    def admin_roster_player_account(pid):
        profile = db.session.get(RosterProfile, pid)
        if not profile:
            abort(404)
        player_id = request.form.get('player_id', type=int)
        player = db.session.get(User, player_id) if player_id else None
        if player_id and (not player or player.role != 'player' or not player.is_active):
            abort(400)
        snapshots = logical_profiles(profile)
        snapshot_ids = [snapshot.id for snapshot in snapshots]
        if player:
            other = db.session.query(RosterPlayerLink, RosterProfile).join(RosterProfile, RosterProfile.id == RosterPlayerLink.profile_id).filter(RosterPlayerLink.player_id == player.id, ~RosterPlayerLink.profile_id.in_(snapshot_ids)).first()
            if other:
                flash(f'{player.name} is already linked to another roster player. Unlink that record first.')
                return redirect(url_for('admin_rosters', team_id=profile.team_id))
        RosterPlayerLink.query.filter(RosterPlayerLink.profile_id.in_(snapshot_ids)).delete(synchronize_session=False)
        if player:
            db.session.add_all([RosterPlayerLink(profile_id=snapshot.id, player_id=player.id) for snapshot in snapshots])
            if len(profile.name.split()) < 2 and len(player.name.split()) >= 2:
                full_name = ' '.join(player.name.split())[:120]
                full_name_key = full_name.casefold()
                conflict = RosterProfile.query.filter(RosterProfile.team_id == profile.team_id, RosterProfile.name_key == full_name_key, ~RosterProfile.id.in_(snapshot_ids)).first()
                if not conflict:
                    for snapshot in snapshots:
                        snapshot.name = full_name
                        snapshot.name_key = full_name_key
            membership = TeamMembership.query.filter_by(team_id=profile.team_id, user_id=player.id).first()
            if membership:
                membership.role = 'player'
                membership.approved = player.consent_verified
            else:
                db.session.add(TeamMembership(team_id=profile.team_id, user_id=player.id, role='player', approved=player.consent_verified))
            if not player.age_group:
                player.age_group = db.session.get(Team, profile.team_id).age_group
        db.session.commit()
        audit('roster_player_account_linked' if player else 'roster_player_account_unlinked', f'profile_id={pid}; player_id={player.id if player else ""}')
        flash(f'{profile.name} is now linked to {player.name}.' if player else f'{profile.name} is no longer linked to a player login.')
        return redirect(url_for('admin_rosters', team_id=profile.team_id))

    @app.route('/admin/families')
    @role_required('admin')
    def admin_families():
        parents = User.query.filter_by(role='parent', is_active=True).order_by(User.name).all()
        profiles = RosterProfile.query.order_by(RosterProfile.team_id, RosterProfile.name).all()
        links = RosterFamilyLink.query.all()
        return render_template('admin_families.html', user=current_user(), parents=parents, profiles=profiles,
            teams={t.id: t for t in Team.query.all()}, linked={p.id: [l.profile_id for l in links if l.parent_id == p.id] for p in parents})

    @app.route('/admin/families/<int:uid>/verification', methods=['POST'])
    @role_required('admin')
    @limiter.limit('10 per hour')
    def admin_parent_verification(uid):
        parent = db.session.get(User, uid)
        if not parent or parent.role != 'parent' or not parent.is_active:
            abort(404)
        if parent.email_verified:
            flash('This parent email is already verified.')
        else:
            sent = send_verification(parent)
            audit('parent_verification_requested', f'parent_id={uid}; accepted={sent}')
        return redirect(url_for('admin_families'))

    @app.route('/admin/families/<int:uid>/link', methods=['POST'])
    @role_required('admin')
    def admin_link_roster_family(uid):
        parent = db.session.get(User, uid)
        profile = db.session.get(RosterProfile, request.form.get('profile_id', type=int)) if request.form.get('profile_id', type=int) else None
        if not parent or parent.role != 'parent' or not parent.is_active or not profile:
            abort(400)
        if not RosterFamilyLink.query.filter_by(parent_id=uid, profile_id=profile.id).first():
            db.session.add(RosterFamilyLink(parent_id=uid, profile_id=profile.id))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            audit('roster_family_linked', f'parent_id={uid}; profile_id={profile.id}')
        flash(f'{profile.name} is now visible under My Players for {parent.email}.')
        return redirect(url_for('admin_families'))

    @app.route('/family-roster/<int:pid>')
    @role_required('parent', 'coach', 'player')
    def family_roster_profile(pid):
        profile = db.session.get(RosterProfile, pid)
        u = current_user()
        if not profile:
            abort(404)
        child_ids = [child.id for child in User.query.filter_by(parent_id=u.id, role='player').all()] if u.role == 'parent' else []
        parent_account_link = RosterPlayerLink.query.filter(RosterPlayerLink.player_id.in_(child_ids), RosterPlayerLink.profile_id == pid).first() if child_ids else None
        allowed = u.role == 'admin' or (u.role == 'coach' and profile.team_id in team_ids_for(u)) or (u.role == 'parent' and (RosterFamilyLink.query.filter_by(parent_id=u.id, profile_id=pid).first() or parent_account_link)) or (u.role == 'player' and RosterPlayerLink.query.filter_by(player_id=u.id, profile_id=pid).first())
        if not allowed:
            abort(403)
        return render_template('family_roster_profile.html', user=u, profile=profile, team=db.session.get(Team, profile.team_id),
            fields=('GP','PA','AB','H','AVG','OBP','OPS','SLG','SB','BB','R','RBI'))

    return RosterFamilyLink, RosterPlayerLink
