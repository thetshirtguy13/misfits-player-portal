import unittest
from datetime import date
from unittest.mock import patch
from test_app import PortalFlowTests, portal


class FamilyAccessTests(unittest.TestCase):
    def setUp(self):
        PortalFlowTests.setUp(self)
        portal.limiter.reset()

    def tearDown(self):
        portal.limiter.reset()
    login_as = PortalFlowTests.login_as

    def make_family(self):
        with portal.app.app_context():
            parent = portal.User(role='parent', name='Parent', email='parent@example.com', password_hash='x', consent_verified=True)
            other = portal.User(role='parent', name='Other parent', email='second@example.com', password_hash='x', consent_verified=True)
            profile = portal.RosterProfile(team_id=self.team_id, name='Roster Child', name_key='roster child', season='Fall 2026', as_of=date(2026, 9, 17), stats={}, notes='Private coach note')
            portal.db.session.add_all([parent, other, profile])
            portal.db.session.commit()
            return parent.id, other.id, profile.id

    def test_roster_link_access_is_scoped_and_idempotent(self):
        parent, other, profile = self.make_family()
        self.login_as(self.admin_id)
        for _ in range(2):
            response = self.client.post(f'/admin/families/{parent}/link', data={'profile_id':profile}, follow_redirects=True)
            self.assertIn(b'now visible under My Players', response.data)
        with portal.app.app_context():
            self.assertEqual(portal.RosterFamilyLink.query.count(), 1)
            self.assertEqual(portal.User.query.count(), 5)
            self.assertEqual(portal.FinanceEntry.query.count(), 0)
        self.login_as(parent)
        for route in ['/dashboard','/my-players',f'/family-roster/{profile}']:
            response = self.client.get(route)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Roster Child',response.data)
            self.assertNotIn(b'Private coach note',response.data)
            self.assertNotIn(b'No players are linked yet',response.data)
        self.assertNotEqual(self.client.post(f'/admin/families/{other}/link',data={'profile_id':profile}).status_code,200)
        self.login_as(other)
        self.assertEqual(self.client.get(f'/family-roster/{profile}').status_code,403)
        self.login_as(self.coach_id)
        self.assertEqual(self.client.get(f'/family-roster/{profile}').status_code,200)
        with portal.app.app_context():
            other_coach=portal.User.query.filter_by(email='other@example.com').one().id
        self.login_as(other_coach)
        self.assertEqual(self.client.get(f'/family-roster/{profile}').status_code,403)

    def test_admin_resend_uses_parent_email_without_marking_verified(self):
        parent, other, profile = self.make_family()
        self.login_as(self.admin_id)
        with patch.object(portal,'send_email',return_value=True) as mail:
            response=self.client.post(f'/admin/families/{parent}/verification',follow_redirects=True)
        self.assertIn(b'Verification email submitted to parent@example.com',response.data)
        self.assertEqual(mail.call_args.args[0],'parent@example.com')
        with portal.app.app_context():
            self.assertFalse(portal.db.session.get(portal.User,parent).email_verified)
        with patch.object(portal,'send_email',return_value=False):
            response=self.client.post(f'/admin/families/{parent}/verification',follow_redirects=True)
        self.assertIn(b'We could not send the verification email',response.data)
        self.assertNotIn(b'Development verification link',response.data)
        self.login_as(other)
        with patch.object(portal,'send_email') as mail:
            self.client.post(f'/admin/families/{parent}/verification')
            mail.assert_not_called()

    def test_registration_keeps_delivery_failure_message(self):
        with patch.object(portal,'send_email',return_value=False):
            response=self.client.post('/register',data={'role':'parent','name':'New Parent','email':'new@example.com','password':'long-password'},follow_redirects=True)
        self.assertIn(b'We could not send the verification email',response.data)
        self.assertNotIn(b'Development verification link',response.data)

    def test_parent_resend_reports_failure_and_verified_is_noop(self):
        parent, other, profile=self.make_family()
        self.login_as(parent)
        with patch.object(portal,'send_email',return_value=False):
            response=self.client.post('/resend-verification',follow_redirects=True)
        self.assertIn(b'We could not send the verification email',response.data)
        self.assertNotIn(b'Verification message sent',response.data)
        with portal.app.app_context():
            portal.db.session.get(portal.User,parent).email_verified=True
            portal.db.session.commit()
        with patch.object(portal,'send_email') as mail:
            response=self.client.post('/resend-verification',follow_redirects=True)
            mail.assert_not_called()
        self.assertIn(b'already verified',response.data)

    def test_parent_registration_selects_existing_roster_player(self):
        with portal.app.app_context():
            profile = portal.RosterProfile(team_id=self.team_id, name='Roster Child', name_key='roster child', season='Fall 2026', as_of=date(2026, 9, 17), stats={})
            portal.db.session.add(profile)
            portal.db.session.commit()
            profile_id = profile.id

        page = self.client.get('/register')
        self.assertIn(b'Roster Child', page.data)
        self.assertIn(b'Parent or guardian', page.data)
        self.assertNotIn(b'<option value="player">', page.data)

        data = {'role':'parent','name':'New Parent','email':'new@example.com','password':'long-password','profile_id':profile_id,'join_code':'BADCODE'}
        response = self.client.post('/register', data=data, follow_redirects=True)
        self.assertIn(b'does not match', response.data)
        with portal.app.app_context():
            self.assertIsNone(portal.User.query.filter_by(email='new@example.com').first())

        data['join_code'] = 'TEAM12'
        with patch.object(portal, 'send_email', return_value=True):
            response = self.client.post('/register', data=data, follow_redirects=True)
        self.assertIn(b'Welcome, New Parent', response.data)
        self.assertIn(b'Roster Child', response.data)
        with portal.app.app_context():
            parent = portal.User.query.filter_by(email='new@example.com').one()
            link = portal.RosterFamilyLink.query.filter_by(parent_id=parent.id, profile_id=profile_id).one()
            self.assertIsNotNone(link)
            self.assertEqual(0, portal.User.query.filter_by(role='player').count())
            self.assertIn(self.team_id, portal.portal_team_ids_for(parent))

        self.assertIn(b'12U Blue', self.client.get('/calendar').data)
        self.assertIn(b'12U Blue', self.client.get('/chats').data)

    def test_registration_uses_latest_profile_once(self):
        with portal.app.app_context():
            portal.db.session.add_all([
                portal.RosterProfile(team_id=self.team_id, name='Roster Child', name_key='roster child', season='Spring 2026', as_of=date(2026, 5, 1), stats={}),
                portal.RosterProfile(team_id=self.team_id, name='Roster Child', name_key='roster child', season='Fall 2026', as_of=date(2026, 9, 17), stats={}),
            ])
            portal.db.session.commit()
        page = self.client.get('/register').data
        self.assertEqual(1, page.count(b'Roster Child'))


if __name__=='__main__':
    unittest.main()
