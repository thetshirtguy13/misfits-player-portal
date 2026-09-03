# Misfits Player Development — Pre-Launch Checklist

## Hosting and database
- [ ] Production HTTPS URL works.
- [ ] PostgreSQL is connected.
- [ ] Database migrations are applied.
- [ ] Production `SECRET_KEY` is set and not committed to source control.
- [ ] `SESSION_COOKIE_SECURE=true` is set.
- [ ] `/health` returns `{"ok": true}`.

## Email and account recovery
- [ ] New account verification email arrives.
- [ ] Verification link works and expires correctly.
- [ ] Forgot-password email arrives.
- [ ] Reset link works and old reset links become invalid after password change.

## Youth privacy and permissions
- [ ] Player registration requires parent/guardian email.
- [ ] Parent consent link works.
- [ ] Player cannot record workouts/stats before consent.
- [ ] Parent sees only linked player accounts.
- [ ] Coach sees only players on shared approved teams.
- [ ] A player outside a coach's team cannot be opened by URL guessing.

## Training and statistics
- [ ] Pitching workouts load.
- [ ] Catching workouts load.
- [ ] Batting workouts load.
- [ ] Fielding workouts load.
- [ ] Workout completion appears in player progress.
- [ ] Manual game stats calculate AVG and OBP correctly.
- [ ] GameChanger CSV import is tested with a real export.

## Private videos
- [ ] Private bucket is not publicly readable.
- [ ] MP4/MOV/WebM uploads work.
- [ ] Unauthorized users cannot open another player's video.
- [ ] Video links expire after 15 minutes.

## Data rights and security
- [ ] CSRF protection blocks invalid POST requests.
- [ ] Login rate limiting is working.
- [ ] Data export downloads a user's information.
- [ ] Account deletion disables the login and removes active player data.
- [ ] Audit records are created for logins, imports, workout completion, exports, and deletion.
- [ ] Database backups are enabled and a restore has been tested.

## Policies
- [ ] Privacy contact information is filled in.
- [ ] Privacy Policy has been reviewed.
- [ ] Terms of Use have been reviewed.
- [ ] Parent-consent wording has been reviewed.
- [ ] Data-retention period has been decided.

## Shopify
- [ ] App works correctly at its own HTTPS URL before embedding.
- [ ] Shopify embed page is mobile tested.
- [ ] Login, logout, uploads, and downloads work inside or alongside the Shopify storefront.
