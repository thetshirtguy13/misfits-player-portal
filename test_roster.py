import io
from test_app import PortalFlowTests, portal


class RosterTests(PortalFlowTests):
    def upload(self, text, team=None, day='2026-09-17'):
        return self.client.post('/roster', data={'team_id': team or self.team_id, 'season':'Fall 2026', 'as_of':day, 'csv_file':(io.BytesIO(text.encode()), 'stats.csv')}, follow_redirects=True)

    def test_snapshot_is_idempotent_and_does_not_create_logins(self):
        self.login_as(self.coach_id)
        csv='Player,Jersey,GP,PA,AB,H,OBP,SB\nPlayer One,7,4,6,2,0,.667,6\n'
        self.assertIn(b'1 player profiles updated', self.upload(csv).data)
        with portal.app.app_context():
            p=portal.RosterProfile.query.one()
            p.focus='Track the ball';portal.db.session.commit()
        self.upload(csv)
        with portal.app.app_context():
            p=portal.RosterProfile.query.one()
            self.assertEqual(6,p.stats['SB'])
            self.assertEqual(.667,p.stats['OBP'])
            self.assertNotIn('BB',p.stats)
            self.assertEqual('Track the ball',p.focus)
            self.assertEqual(0,portal.User.query.filter_by(role='player').count())

    def test_team_scope_and_notes_are_protected(self):
        self.login_as(self.coach_id)
        self.assertEqual(403,self.upload('Player,AB\nOne,2\n',team=self.other_team_id).status_code)
        self.upload('Player,AB\nOne,2\n')
        with portal.app.app_context():
            pid=portal.RosterProfile.query.one().id
            other=portal.User.query.filter_by(email='other@example.com').one().id
        self.login_as(other)
        self.assertEqual(403,self.client.post(f'/roster/{pid}/development',data={'focus':'bad'}).status_code)
        self.assertNotIn(b'One',self.client.get('/roster').data)

    def test_invalid_import_is_atomic_and_older_snapshot_rejected(self):
        self.login_as(self.coach_id)
        self.upload('Player,AB\nOne,2\n')
        self.upload('Player,AB\nOne,3\nTwo,NaN\n')
        self.upload('Player,AB\nOne,9\n',day='2026-09-16')
        with portal.app.app_context():
            self.assertEqual(1,portal.RosterProfile.query.count())
            self.assertEqual(2,portal.RosterProfile.query.one().stats['AB'])

    def test_animation_controls_and_tips_render(self):
        self.login_as(self.coach_id)
        page=self.client.get('/practice').data
        for label in (b'play-ball', b'ball-trail', b'replay-play', b'pause-play', b'play-speed', b'question-tip', b'field_play.js'):
            self.assertIn(label,page)
