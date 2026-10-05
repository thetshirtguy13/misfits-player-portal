import io
import os
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"

import app as portal


class PortalFlowTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ADMIN_EMAIL", None)
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
            portal.initialize_database()

    def test_database_initialization_is_not_repeated_per_request(self):
        with patch.object(portal, "initialize_database") as initialize:
            self.client.get("/login")
            self.client.get("/health")
        initialize.assert_not_called()

    def test_practice_drills_are_seeded_without_duplicates(self):
        with portal.app.app_context():
            self.assertEqual(42, len(portal.PRACTICE_DRILLS))
            self.assertIsNotNone(portal.Workout.query.filter_by(title="01. Four Steps to Throwing").first())
            self.assertIsNotNone(portal.Workout.query.filter_by(title="42. Low, Long Pitching Stride").first())
            original_count=portal.Workout.query.count()
            portal.initialize_database()
            self.assertEqual(original_count,portal.Workout.query.count())

    def test_admin_can_add_drill_and_coach_cannot(self):
        payload={"category":"Fielding","title":"Coach Demo Drill","level":"All ages","minutes":"12","exercises":"Ready position\nMove through the ball","video_url":"https://youtu.be/abc123"}
        self.login_as(self.coach_id)
        self.assertEqual(302,self.client.post("/workouts/add",data=payload).status_code)
        with portal.app.app_context(): self.assertIsNone(portal.Workout.query.filter_by(title="Coach Demo Drill").first())
        self.login_as(self.admin_id)
        response=self.client.post("/workouts/add",data=payload,follow_redirects=True)
        self.assertIn(b"Coach Demo Drill",response.data)
        self.assertIn(b"youtube-nocookie.com/embed/abc123",response.data)

    def test_admin_upload_associates_video_with_drill(self):
        self.login_as(self.admin_id)
        with portal.app.app_context(): workout=portal.Workout.query.filter_by(title="01. Four Steps to Throwing").one(); workout_id=workout.id
        fake_file=(io.BytesIO(b"video-data"),"throwing.mp4")
        with patch.dict(os.environ,{"S3_BUCKET":"test-bucket"}), patch.object(portal,"s3_client") as s3:
            response=self.client.post(f"/workout/{workout_id}/video",data={"media_file":fake_file},content_type="multipart/form-data",follow_redirects=True)
        self.assertIn(b"Drill video uploaded",response.data)
        s3.return_value.put_object.assert_called_once()
        with portal.app.app_context():
            media=portal.Media.query.filter_by(workout_id=workout_id).one()
            self.assertEqual("throwing.mp4",media.original_name)

    def test_team_store_displays_live_shopify_products_and_filters(self):
        product={"title":"Misfits Home Uniform","url":"https://thetshirtguy.co/products/home-uniform","image":"https://cdn.shopify.com/uniform.jpg","price":"$30.00","available":True,"category":"Uniforms"}
        self.login_as(self.admin_id)
        with patch.object(portal,"shop_products",return_value=([product],None)):
            response=self.client.get("/shop?category=Uniforms")
        self.assertEqual(200,response.status_code)
        self.assertIn(b"Misfits Team Store",response.data)
        self.assertIn(b"Misfits Home Uniform",response.data)
        self.assertIn(b"$30.00",response.data)
        self.assertIn(b"https://thetshirtguy.co/products/home-uniform",response.data)
        self.assertIn("https://cdn.shopify.com",response.headers["Content-Security-Policy"])
        self.assertIn(b"Team Store",self.client.get("/dashboard").data)

    def test_team_store_has_shopify_fallback(self):
        self.login_as(self.admin_id)
        with patch.object(portal,"shop_products",return_value=([],"Live product details are temporarily unavailable.")):
            response=self.client.get("/shop")
        self.assertIn(b"Shopify connection notice",response.data)
        self.assertIn(b"https://thetshirtguy.co/collections/misfits-baseball",response.data)

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
        self.assertIn(b"Account Administration", admin.data)
        self.assertIn(b"Coach Accounts", admin.data)
        self.assertIn(b"coach@example.com", admin.data)
        self.assertIn(b"parent@example.com", admin.data)

    def test_calendar_shows_seeded_events_and_navigation(self):
        self.login_as(self.admin_id)
        response = self.client.get("/calendar?year=2026&month=9")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Master Calendar", response.data)
        self.assertIn(b"12U Blue", response.data)
        self.assertIn(b"13U Gold", response.data)
        self.assertIn(b"Power Alley Practice", response.data)
        self.assertIn(b"5:45 PM", response.data)
        self.assertIn(b"Calendar", self.client.get("/dashboard").data)

    def test_admin_can_add_tournament_to_calendar(self):
        self.login_as(self.admin_id)
        response = self.client.post("/calendar", data={
            "team_id": self.team_id,
            "title": "Fall Classic",
            "event_type": "tournament",
            "starts_on": "2026-10-10",
            "start_time": "08:00",
            "end_time": "17:00",
        }, follow_redirects=True)
        self.assertIn(b"Fall Classic", response.data)
        duplicate = self.client.post("/calendar", data={
            "team_id": self.team_id,
            "title": "Fall Classic",
            "event_type": "tournament",
            "starts_on": "2026-10-10",
            "start_time": "08:00",
            "end_time": "17:00",
        }, follow_redirects=True)
        self.assertIn(b"already on the calendar", duplicate.data)
        other_team = self.client.post("/calendar", data={
            "team_id": self.other_team_id,
            "title": "Fall Classic",
            "event_type": "tournament",
            "starts_on": "2026-10-10",
            "start_time": "08:00",
            "end_time": "17:00",
        }, follow_redirects=True)
        self.assertIn(b"Fall Classic", other_team.data)
        with portal.app.app_context():
            self.assertEqual(2, portal.ScheduleEvent.query.filter_by(title="Fall Classic").count())

    def test_coach_cannot_add_calendar_event(self):
        self.login_as(self.coach_id)
        response = self.client.post("/calendar", data={
            "team_id": self.team_id,
            "title": "Unauthorized Event",
            "event_type": "game",
            "starts_on": "2026-10-10",
            "start_time": "08:00",
        }, follow_redirects=True)
        self.assertIn(b"Only administrators", response.data)
        with portal.app.app_context():
            self.assertIsNone(portal.ScheduleEvent.query.filter_by(title="Unauthorized Event").first())

    def test_team_calendar_isolated_by_membership(self):
        with portal.app.app_context():
            portal.db.session.add_all([
                portal.ScheduleEvent(team_id=self.team_id,title="Blue Practice",event_type="practice",starts_on=portal.date(2026,10,8),start_time=portal.datetime.strptime("17:00","%H:%M").time()),
                portal.ScheduleEvent(team_id=self.other_team_id,title="Gold Practice",event_type="practice",starts_on=portal.date(2026,10,8),start_time=portal.datetime.strptime("18:00","%H:%M").time()),
            ])
            portal.db.session.commit()
        self.login_as(self.coach_id)
        own=self.client.get(f"/calendar?team_id={self.team_id}&year=2026&month=10")
        blocked=self.client.get(f"/calendar?team_id={self.other_team_id}&year=2026&month=10",follow_redirects=True)
        self.assertIn(b"Blue Practice",own.data)
        self.assertNotIn(b"Gold Practice",own.data)
        self.assertIn(b"do not have access",blocked.data)

    def test_admin_master_calendar_uses_team_colors(self):
        self.login_as(self.admin_id)
        response=self.client.get("/calendar?team_id=all&year=2026&month=9")
        self.assertIn(b"Admin master schedule",response.data)
        self.assertIn(b"--team-color:",response.data)
        self.assertIn(b"Add Team Event",response.data)

    def test_team_chat_is_private_and_admin_can_join(self):
        self.login_as(self.coach_id)
        posted=self.client.post(f"/teams/{self.team_id}/chat",data={"body":"Practice moved to field two."},follow_redirects=True)
        self.assertIn(b"Practice moved to field two.",posted.data)
        blocked=self.client.get(f"/teams/{self.other_team_id}/chat",follow_redirects=True)
        self.assertIn(b"do not have access",blocked.data)
        self.login_as(self.admin_id)
        admin_room=self.client.get(f"/teams/{self.team_id}/chat")
        self.assertIn(b"Practice moved to field two.",admin_room.data)
        admin_post=self.client.post(f"/teams/{self.other_team_id}/chat",data={"body":"Admin is in every team room."},follow_redirects=True)
        self.assertIn(b"Admin is in every team room.",admin_post.data)
        self.assertIn(b"Admin",admin_post.data)

    def test_chat_message_delete_and_pin_permissions(self):
        self.login_as(self.coach_id)
        self.client.post(f"/teams/{self.team_id}/chat",data={"body":"Bring both uniforms."})
        with portal.app.app_context(): message=portal.ChatMessage.query.filter_by(body="Bring both uniforms.").one(); message_id=message.id
        pinned=self.client.post(f"/teams/{self.team_id}/chat/message/{message_id}/pin",follow_redirects=True)
        self.assertIn(b"Pinned Information",pinned.data)
        deleted=self.client.post(f"/teams/{self.team_id}/chat/message/{message_id}/delete",follow_redirects=True)
        self.assertIn(b"Message deleted",deleted.data)
        with portal.app.app_context():
            message=portal.db.session.get(portal.ChatMessage,message_id)
            self.assertTrue(message.is_deleted)
            self.assertIsNone(message.pinned_at)

    def test_chat_poll_allows_one_changeable_vote_per_user(self):
        self.login_as(self.coach_id)
        response=self.client.post(f"/teams/{self.team_id}/chat/poll",data={"question":"Which practice time?","options":"5 PM\n6 PM\n7 PM"},follow_redirects=True)
        self.assertIn(b"Which practice time?",response.data)
        with portal.app.app_context():
            message=portal.ChatMessage.query.filter_by(kind="poll").one()
            options=portal.ChatPollOption.query.filter_by(message_id=message.id).order_by(portal.ChatPollOption.position).all()
            message_id=message.id; first_id=options[0].id; second_id=options[1].id
        self.client.post(f"/teams/{self.team_id}/chat/poll/{message_id}/vote",data={"option_id":first_id})
        self.client.post(f"/teams/{self.team_id}/chat/poll/{message_id}/vote",data={"option_id":second_id})
        with portal.app.app_context():
            votes=portal.ChatPollVote.query.filter_by(message_id=message_id,user_id=self.coach_id).all()
            self.assertEqual(1,len(votes))
            self.assertEqual(second_id,votes[0].option_id)

    def test_new_chat_and_calendar_events_create_team_notifications(self):
        self.login_as(self.coach_id)
        self.client.post(f"/teams/{self.team_id}/chat",data={"body":"Saturday arrival is 8 AM."})
        with portal.app.app_context():
            admin_alert=portal.PortalNotification.query.filter_by(user_id=self.admin_id,kind="chat").one()
            self.assertIn("Saturday arrival",admin_alert.body)
        self.login_as(self.admin_id)
        self.client.post("/calendar",data={"team_id":self.team_id,"event_type":"practice","title":"Defense Practice","starts_on":"2026-10-12","start_time":"17:00","end_time":"19:00"})
        with portal.app.app_context():
            coach_alert=portal.PortalNotification.query.filter_by(user_id=self.coach_id,kind="calendar").one()
            self.assertIn("Defense Practice",coach_alert.title)

    def test_push_subscription_and_service_worker(self):
        self.login_as(self.coach_id)
        subscription={"endpoint":"https://push.example.test/abc","keys":{"p256dh":"public-key","auth":"auth-key"}}
        response=self.client.post("/push/subscribe",json=subscription)
        self.assertEqual(200,response.status_code)
        with portal.app.app_context():
            saved=portal.PushSubscription.query.filter_by(user_id=self.coach_id).one()
            self.assertEqual(subscription["endpoint"],saved.endpoint)
        worker=self.client.get("/service-worker.js")
        self.assertEqual(200,worker.status_code)
        self.assertEqual("/",worker.headers["Service-Worker-Allowed"])

    def test_only_admin_can_delete_entire_chat_thread(self):
        self.login_as(self.coach_id)
        self.client.post(f"/teams/{self.team_id}/chat",data={"body":"Permanent until thread deletion."})
        blocked=self.client.post(f"/teams/{self.team_id}/chat/delete",data={"confirm_name":"12U Blue"})
        self.assertEqual(302,blocked.status_code)
        with portal.app.app_context(): self.assertEqual(1,portal.ChatMessage.query.filter_by(team_id=self.team_id).count())
        self.login_as(self.admin_id)
        removed=self.client.post(f"/teams/{self.team_id}/chat/delete",data={"confirm_name":"12U Blue"},follow_redirects=True)
        self.assertIn(b"chat thread deleted",removed.data)
        with portal.app.app_context(): self.assertEqual(0,portal.ChatMessage.query.filter_by(team_id=self.team_id).count())

    def test_admin_can_assign_new_coach_to_team(self):
        with portal.app.app_context():
            new_coach=portal.User(role="coach",name="New Coach",email="newcoach@example.com",password_hash="x",consent_verified=True)
            portal.db.session.add(new_coach); portal.db.session.commit(); coach_id=new_coach.id
        self.login_as(self.admin_id)
        response=self.client.post(f"/admin/coach/{coach_id}/team",data={"team_id":self.team_id},follow_redirects=True)
        self.assertIn(b"New Coach assigned",response.data)
        with portal.app.app_context():
            membership=portal.TeamMembership.query.filter_by(user_id=coach_id,team_id=self.team_id,role="coach").one()
            self.assertTrue(membership.approved)
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

    def test_admin_email_setting_promotes_only_matching_account(self):
        with patch.dict(os.environ, {"ADMIN_EMAIL": "coach@example.com"}):
            with portal.app.app_context():
                portal.initialize_database()
        with portal.app.app_context():
            coach = portal.db.session.get(portal.User, self.coach_id)
            other = portal.User.query.filter_by(email="other@example.com").one()
            self.assertEqual("admin", coach.role)
            self.assertEqual("coach", other.role)

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

    def test_baseball_iq_learning_and_practice_pages(self):
        self.login_as(self.coach_id)
        learn = self.client.get("/learn")
        self.assertEqual(200, learn.status_code)
        self.assertIn(b"Complete Baseball IQ", learn.data)
        for level in ("foundation", "competitive", "advanced"):
            practice = self.client.get(f"/practice?level={level}")
            pitch_plan = self.client.get(f"/pitch-plan?level={level}")
            self.assertEqual(200, practice.status_code)
            self.assertEqual(200, pitch_plan.status_code)
            self.assertIn(b"Question 1 of 10", practice.data)
            self.assertIn(level.upper().encode(), practice.data)
            self.assertIn(b"Pitch Plan Lab", pitch_plan.data)
            self.assertIn(level.upper().encode(), pitch_plan.data)
        guides = self.client.get("/field-guides")
        self.assertEqual(200, guides.status_code)
        self.assertIn(b"Pop-up priority ladder", guides.data)
        self.assertIn(b"Single to right", guides.data)

    def test_learning_question_banks_are_progressive_and_valid(self):
        expected_areas={"Infield","Outfield","Catching","Baserunning","Hitting","Pitching","Team Defense"}
        for level in ("foundation","competitive","advanced"):
            field_questions=portal.FIELD_IQ_LEVELS[level]
            pitch_questions=portal.PITCH_PLAN_LEVELS[level]
            self.assertEqual(10,len(field_questions))
            self.assertEqual(10,len(pitch_questions))
            self.assertTrue(expected_areas.issubset({question["area"] for question in field_questions}))
            for question in field_questions+pitch_questions:
                self.assertEqual(level,question["level"])
                self.assertEqual(4,len(question["answers"]))
                self.assertIn(question["correct"],range(4))
                self.assertGreater(len(question["explanation"]),40)


if __name__ == "__main__":
    unittest.main()
