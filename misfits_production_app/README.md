# Misfits Player Development — Pre-Shopify Production Package

This package replaces the browser-only prototype with a server/database application intended to be deployed before embedding it into Shopify.

## Included

- Player, parent, and coach accounts
- Hashed passwords
- Email verification
- Password reset / account recovery
- Parent/guardian consent for youth player accounts
- Parent-to-player authorization
- Organization/team creation and team join codes
- Coach access limited to players sharing approved teams
- Pitching, catching, batting, and fielding home workouts
- Workout completion and notes
- Manual game statistics
- GameChanger CSV import restricted to coach teams
- Player progress dashboards
- Private training-video storage using an S3-compatible private bucket
- Signed video links that expire after 15 minutes
- CSRF protection
- Login and endpoint rate limiting
- Secure cookie options
- Security headers
- Audit logging
- User data export
- Account deletion/anonymization
- Privacy Policy and Terms pages
- Health-check endpoint
- PostgreSQL production support
- Flask-Migrate support
- Dockerfile, Procfile, Render blueprint, and environment template
- PostgreSQL backup script

## Local test

1. Install Python 3.12+.
2. `python -m venv .venv`
3. Activate it.
4. `pip install -r requirements.txt`
5. `python app.py`
6. Open `http://localhost:5000`

Without SMTP configured, verification, reset, and parent-consent URLs are displayed as development flash messages so the flows can still be tested locally.

## Production environment

Set at minimum:

- `SECRET_KEY`
- `DATABASE_URL` (PostgreSQL)
- `APP_BASE_URL`
- `SESSION_COOKIE_SECURE=true`
- SMTP variables in `.env.example`

For private video uploads also set:

- `S3_BUCKET`
- `S3_REGION`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- optionally `S3_ENDPOINT_URL` for an S3-compatible provider

The storage bucket should be private. Do not configure it for public read access.

## Database migrations

After configuring the database:

- `flask --app app db init` (first migration repository only)
- `flask --app app db migrate -m "initial"`
- `flask --app app db upgrade`

For subsequent releases, create and apply a new migration rather than using `db.create_all()` as the long-term schema workflow.

## Backups

`scripts/backup_postgres.sh` creates a compressed PostgreSQL dump. In production, run this on a scheduled job and retain encrypted copies according to your retention policy. Also enable the database provider's managed backups / point-in-time recovery if available.

## Before Shopify

1. Deploy the app to an HTTPS URL.
2. Connect PostgreSQL.
3. Configure SMTP and test verification/reset/consent emails.
4. Configure the private video bucket and test signed access.
5. Apply database migrations.
6. Run the PRELAUNCH_CHECKLIST.md.
7. Replace placeholder privacy-contact information in the Privacy Policy.
8. Test player, parent, and coach roles on separate devices.
9. Import a real GameChanger CSV and verify totals.
10. Only then embed the HTTPS app URL in Shopify.

## Important legal/privacy note

This package implements product controls for youth accounts, but it is not a substitute for legal review. Before public use with minors, have the final privacy policy, terms, consent process, retention practice, and applicable child-privacy obligations reviewed for your organization and jurisdiction.
