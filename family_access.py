"""Admin-approved parent access to imported rosters without creating child logins."""
from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError


def install(app, db, User, Team, RosterProfile, current_user, role_required, team_ids_for, audit, send_verification, limiter):
    class RosterFamilyLink(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
        profile_id = db.Column(db.Integer, db.ForeignKey('roster_profile.id'), nullable=False, index=True)
        __table_args__ = (db.UniqueConstraint('parent_id', 'profile_id', name='uq_roster_family'),)

    @app.context_processor
    def family_context():
        u = current_user()
        profiles = RosterProfile.query.join(RosterFamilyLink, RosterFamilyLink.profile_id == RosterProfile.id).filter(RosterFamilyLink.parent_id == u.id).order_by(RosterProfile.name).all() if u and u.role == 'parent' else []
        return {'family_roster_profiles': profiles}

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
    @role_required('parent', 'coach')
    def family_roster_profile(pid):
        profile = db.session.get(RosterProfile, pid)
        u = current_user()
        if not profile:
            abort(404)
        allowed = u.role == 'admin' or (u.role == 'coach' and profile.team_id in team_ids_for(u)) or (u.role == 'parent' and RosterFamilyLink.query.filter_by(parent_id=u.id, profile_id=pid).first())
        if not allowed:
            abort(403)
        return render_template('family_roster_profile.html', user=u, profile=profile, team=db.session.get(Team, profile.team_id),
            fields=('GP','PA','AB','H','AVG','OBP','OPS','SLG','SB','BB','R','RBI'))

    return RosterFamilyLink
