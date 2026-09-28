"""Private family statements and an append-only, administrator-managed ledger."""
import re
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError


def cents(value, allow_zero=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 1000000 or amount != amount.quantize(Decimal('.01')):
            raise ValueError()
        if not allow_zero and not amount:
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError):
        raise ValueError('Enter a positive dollar amount with at most two decimal places (maximum $1,000,000).')


def venmo_username(value):
    value = value.strip()
    if value.startswith('https://'):
        parsed = urlparse(value)
        if parsed.netloc not in {'venmo.com', 'www.venmo.com'} or parsed.query or parsed.fragment:
            raise ValueError('Use a Venmo username or a venmo.com profile link.')
        value = parsed.path.removeprefix('/u/').strip('/')
    value = value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_-]{5,30}', value):
        raise ValueError('Enter the Venmo username (5–30 letters, numbers, underscores or hyphens).')
    return value


def install(app, db, User, Team, TeamMembership, RosterProfile, AuditLog, current_user, role_required):
    class FinanceSetting(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        venmo = db.Column(db.String(30), default='')

    class FinanceAccount(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
        name = db.Column(db.String(120), nullable=False)
        name_key = db.Column(db.String(120), nullable=False)
        season = db.Column(db.String(80), nullable=False)
        player_id = db.Column(db.Integer, db.ForeignKey('user.id'))
        parent_id = db.Column(db.Integer, db.ForeignKey('user.id'))
        __table_args__ = (db.UniqueConstraint('team_id', 'name_key', 'season', name='uq_finance_account'),)

    class FinanceEntry(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        account_id = db.Column(db.Integer, db.ForeignKey('finance_account.id'), nullable=False, index=True)
        kind = db.Column(db.String(30), nullable=False)
        cents = db.Column(db.Integer, nullable=False)
        description = db.Column(db.String(500), nullable=False)
        sponsor = db.Column(db.String(160), default='')
        method = db.Column(db.String(20), default='')
        reference = db.Column(db.String(160), default='')
        reference_key = db.Column(db.String(200), unique=True)
        due_on = db.Column(db.Date)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        operation_key = db.Column(db.String(100), nullable=False, unique=True)
        reversal_of = db.Column(db.Integer, db.ForeignKey('finance_entry.id'), unique=True)

    class PaymentNotice(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        account_id = db.Column(db.Integer, db.ForeignKey('finance_account.id'), nullable=False, index=True)
        cents = db.Column(db.Integer, nullable=False)
        reference = db.Column(db.String(160), nullable=False)
        sponsor = db.Column(db.String(160), default='')
        status = db.Column(db.String(20), nullable=False, default='pending')
        submitted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        reviewed_at = db.Column(db.DateTime)
        reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
        review_note = db.Column(db.String(500), default='')
        operation_key = db.Column(db.String(100), unique=True, nullable=False)

    def log(action, detail):
        db.session.add(AuditLog(user_id=current_user().id, action=action, detail=detail[:1500], ip_address=request.remote_addr or ''))

    def key(name):
        return ' '.join(name.casefold().split())

    def name_tokens(name_key):
        parts = name_key.split()
        return {parts[0], parts[-1]} if parts else set()

    def token():
        value = request.form.get('operation_key', '')
        if not re.fullmatch(r'[a-f0-9]{32}', value):
            raise ValueError('Reload the page before submitting this form.')
        return value

    def get_account(aid):
        a = db.session.get(FinanceAccount, aid)
        u = current_user()
        if not a:
            abort(404)
        player = db.session.get(User, a.player_id) if a.player_id else None
        permitted = (
            u.role == 'admin'
            or (u.role == 'player' and a.player_id == u.id)
            or (u.role == 'parent' and (a.parent_id == u.id or (player and player.parent_id == u.id)))
        )
        if not permitted:
            abort(403)
        return a

    def entries(a):
        return FinanceEntry.query.filter_by(account_id=a.id).order_by(FinanceEntry.created_at.desc(), FinanceEntry.id.desc()).all()

    def balance(a):
        return sum(e.cents for e in entries(a))

    def finish(destination, **kwargs):
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('This action or payment reference was already recorded. Nothing was added twice.')
        return redirect(url_for(destination, **kwargs))

    @app.template_filter('money')
    def money(value):
        return f'${abs(value) / 100:,.2f}'

    @app.context_processor
    def finance_helpers():
        return {'finance_token': lambda: secrets.token_hex(16)}

    @app.route('/finances')
    @role_required('parent', 'player')
    def finances():
        u = current_user()
        if u.role == 'admin':
            accounts = FinanceAccount.query.order_by(FinanceAccount.team_id, FinanceAccount.name, FinanceAccount.season).all()
        elif u.role == 'player':
            accounts = FinanceAccount.query.filter_by(player_id=u.id).order_by(FinanceAccount.name).all()
        else:
            accounts = FinanceAccount.query.filter(db.or_(FinanceAccount.parent_id == u.id, FinanceAccount.player_id.in_(db.select(User.id).where(User.parent_id == u.id, User.role == 'player')))).order_by(FinanceAccount.name).all()
        team_id = request.args.get('team_id', type=int)
        if team_id:
            accounts = [a for a in accounts if a.team_id == team_id]
        data = [(a, balance(a)) for a in accounts]
        pending = PaymentNotice.query.filter_by(status='pending').order_by(PaymentNotice.created_at).all() if u.role == 'admin' else []
        return render_template('finances.html', user=u, data=data, teams={t.id: t for t in Team.query.all()}, team_id=team_id,
            due=sum(max(b, 0) for a, b in data), credits=sum(max(-b, 0) for a, b in data), pending=pending,
            account_map={a.id: a for a in FinanceAccount.query.all()} if u.role == 'admin' else {}, setting=db.session.get(FinanceSetting, 1))

    @app.route('/finances/settings', methods=['POST'])
    @role_required('admin')
    def finance_settings():
        try:
            value = venmo_username(request.form.get('venmo', ''))
            setting = db.session.get(FinanceSetting, 1) or FinanceSetting(id=1)
            setting.venmo = value
            db.session.add(setting)
            log('finance_venmo_updated', f'username={value}')
            db.session.commit()
            flash('Venmo destination saved.')
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for('finances'))

    @app.route('/finances/setup', methods=['POST'])
    @role_required('admin')
    def finance_setup():
        try:
            season = request.form.get('season', '').strip()
            tids = set(request.form.getlist('team_ids', type=int))
            if not season or len(season) > 80 or not tids or not tids.issubset({t.id for t in Team.query.all()}):
                raise ValueError('Choose at least one team and a season.')
            charge = cents(request.form.get('dues', ''), allow_zero=True)
            discount = cents(request.form.get('discount', ''), allow_zero=True)
            if discount > charge:
                raise ValueError('The fall discount cannot exceed the dues.')
            due_on = date.fromisoformat(request.form['due_on']) if request.form.get('due_on') else None
            count = 0
            renamed = 0
            for tid in sorted(tids):
                players = User.query.join(TeamMembership, TeamMembership.user_id == User.id).filter(TeamMembership.team_id == tid, TeamMembership.role == 'player', User.role == 'player', User.is_active.is_(True)).all()
                roster = RosterProfile.query.filter_by(team_id=tid, season=season).all()
                names = {p.name_key: p.name for p in roster}
                roster_tokens = [(p.name_key, name_tokens(p.name_key)) for p in roster]
                for player in players:
                    player_key = key(player.name)
                    if player_key not in names and not any(name_tokens(player_key) & tokens for _, tokens in roster_tokens):
                        names[player_key] = player.name
                existing = FinanceAccount.query.filter_by(team_id=tid, season=season).all()
                assigned = set()
                for name_key, name in names.items():
                    a = FinanceAccount.query.filter_by(team_id=tid, name_key=name_key, season=season).first()
                    if not a:
                        tokens = name_tokens(name_key)
                        candidates = [account for account in existing if account.id not in assigned and name_tokens(account.name_key) & tokens]
                        competing_names = [candidate_key for candidate_key in names if name_tokens(candidate_key) & name_tokens(candidates[0].name_key)] if len(candidates) == 1 else []
                        if len(candidates) == 1 and competing_names == [name_key]:
                            a = candidates[0]
                            a.name = name
                            a.name_key = name_key
                            renamed += 1
                    if not a:
                        a = FinanceAccount(team_id=tid, name_key=name_key, name=name, season=season)
                        db.session.add(a)
                        db.session.flush()
                        count += 1
                        existing.append(a)
                    assigned.add(a.id)
                    matches = [p for p in players if key(p.name) == name_key]
                    if not a.player_id and len(matches) == 1:
                        a.player_id = matches[0].id
                    # Stable keys make repeating setup safe; corrections use ledger entries.
                    for kind, amount, description in [('dues', charge, 'Season dues'), ('discount', -discount, 'Fall league discount')]:
                        operation = f'setup:{a.id}:{kind}'
                        if amount and not FinanceEntry.query.filter_by(operation_key=operation).first():
                            db.session.add(FinanceEntry(account_id=a.id, kind=kind, cents=amount, description=description,
                                due_on=due_on if kind == 'dues' else None, created_by=current_user().id, operation_key=operation))
            log('finance_setup', f'teams={sorted(tids)}; season={season}; new_accounts={count}; renamed_accounts={renamed}; dues_cents={charge}; discount_cents={discount}')
            flash(f'{count} new player accounts prepared and {renamed} existing names updated. Existing balances and season charges were preserved.')
            return finish('finances')
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc))
            return redirect(url_for('finances'))

    @app.route('/finances/<int:aid>')
    @role_required('parent', 'player')
    def finance_account(aid):
        a = get_account(aid)
        rows = entries(a)
        setting = db.session.get(FinanceSetting, 1)
        admin = current_user().role == 'admin'
        players = User.query.join(TeamMembership, TeamMembership.user_id == User.id).filter(TeamMembership.team_id == a.team_id, User.role == 'player', User.is_active.is_(True)).all() if admin else []
        return render_template('finance_account.html', user=current_user(), account=a, team=db.session.get(Team, a.team_id), rows=rows,
            balance=sum(e.cents for e in rows), notices=PaymentNotice.query.filter_by(account_id=aid).order_by(PaymentNotice.id.desc()).all(),
            reversed_ids={e.reversal_of for e in rows if e.reversal_of}, setting=setting,
            parents=User.query.filter_by(role='parent', is_active=True).order_by(User.name).all() if admin else [], players=players,
            linked_player=db.session.get(User, a.player_id) if a.player_id else None)

    @app.route('/finances/<int:aid>/link', methods=['POST'])
    @role_required('admin')
    def finance_link(aid):
        a = get_account(aid)
        pid = request.form.get('player_id', type=int)
        parent_id = request.form.get('parent_id', type=int)
        if pid:
            p = db.session.get(User, pid)
            if not p or p.role != 'player' or not p.is_active or not TeamMembership.query.filter_by(team_id=a.team_id, user_id=pid, role='player').first():
                abort(400)
        if parent_id:
            p = db.session.get(User, parent_id)
            if not p or p.role != 'parent' or not p.is_active:
                abort(400)
        a.player_id, a.parent_id = pid, parent_id
        log('finance_family_link', f'account={aid}; player={pid}; parent={parent_id}')
        flash('Family access saved.')
        return finish('finance_account', aid=aid)

    @app.route('/finances/<int:aid>/entry', methods=['POST'])
    @role_required('admin')
    def finance_entry(aid):
        get_account(aid)
        try:
            kind = request.form.get('kind', '')
            signs = {'charge': 1, 'payment': -1, 'sponsor': -1, 'discount': -1, 'adjustment_credit': -1, 'adjustment_debit': 1, 'refund': 1}
            if kind not in signs:
                raise ValueError('Choose an entry type.')
            amount = cents(request.form.get('amount', ''))
            description = request.form.get('description', '').strip()[:500]
            sponsor = request.form.get('sponsor', '').strip()[:160]
            method = request.form.get('method', '') if kind in {'payment', 'sponsor', 'refund'} else ''
            reference = request.form.get('reference', '').strip()[:160] if method else ''
            if not description or (kind == 'sponsor' and not sponsor):
                raise ValueError('Add a description and, for a sponsor payment, the sponsor name.')
            if method not in {'', 'Venmo', 'Cash', 'Check', 'Other'} or (kind in {'payment', 'sponsor', 'refund'} and not method):
                raise ValueError('Select the payment method.')
            if method == 'Venmo' and not reference:
                raise ValueError('Enter the Venmo transaction ID after confirming receipt in Venmo.')
            due_on = date.fromisoformat(request.form['due_on']) if request.form.get('due_on') and signs[kind] == 1 else None
            reference_key = f'{method}:{key(reference)}' if reference else None
            db.session.add(FinanceEntry(account_id=aid, kind=kind, cents=amount * signs[kind], description=description,
                sponsor=sponsor, method=method, reference=reference, reference_key=reference_key, due_on=due_on,
                created_by=current_user().id, operation_key=token()))
            log('finance_entry_added', f'account={aid}; kind={kind}; cents={amount * signs[kind]}')
            flash('Ledger entry recorded.')
            return finish('finance_account', aid=aid)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc))
            return redirect(url_for('finance_account', aid=aid))

    @app.route('/finances/<int:aid>/report', methods=['POST'])
    @role_required('parent', 'player')
    def finance_report(aid):
        get_account(aid)
        try:
            amount = cents(request.form.get('amount', ''))
            reference = request.form.get('reference', '').strip()[:160]
            if not reference:
                raise ValueError('Enter the Venmo transaction ID or identifying payment details.')
            db.session.add(PaymentNotice(account_id=aid, cents=amount, reference=reference,
                sponsor=request.form.get('sponsor', '').strip()[:160], submitted_by=current_user().id, operation_key=token()))
            log('finance_payment_reported', f'account={aid}; cents={amount}')
            flash('Payment reported for admin review. Your balance changes after receipt is confirmed.')
            return finish('finance_account', aid=aid)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc))
            return redirect(url_for('finance_account', aid=aid))

    @app.route('/finances/notices/<int:nid>/review', methods=['POST'])
    @role_required('admin')
    def finance_review(nid):
        n = db.session.get(PaymentNotice, nid)
        if not n:
            abort(404)
        aid = n.account_id
        try:
            decision = request.form.get('decision')
            note = request.form.get('review_note', '').strip()[:500]
            reference = request.form.get('reference', '').strip()[:160]
            if decision not in {'confirmed', 'rejected'} or (decision == 'confirmed' and not reference) or (decision == 'rejected' and not note):
                raise ValueError('Confirm with the actual Venmo transaction ID, or give a reason for declining.')
            # Conditional update plus unique operation key prevents double credit on concurrent reviews.
            changed = PaymentNotice.query.filter_by(id=nid, status='pending').update({'status': decision, 'reviewed_at': datetime.utcnow(), 'reviewed_by': current_user().id, 'review_note': note}, synchronize_session=False)
            if not changed:
                db.session.rollback()
                flash('This payment has already been reviewed.')
                return redirect(url_for('finance_account', aid=aid))
            if decision == 'confirmed':
                db.session.add(FinanceEntry(account_id=aid, kind='sponsor' if n.sponsor else 'payment', cents=-n.cents,
                    description='Confirmed Venmo payment', method='Venmo', reference=reference, reference_key=f'Venmo:{key(reference)}',
                    sponsor=n.sponsor, created_by=current_user().id, operation_key=f'notice:{nid}'))
            log('finance_payment_reviewed', f'notice={nid}; decision={decision}')
            flash('Payment review saved.')
            return finish('finance_account', aid=aid)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc))
            return redirect(url_for('finance_account', aid=aid))

    @app.route('/finances/entries/<int:eid>/reverse', methods=['POST'])
    @role_required('admin')
    def finance_reverse(eid):
        e = db.session.get(FinanceEntry, eid)
        if not e:
            abort(404)
        reason = request.form.get('reason', '').strip()[:450]
        if not reason or e.reversal_of or FinanceEntry.query.filter_by(reversal_of=eid).first():
            flash('Enter a reason. An entry can only be reversed once; reversals cannot be reversed.')
            return redirect(url_for('finance_account', aid=e.account_id))
        db.session.add(FinanceEntry(account_id=e.account_id, kind='reversal', cents=-e.cents, description=f'Correction of entry #{eid}: {reason}',
            created_by=current_user().id, operation_key=f'reverse:{eid}', reversal_of=eid))
        # Keep the original reference in history, but permit correcting its account assignment.
        e.reference_key = None
        log('finance_entry_reversed', f'entry={eid}; reason={reason}')
        flash('Correction recorded. The original entry remains in the history. No money was moved in Venmo.')
        return finish('finance_account', aid=e.account_id)

    return FinanceSetting, FinanceAccount, FinanceEntry, PaymentNotice
