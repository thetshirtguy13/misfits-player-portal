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

    def test_registration_includes_10u_roster_players(self):
        with portal.app.app_context():
            organization_id = portal.db.session.get(portal.Team, self.team_id).organization_id
            team = portal.Team(organization_id=organization_id, name='10U Black', age_group='10U', join_code='TEAM10')
            portal.db.session.add(team)
            portal.db.session.flush()
            profile = portal.RosterProfile(team_id=team.id, name='Ten U Child', name_key='ten u child', season='Fall 2026', as_of=date(2026, 9, 17), stats={})
            portal.db.session.add(profile)
            portal.db.session.commit()
        page = self.client.get('/register').data
        self.assertIn(b'10U', page)
        self.assertIn(b'Ten U Child', page)

    def test_registration_hides_claimed_player_across_roster_snapshots(self):
        with portal.app.app_context():
            parent = portal.User(role='parent', name='Existing Parent', email='existing@example.com', password_hash='x', consent_verified=True)
            old_profile = portal.RosterProfile(team_id=self.team_id, name='Claimed Child', name_key='claimed child', season='Spring 2026', as_of=date(2026, 5, 1), stats={})
            new_profile = portal.RosterProfile(team_id=self.team_id, name='Claimed Child', name_key='claimed child', season='Fall 2026', as_of=date(2026, 9, 17), stats={})
            portal.db.session.add_all([parent, old_profile, new_profile])
            portal.db.session.flush()
            portal.db.session.add(portal.RosterFamilyLink(parent_id=parent.id, profile_id=old_profile.id))
            portal.db.session.commit()
            parent_id, old_profile_id, new_profile_id = parent.id, old_profile.id, new_profile.id

        page = self.client.get('/register').data
        self.assertNotIn(b'Claimed Child', page)

        stale_choices = lambda: [(portal.db.session.get(portal.RosterProfile, new_profile_id), portal.db.session.get(portal.Team, self.team_id))]
        with patch.object(portal, 'registration_player_choices', side_effect=stale_choices):
            response = self.client.post('/register', data={'role':'parent','name':'Second Parent','email':'second-parent@example.com','password':'long-password','profile_id':new_profile_id,'join_code':'TEAM12'}, follow_redirects=True)
        self.assertIn(b'already linked to a family account', response.data)
        with portal.app.app_context():
            self.assertIsNone(portal.User.query.filter_by(email='second-parent@example.com').first())
            links = portal.RosterFamilyLink.query.all()
            self.assertEqual([(parent_id, old_profile_id)], [(link.parent_id, link.profile_id) for link in links])

    def test_admin_roster_links_player_login_without_changing_parent_claim(self):
        parent_id, other_id, profile_id = self.make_family()
        with portal.app.app_context():
            player = portal.User(role='player', name='Tucker Ridgway', email='tucker@example.com', password_hash='x', consent_verified=True)
            portal.db.session.add(player)
            portal.db.session.flush()
            portal.db.session.add(portal.RosterFamilyLink(parent_id=parent_id, profile_id=profile_id))
            portal.db.session.commit()
            player_id = player.id

        self.login_as(self.admin_id)
        page = self.client.get(f'/admin/rosters?team_id={self.team_id}')
        self.assertIn(b'Roster Child', page.data)
        self.assertIn(b'No player login', page.data)
        self.assertIn(b'Parent', page.data)

        response = self.client.post(f'/admin/rosters/{profile_id}/player-account', data={'player_id':player_id}, follow_redirects=True)
        self.assertIn(b'now linked to Tucker Ridgway', response.data)
        self.assertIn(b'tucker@example.com', response.data)
        with portal.app.app_context():
            self.assertEqual(player_id, portal.RosterPlayerLink.query.filter_by(profile_id=profile_id).one().player_id)
            membership = portal.TeamMembership.query.filter_by(team_id=self.team_id, user_id=player_id).one()
            self.assertTrue(membership.approved)
            self.assertEqual(1, portal.RosterFamilyLink.query.filter_by(parent_id=parent_id, profile_id=profile_id).count())

        self.login_as(player_id)
        self.assertEqual(200, self.client.get(f'/family-roster/{profile_id}').status_code)
        self.assertIn(b'Roster Child', self.client.get('/dashboard').data)

    def test_admin_rosters_tab_shows_every_team_and_flags_duplicate_names(self):
        with portal.app.app_context():
            profiles = [
                portal.RosterProfile(team_id=self.team_id, name='Shared Player', name_key='shared player', season='Fall 2026', as_of=date(2026, 9, 17), stats={}),
                portal.RosterProfile(team_id=self.other_team_id, name='Shared Player', name_key='shared player', season='Fall 2026', as_of=date(2026, 9, 17), stats={}),
            ]
            portal.db.session.add_all(profiles)
            portal.db.session.commit()

        self.login_as(self.admin_id)
        page = self.client.get(f'/admin/rosters?team_id={self.team_id}')
        self.assertEqual(200, page.status_code)
        self.assertIn(b'Rosters', page.data)
        self.assertIn(b'12U Blue', page.data)
        self.assertIn(b'13U Gold', page.data)
        self.assertEqual(2, page.data.count(b'Possible duplicate</span>'))
        self.assertIn(b'href="/admin/rosters"', page.data)

    def test_admin_roster_name_update_applies_to_every_snapshot(self):
        with portal.app.app_context():
            profiles = [
                portal.RosterProfile(team_id=self.team_id, name='Tucker', name_key='tucker', season='Spring 2026', as_of=date(2026, 5, 1), stats={}),
                portal.RosterProfile(team_id=self.team_id, name='Tucker', name_key='tucker', season='Fall 2026', as_of=date(2026, 9, 17), stats={}),
            ]
            portal.db.session.add_all(profiles)
            portal.db.session.commit()
            profile_id = profiles[-1].id
        self.login_as(self.admin_id)
        response = self.client.post(f'/admin/rosters/{profile_id}/name', data={'name':'Tucker Ridgway','jersey':'25'}, follow_redirects=True)
        self.assertIn(b'Roster profile updated for Tucker Ridgway', response.data)
        with portal.app.app_context():
            updated = portal.RosterProfile.query.filter_by(team_id=self.team_id).all()
            self.assertEqual({'Tucker Ridgway'}, {profile.name for profile in updated})
            self.assertEqual({'tucker ridgway'}, {profile.name_key for profile in updated})
            self.assertEqual({'25'}, {profile.jersey for profile in updated})

    def test_admin_can_link_coach_account_to_family_roster(self):
        with portal.app.app_context():
            coach = portal.User(role='coach', name='Coach Parent', email='coach-parent@example.com', password_hash='x', is_active=True)
            profile = portal.RosterProfile(team_id=self.other_team_id, name='Coach Child', name_key='coach child', season='Fall 2026', as_of=date(2026, 9, 17), stats={})
            portal.db.session.add_all([coach, profile])
            portal.db.session.commit()
            coach_id, profile_id = coach.id, profile.id

        self.login_as(self.admin_id)
        page = self.client.get('/admin/families')
        self.assertIn(b'Coach Parent', page.data)
        response = self.client.post(f'/admin/families/{coach_id}/link', data={'profile_id':profile_id}, follow_redirects=True)
        self.assertIn(b'Coach Child is now linked to Coach Parent', response.data)

        self.login_as(coach_id)
        self.assertEqual(200, self.client.get('/my-players').status_code)
        self.assertEqual(200, self.client.get(f'/family-roster/{profile_id}').status_code)
        self.assertIn(b'Coach Child', self.client.get('/dashboard').data)
        with portal.app.app_context():
            self.assertIn(self.other_team_id, portal.portal_team_ids_for(portal.db.session.get(portal.User, coach_id)))

    def test_admin_can_add_missing_roster_player(self):
        self.login_as(self.admin_id)
        response = self.client.post('/admin/rosters/add', data={'team_id':self.team_id,'name':'Brayden Brett','jersey':'','season':'Fall 2026','as_of':'2026-09-17'}, follow_redirects=True)
        self.assertIn(b'Brayden Brett added to 12U Blue', response.data)
        with portal.app.app_context():
            profile = portal.RosterProfile.query.filter_by(team_id=self.team_id, name_key='brayden brett').one()
            self.assertEqual('Fall 2026', profile.season)
            self.assertEqual(date(2026, 9, 17), profile.as_of)

    def test_linking_full_player_account_completes_first_only_roster_name(self):
        with portal.app.app_context():
            profile = portal.RosterProfile(team_id=self.team_id, name='Tucker', name_key='tucker', season='Fall 2026', as_of=date(2026, 9, 17), stats={})
            player = portal.User(role='player', name='Tucker Ridgway', email='tucker@example.com', password_hash='x', consent_verified=True)
            portal.db.session.add_all([profile, player])
            portal.db.session.commit()
            profile_id, player_id = profile.id, player.id
        self.login_as(self.admin_id)
        self.client.post(f'/admin/rosters/{profile_id}/player-account', data={'player_id':player_id})
        with portal.app.app_context():
            profile = portal.db.session.get(portal.RosterProfile, profile_id)
            self.assertEqual('Tucker Ridgway', profile.name)
            self.assertEqual('tucker ridgway', profile.name_key)


if __name__=='__main__':
    unittest.main()
