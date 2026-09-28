import secrets
import unittest
from datetime import date
from test_app import PortalFlowTests, portal
from finances import cents, venmo_username


class FinanceTests(unittest.TestCase):
    setUp = PortalFlowTests.setUp
    login_as = PortalFlowTests.login_as

    def prepare(self):
        with portal.app.app_context():
            parent = portal.User(role='parent', name='Parent', email='family@example.com', password_hash='x')
            other = portal.User(role='parent', name='Other parent', email='otherparent@example.com', password_hash='x')
            portal.db.session.add_all([parent, other]); portal.db.session.flush()
            player = portal.User(role='player', name='Boone Soper', email='boone@example.com', password_hash='x', parent_id=parent.id)
            portal.db.session.add(player); portal.db.session.flush()
            portal.db.session.add(portal.TeamMembership(team_id=self.team_id, user_id=player.id, role='player'))
            portal.db.session.add(portal.RosterProfile(team_id=self.team_id, name='Boone Soper', name_key='boone soper', season='Fall 2026', as_of=date(2026,9,17)))
            portal.db.session.commit()
            self.parent_id, self.other_parent_id, self.player_id = parent.id, other.id, player.id
        self.login_as(self.admin_id)
        self.setup_accounts()
        with portal.app.app_context():
            self.aid = portal.FinanceAccount.query.one().id

    def setup_accounts(self, **overrides):
        data = dict(team_ids=[self.team_id], season='Fall 2026', dues='1300.00', discount='100.00')
        data.update(overrides)
        return self.client.post('/finances/setup', data=data, follow_redirects=True)

    def balance(self):
        with portal.app.app_context():
            return sum(e.cents for e in portal.FinanceEntry.query.filter_by(account_id=self.aid).all())

    def entry(self, **overrides):
        data = dict(kind='sponsor', amount='200.00', sponsor='Local Company', method='Venmo', reference='tx-1', description='Fall sponsorship', operation_key=secrets.token_hex(16))
        data.update(overrides)
        return self.client.post(f'/finances/{self.aid}/entry', data=data, follow_redirects=True)

    def report(self, **overrides):
        data=dict(amount='150.00', reference='paid Sept 17', operation_key=secrets.token_hex(16))
        data.update(overrides)
        return self.client.post(f'/finances/{self.aid}/report', data=data, follow_redirects=True)

    def test_season_setup_matches_roster_player_and_is_idempotent(self):
        self.prepare()
        self.assertEqual(120000, self.balance())
        self.setup_accounts()
        self.setup_accounts(dues='1400', discount='150')
        with portal.app.app_context():
            self.assertEqual(1, portal.FinanceAccount.query.count())
            self.assertEqual(2, portal.FinanceEntry.query.count())
            self.assertIsNotNone(portal.FinanceAccount.query.one().player_id)
        self.assertEqual(120000, self.balance())
        self.assertIn(b'$1,200.00', self.client.get('/finances').data)

    def test_season_setup_repairs_roster_names_without_losing_ledger(self):
        self.prepare()
        with portal.app.app_context():
            account = portal.FinanceAccount.query.one()
            account.name = 'Boone'
            account.name_key = 'boone'
            account_id = account.id
            portal.db.session.commit()
        response = self.setup_accounts()
        with portal.app.app_context():
            account = portal.FinanceAccount.query.one()
            self.assertEqual(account_id, account.id)
            self.assertEqual('Boone Soper', account.name)
            self.assertEqual('boone soper', account.name_key)
            self.assertEqual(2, portal.FinanceEntry.query.filter_by(account_id=account.id).count())
        self.assertIn(b'0 new player accounts prepared and 1 existing names updated', response.data)

    def test_season_setup_repairs_misspelled_name_by_unique_last_name(self):
        self.prepare()
        with portal.app.app_context():
            account = portal.FinanceAccount.query.one()
            account.name = 'Boon Soper'
            account.name_key = 'boon soper'
            portal.db.session.commit()
        self.setup_accounts()
        with portal.app.app_context():
            self.assertEqual('Boone Soper', portal.FinanceAccount.query.one().name)

    def test_parent_isolation_and_admin_only_writes(self):
        self.prepare()
        self.login_as(self.other_parent_id)
        self.assertNotIn(b'Boone Soper', self.client.get('/finances').data)
        for path in [f'/finances/{self.aid}', f'/finances/{self.aid}/report']:
            response=self.client.get(path) if path.endswith(str(self.aid)) else self.client.post(path)
            self.assertEqual(403, response.status_code)
        self.login_as(self.parent_id)
        self.assertIn(b'Boone Soper', self.client.get('/finances').data)
        self.assertEqual(200, self.client.get(f'/finances/{self.aid}').status_code)
        self.assertNotIn(b'Link family access', self.client.get(f'/finances/{self.aid}').data)
        for path in ['/finances/setup', '/finances/settings', f'/finances/{self.aid}/entry', f'/finances/{self.aid}/link']:
            self.assertEqual(302, self.client.post(path).status_code)
        self.assertEqual(120000, self.balance())
        self.login_as(self.coach_id)
        self.assertEqual(302, self.client.get('/finances').status_code)

    def test_linked_player_can_view_only_their_own_statement(self):
        self.prepare()
        with portal.app.app_context():
            other_account = portal.FinanceAccount(team_id=self.team_id, name='Other Player', name_key='other player', season='Fall 2026')
            portal.db.session.add(other_account)
            portal.db.session.commit()
            other_account_id = other_account.id
        self.login_as(self.player_id)
        page = self.client.get('/finances')
        self.assertEqual(200, page.status_code)
        self.assertIn(b'Boone Soper', page.data)
        self.assertNotIn(b'Other Player', page.data)
        self.assertEqual(200, self.client.get(f'/finances/{self.aid}').status_code)
        self.assertEqual(403, self.client.get(f'/finances/{other_account_id}').status_code)
        self.assertIn(b'Payments', self.client.get('/dashboard').data)

    def test_sponsor_credit_duplicate_reference_and_reversal(self):
        self.prepare()
        operation=secrets.token_hex(16)
        self.assertIn(b'Local Company', self.entry(operation_key=operation).data)
        self.assertEqual(100000, self.balance())
        self.entry(operation_key=operation)
        self.entry()
        self.assertEqual(100000, self.balance())
        with portal.app.app_context():
            eid=portal.FinanceEntry.query.filter_by(kind='sponsor').one().id
        self.client.post(f'/finances/entries/{eid}/reverse', data={'reason':'Incorrect account'})
        self.client.post(f'/finances/entries/{eid}/reverse', data={'reason':'Double click'})
        self.assertEqual(120000, self.balance())
        with portal.app.app_context():
            self.assertEqual(1, portal.FinanceEntry.query.filter_by(reversal_of=eid).count())
            self.assertIsNotNone(portal.db.session.get(portal.FinanceEntry,eid))
        self.entry(reference='tx-1', description='Corrected sponsorship')
        self.assertEqual(100000, self.balance())

    def test_pending_report_only_credits_once_after_admin_confirmation(self):
        self.prepare()
        self.login_as(self.parent_id)
        operation=secrets.token_hex(16)
        self.report(operation_key=operation, sponsor='Sponsor Inc')
        self.report(operation_key=operation, sponsor='Sponsor Inc')
        self.assertEqual(120000,self.balance())
        with portal.app.app_context():
            self.assertEqual(1,portal.PaymentNotice.query.count())
            nid=portal.PaymentNotice.query.one().id
        self.client.post(f'/finances/notices/{nid}/review',data={'decision':'confirmed','reference':'abc'})
        self.assertEqual(120000,self.balance())
        self.login_as(self.admin_id)
        for _ in range(2):
            self.client.post(f'/finances/notices/{nid}/review',data={'decision':'confirmed','reference':'abc'})
        self.assertEqual(105000,self.balance())
        with portal.app.app_context():
            self.assertEqual('confirmed',portal.PaymentNotice.query.one().status)
            self.assertEqual('Sponsor Inc',portal.FinanceEntry.query.filter_by(kind='sponsor').one().sponsor)

    def test_duplicate_transaction_rolls_back_review_and_decline_never_credits(self):
        self.prepare()
        self.entry(reference='abc')
        self.login_as(self.parent_id)
        self.report()
        with portal.app.app_context(): nid=portal.PaymentNotice.query.one().id
        self.login_as(self.admin_id)
        self.client.post(f'/finances/notices/{nid}/review',data={'decision':'confirmed','reference':'abc'})
        with portal.app.app_context(): self.assertEqual('pending',portal.PaymentNotice.query.one().status)
        self.client.post(f'/finances/notices/{nid}/review',data={'decision':'rejected','review_note':'Already credited'})
        self.assertEqual(100000,self.balance())
        with portal.app.app_context(): self.assertEqual('rejected',portal.PaymentNotice.query.one().status)

    def test_validation_exact_cents_and_venmo_url(self):
        self.prepare()
        for invalid in ['NaN','Infinity','-10','1.001','0','1000001']:
            with self.assertRaises(ValueError): cents(invalid)
            self.entry(amount=invalid)
        self.entry(sponsor='')
        self.entry(reference='')
        self.assertEqual(120000,self.balance())
        self.assertEqual(130001,cents('1300.01'))
        self.assertEqual('theTshirtguy13',venmo_username('@theTshirtguy13'))
        for invalid in ['https://evil.example/u/testuser','https://venmo.com.evil.example/u/testuser','javascript:alert(1)','https://venmo.com/u/testuser?redirect=evil']:
            with self.assertRaises(ValueError): venmo_username(invalid)
        self.client.post('/finances/settings',data={'venmo':'@theTshirtguy13'})
        page=self.client.get(f'/finances/{self.aid}').data
        self.assertIn(b'https://venmo.com/u/theTshirtguy13',page)
        self.assertNotIn(b'paycharge',page)

    def test_parent_link_grants_only_selected_account(self):
        self.prepare()
        self.client.post(f'/finances/{self.aid}/link',data={'parent_id':self.other_parent_id})
        self.login_as(self.other_parent_id)
        self.assertEqual(200,self.client.get(f'/finances/{self.aid}').status_code)
        self.login_as(self.parent_id)
        self.assertEqual(403,self.client.get(f'/finances/{self.aid}').status_code)

    def test_financial_posts_require_csrf(self):
        self.prepare()
        portal.app.config['WTF_CSRF_ENABLED']=True
        try:
            self.assertEqual(400,self.client.post('/finances/setup').status_code)
            self.assertEqual(400,self.client.post(f'/finances/{self.aid}/entry').status_code)
        finally:
            portal.app.config['WTF_CSRF_ENABLED']=False


if __name__ == '__main__': unittest.main()
