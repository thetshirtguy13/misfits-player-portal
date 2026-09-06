import os
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"

import app as portal


class PortalFlowTests(unittest.TestCase):
    def setUp(self):
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = portal.app.test_client()
        with portal.app.app_context():
            portal.db.drop_all()
            portal.db.create_all()
            admin = portal.User(role="admin", name="Admin", email="admin@example.com", password_hash="x", consent_verified=True)
            coach = portal.User(role="coach", name="Coach", email="coach@example.com", password_hash="x", consent_verified=True)
            other_coach = portal.User(role="coach", name="Other", email="other@example.com", password_hash="x", consent_verified=True)
            portal.db.session.add_all([admin, coach, other_coach])
            portal.db.session.flush()
            org = portal.Organization(name="Misfits", owner_id=coach.id)
            portal.db.session.add(org)
            portal.db.session.flush()
            team = portal.Team(organization_id=org.id, name="12U Blue", age_group="12U", join_code="TEAM12")
            other_team = portal.Team(organization_id=org.id, name="13U Gold", age_group="13U", join_code="TEAM13")
            portal.db.session.add_all([team, other_team])
            portal.db.session.flush()
            portal.db.session.add(portal.TeamMembership(team_id=team.id, user_id=coach.id, role="coach"))
            portal.db.session.add(portal.TeamMembership(team_id=other_team.id, user_id=other_coach.id, role="coach"))
            portal.db.session.commit()
            self.admin_id, self.coach_id = admin.id, coach.id
            self.team_id, self.other_team_id = team.id, other_team.id

    def login_as(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def register(self, **overrides):
        data = {
            "role": "player",
            "name": "Player One",
            "email": "player@example.com",
            "password": "long-password",
            "join_code": "TEAM12",
            "parent_email": "parent@example.com",
            "age_group": "12U",
            "position": "Pitcher",
        }
        data.update(overrides)
        with patch.object(portal, "send_email", return_value=True):
            return self.client.post("/register", data=data, follow_redirects=True)

    def test_player_registration_requires_valid_team_code(self):
        response = self.register(join_code="BADCODE")
        self.assertIn(b"valid team join code", response.data)
        with portal.app.app_context():
            self.assertIsNone(portal.User.query.filter_by(email="player@example.com").first())

    def test_consent_approves_membership_and_late_parent_signup_links_player(self):
        self.register()
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            membership = portal.TeamMembership.query.filter_by(user_id=player.id).one()
            request = portal.ConsentRequest.query.filter_by(player_id=player.id).one()
            self.assertFalse(membership.approved)
            token = portal.token_for("consent", {"cid": request.id, "nonce": request.token_nonce})

        self.client.post(f"/parent-consent/{token}")
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            self.assertTrue(player.consent_verified)
            self.assertTrue(portal.TeamMembership.query.filter_by(user_id=player.id).one().approved)
            self.assertIsNone(player.parent_id)

        self.client.get("/logout")
        self.register(role="parent", name="Parent One", email="parent@example.com", join_code="", parent_email="")
        with portal.app.app_context():
            parent = portal.User.query.filter_by(email="parent@example.com").one()
            player = portal.User.query.filter_by(email="player@example.com").one()
            self.assertEqual(parent.id, player.parent_id)

    def test_existing_parent_links_when_consent_is_approved(self):
        self.register(role="parent", name="Parent One", email="parent@example.com", join_code="", parent_email="")
        self.client.get("/logout")
        self.register()
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            request = portal.ConsentRequest.query.filter_by(player_id=player.id).one()
            token = portal.token_for("consent", {"cid": request.id, "nonce": request.token_nonce})
        self.client.post(f"/parent-consent/{token}")
        with portal.app.app_context():
            parent = portal.User.query.filter_by(email="parent@example.com").one()
            player = portal.User.query.filter_by(email="player@example.com").one()
            self.assertEqual(parent.id, player.parent_id)

        self.login_as(parent.id)
        with patch.dict(os.environ, {"S3_BUCKET": "test-bucket"}):
            self.assertIn(b"Player One", self.client.get("/media").data)

    def test_admin_sees_all_teams_and_players(self):
        self.register()
        self.login_as(self.admin_id)
        teams = self.client.get("/teams")
        players = self.client.get("/coach")
        admin = self.client.get("/admin")
        self.assertIn(b"12U Blue", teams.data)
        self.assertIn(b"13U Gold", teams.data)
        self.assertIn(b"Player One", players.data)
        self.assertIn(b"Player Account Administration", admin.data)
        self.assertIn(b"parent@example.com", admin.data)
        self.assertIn(b">Players<", self.client.get("/dashboard").data)

    def test_admin_can_assign_remove_and_disable_player(self):
        self.register()
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            player.consent_verified = True
            portal.db.session.commit()
            player_id = player.id
        self.login_as(self.admin_id)
        self.client.post(f"/admin/player/{player_id}/team", data={"team_id": self.other_team_id})
        with portal.app.app_context():
            membership = portal.TeamMembership.query.filter_by(team_id=self.other_team_id, user_id=player_id).one()
            self.assertTrue(membership.approved)
        self.client.post(f"/admin/player/{player_id}/status", data={"status": "deactivate"})
        with portal.app.app_context():
            self.assertFalse(portal.db.session.get(portal.User, player_id).is_active)
        self.client.post(f"/admin/player/{player_id}/team/{self.other_team_id}/remove")
        with portal.app.app_context():
            self.assertIsNone(portal.TeamMembership.query.filter_by(team_id=self.other_team_id, user_id=player_id).first())

    def test_admin_deletion_requires_name_and_removes_player_account(self):
        self.register()
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            player_id = player.id
            portal.db.session.add(portal.GameStat(player_id=player_id, opponent="Test"))
            portal.db.session.commit()
        self.login_as(self.admin_id)
        response = self.client.post(f"/admin/player/{player_id}/delete", data={"confirm_name": "wrong"}, follow_redirects=True)
        self.assertIn(b"exact name", response.data)
        with portal.app.app_context():
            self.assertIsNotNone(portal.db.session.get(portal.User, player_id))
        self.client.post(f"/admin/player/{player_id}/delete", data={"confirm_name": "Player One"})
        with portal.app.app_context():
            self.assertIsNone(portal.db.session.get(portal.User, player_id))
            self.assertEqual(0, portal.GameStat.query.filter_by(player_id=player_id).count())

    def test_coach_cannot_open_admin_workspace(self):
        self.login_as(self.coach_id)
        response = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))

    def test_registered_player_can_login_and_join_another_team(self):
        self.register()
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            request = portal.ConsentRequest.query.filter_by(player_id=player.id).one()
            token = portal.token_for("consent", {"cid": request.id, "nonce": request.token_nonce})
        self.client.post(f"/parent-consent/{token}")
        self.client.post("/logout")
        response = self.client.post(
            "/login",
            data={"email": "player@example.com", "password": "long-password"},
            follow_redirects=True,
        )
        self.assertIn(b"Welcome, Player One", response.data)
        self.client.post("/join-team", data={"join_code": "TEAM13"})
        with portal.app.app_context():
            player = portal.User.query.filter_by(email="player@example.com").one()
            membership = portal.TeamMembership.query.filter_by(team_id=self.other_team_id, user_id=player.id).one()
            self.assertTrue(membership.approved)

    def test_legacy_add_player_endpoint_does_not_create_account(self):
        self.login_as(self.coach_id)
        response = self.client.post(
            f"/teams/{self.team_id}/add-player",
            data={"name": "Manual Player", "email": "manual@example.com"},
            follow_redirects=True,
        )
        self.assertIn(b"create their own accounts", response.data)
        with portal.app.app_context():
            self.assertIsNone(portal.User.query.filter_by(email="manual@example.com").first())

    def test_resend_request_keeps_required_user_agent(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch.dict(os.environ, {"RESEND_API_KEY": "test-key"}), patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            self.assertTrue(portal.send_email("user@example.com", "Subject", "Body"))
        request = urlopen.call_args.args[0]
        self.assertEqual("misfits-player-development/1.0", request.get_header("User-agent"))


if __name__ == "__main__":
    unittest.main()
