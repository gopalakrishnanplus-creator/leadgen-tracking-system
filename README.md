# Leadgen Tracking System

Django application for managing lead generation staff, supervisor review, CRM call imports, meeting scheduling, and reporting.

## Stack

- Django 5.2
- Python 3.11
- MySQL in deployment, SQLite fallback for local development if MySQL env vars are not set
- Google OAuth via `django-allauth`
- SendGrid email delivery with `.ics` calendar invites

## Features

- Single supervisor account with Google sign-in
- Lead gen staff management by supervisor
- Staff prospect creation and supervisor approval workflow
- Prospect assignment visibility restricted to the assigned staff member
- Daily CRM spreadsheet import using the Exotel report format
- Manual post-call outcome capture for follow-up, decline, and scheduled meetings
- Calendar invitation emails to prospect, supervisor, staff member, and up to three sales recipients
- Meeting outcome updates with automatic follow-up reset when a meeting does not happen
- Supervisor reporting for attempts, connects, follow-ups, scheduled meetings, and meetings that did not happen

## Local setup

1. Create and activate a Python 3.11 virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables from `.env.example`.
4. Run migrations:

```bash
python manage.py migrate
```

5. Optionally pre-create the supervisor record:

```bash
python manage.py bootstrap_supervisor
```

6. Start the app:

```bash
python manage.py runserver
```

## Google OAuth

Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in the environment. Configure the Google OAuth redirect URI as:

`http://127.0.0.1:8000/accounts/google/login/callback/`

Use the deployed domain equivalent in production.

Only users whose email addresses already exist in the system can sign in, except for the supervisor email configured by `SUPERVISOR_EMAIL`, which is auto-provisioned on first Google login if it does not already exist.

## SendGrid

Set `SENDGRID_API_KEY` to enable SendGrid API delivery. If it is omitted, Django falls back to the configured email backend, which defaults to console output for local development.

## CRM import format

The spreadsheet import expects these columns:

- `Call SID`
- `Start Time`
- `End Time`
- `From`
- `To`
- `Direction`
- `Status`

Matching logic:

- `From` matches the lead gen staff calling number
- `To` matches the assigned prospect phone number

## Deployment notes

- Set MySQL environment variables in production
- Set `DJANGO_ALLOWED_HOSTS`
- Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`
- Set `SENDGRID_API_KEY`
- Run `python manage.py migrate`
