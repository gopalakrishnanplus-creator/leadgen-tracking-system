# Project Handoff

This document is the orientation pack for any new thread working on the Leadgen Tracking System.

If a new Codex thread starts on this project, the first instruction should be:

```text
Read /Users/gk/Documents/Codex-projects/Leadgen-tracking-system/docs/PROJECT_HANDOFF.md before making changes.
```

## 1. Project Identity

- Project name: `Leadgen Tracking System`
- Local repo path: `/Users/gk/Documents/Codex-projects/Leadgen-tracking-system`
- GitHub repo: [gopalakrishnanplus-creator/leadgen-tracking-system](https://github.com/gopalakrishnanplus-creator/leadgen-tracking-system)
- Default branch: `main`
- Current deployment model: every push to `main` triggers GitHub Actions deployment to the production EC2 instance

This is a Django application used to manage:

- lead generation staff and supervisor workflow
- prospect intake and approval
- Exotel call-import processing
- meeting scheduling and reminder tracking
- sales pipeline progression
- contracts and collections
- role-based access for supervisor, lead gen staff, sales manager, and finance manager

## 2. Tech Stack

- Python `3.11`
- Django `5.2`
- `django-allauth` with Google OAuth only
- MySQL in production
- SQLite fallback locally
- SendGrid for email delivery
- `.ics` invites via `icalendar`
- `openpyxl` for Exotel spreadsheet ingestion
- WhiteNoise for static files
- Gunicorn + Nginx on EC2

Dependencies are in [requirements.txt](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/requirements.txt).

## 3. High-Level Architecture

The project is a single Django app called `leadgen`, with most domain logic centralized in `leadgen/services.py`.

### Main layers

- [config/settings.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/config/settings.py)
  - settings, env loading, auth config, mail config, DB config
- [leadgen/models.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/models.py)
  - domain models and role definitions
- [leadgen/forms.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/forms.py)
  - all form logic and validation
- [leadgen/views.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/views.py)
  - all HTTP views and page flows
- [leadgen/services.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/services.py)
  - business logic, imports, email delivery, reminders, reporting, meeting/sales/contracts transitions
- [leadgen/adapters.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/adapters.py)
  - Google login authorization rules
- [leadgen/account_adapter.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/account_adapter.py)
  - disables open signup
- [leadgen/middleware.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/middleware.py)
  - active-account and valid-role enforcement
- [leadgen/decorators.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/decorators.py)
  - role gating for views

### Important architectural note

The application is intentionally monolithic. There are no separate Django apps for lead gen, sales, or finance. Those concerns all live in the `leadgen` app, segmented mostly by:

- user role
- URL namespace
- model type
- template group

## 4. User Roles

Defined in [leadgen/models.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/models.py).

### Supervisor

- There is only one actual supervisor user record in the database.
- Multiple Google accounts can map to that one supervisor identity using `SUPERVISOR_ALLOWED_EMAILS`.
- Supervisor can:
  - add/edit/deactivate lead gen staff
  - add/edit/deactivate sales managers
  - add/edit/deactivate finance managers
  - review, reassign, add, delete, and manage prospects
  - import Exotel call spreadsheets
  - see reporting, reminder dashboards, invalid numbers, supervisor-action queue
  - update meeting outcomes
  - access sales pipeline and contracts screens

### Lead Gen Staff

- Must have a unique `calling_number`.
- Can only see prospects assigned to them.
- Can add prospects for review.
- Can update call outcomes.
- Can view their meetings and log WhatsApp reminder screenshots.

### Sales Manager

- No calling number.
- Access restricted to sales pipeline and contracts views.
- If multiple sales managers exist, pipeline items may be unassigned unless explicitly assigned.
- Seeded initial sales manager: `amit@inditech.co.in`

### Finance Manager

- No calling number.
- Access restricted to contracts and pending collections.
- Can update collection amounts and collection dates only.

## 5. Authentication Model

Authentication is Google-only. Username/password is not used.

Relevant files:

- [config/settings.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/config/settings.py)
- [leadgen/adapters.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/adapters.py)
- [leadgen/account_adapter.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/account_adapter.py)
- [leadgen/middleware.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/middleware.py)

### Important auth rules

- Only Google provider is allowed.
- Google email must be verified.
- Open signup is disabled.
- The email must already exist in the system, except supervisor aliases.
- These supervisor aliases currently map to the single supervisor account:
  - `gopala.krishnan@inditech.co.in`
  - `gkinchina@gmail.com`
  - `bhavesh.kataria@inditech.co.in`

## 6. Data Model Summary

### User

- Custom auth model
- Roles: supervisor, staff, sales_manager, finance_manager
- Lead gen staff require `calling_number`
- Non-staff roles must not have `calling_number`

### SystemSetting

- Singleton row with primary key `1`
- Holds:
  - supervisor sender identity
  - timezone
  - extra sales email recipients

### Prospect

Core lead record. Main fields:

- `company_name`
- `contact_name`
- `linkedin_url`
- `phone_number`
- `assigned_to`
- `created_by`
- approval and workflow state
- Exotel-derived call metrics
- follow-up reason/date
- prospect email
- supervisor/system notes

Important workflow states:

- `pending_review`
- `ready_to_call`
- `follow_up_to_schedule`
- `does_not_agree`
- `scheduled`
- `meeting_happened`
- `supervisor_action_required`
- `invalid_number`

### CallImportBatch

- One uploaded Exotel spreadsheet import

### CallLog

- One imported Exotel call row
- Matches staff by `From`
- Matches prospect by `To`

### ProspectStatusUpdate

- Manual lead-gen outcome log:
  - follow up
  - decline
  - scheduled
  - meeting happened
  - meeting did not happen

### Meeting

- Created when a lead gen staff marks a prospect as `Scheduled`
- Tracks:
  - staff who scheduled it
  - scheduled datetime
  - prospect email
  - platform (`teams` or `zoom`)
  - recipients
  - meeting outcome

### MeetingReminder

- One reminder record per meeting per reminder type
- Types:
  - first WhatsApp
  - 24-hour email
  - same-day 9 a.m. email
  - final WhatsApp
- WhatsApp reminders store uploaded screenshot proof

### SalesConversation

- Auto-created from a meeting marked `happened`
- Can also be created manually
- Has `sales_conversation_id`
- Holds sales-stage metadata, brand data, comments, and attached solution/proposal files

### ContractCollection

- Created when a sales conversation is marked `contract_signed`
- Can also be created manually
- Has `contract_collection_id`
- Holds contact snapshot, contract value, files, installments, and assigned sales manager

### ContractCollectionInstallment

- Up to 6 installments
- Tracks invoice date, expected/revised collection dates, collected amount/date, invoice notification timestamp

## 7. Main Business Flows

### A. Lead Gen Prospect Flow

1. Lead gen staff adds prospect.
2. Prospect is created as:
   - `approval_status = pending`
   - `workflow_status = pending_review`
3. Supervisor reviews and either:
   - accepts -> `ready_to_call`
   - rejects
4. Supervisor can also:
   - reassign prospect to another lead gen staff
   - add prospect directly and assign it
   - hard-delete prospect

### B. Exotel Import Flow

Driven by [leadgen/services.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/services.py), especially `import_exotel_report`, `refresh_prospect_call_metrics`, and `refresh_prospect_import_state`.

Import expects spreadsheet columns:

- `Call SID`
- `Start Time`
- `End Time`
- `From`
- `To`
- `Direction`
- `Status`

Matching logic:

- `From` -> staff `calling_number`
- `To` -> prospect `phone_number` within that staff’s assigned prospects

Automations from imported status:

- `failed` -> prospect becomes `invalid_number`
- `no-answer` x 5 since last reset -> prospect becomes `supervisor_action_required`

When supervisor reassigns a 5-times-unanswered prospect, `no_answer_reset_count` is updated so the new staff member gets a fresh attempt cycle.

### C. Manual Call Outcome Flow

Handled by `apply_call_outcome`.

Possible outcomes:

- follow-up-to-schedule
- does-not-agree
- scheduled

If scheduled:

- creates a `Meeting`
- stores prospect email
- stores chosen meeting platform
- sends invitation email after DB commit

### D. Meeting Outcome Flow

Handled by `update_meeting_outcome`.

If `Meeting happened`:

- prospect moves to `meeting_happened`
- sales conversation is auto-created

If `Did not happen`:

- prospect returns to `follow_up_to_schedule`
- follow-up date becomes meeting date in local timezone
- follow-up reason becomes `Meeting did not happen`
- did-not-happen email is sent after commit

### E. Meeting Reminder Flow

Current rules implemented:

- first WhatsApp: manual proof upload by lead gen staff
- 24-hour reminder email: automated
- same-day 9 a.m. reminder email: automated
- final WhatsApp: manual proof upload by lead gen staff

Reminder features:

- staff logs WhatsApp proof on `/staff/meetings/<meeting_id>/reminders/`
- supervisor sees reminder dashboard on `/supervisor/reminders/`
- if a meeting is rescheduled, a new `Meeting` record is created, so reminder tracking naturally restarts on the new meeting record

### F. Sales Pipeline Flow

Sales pipeline is visible to:

- supervisor
- sales manager

Main behavior:

- open pipeline excludes `contract_signed=True`
- contract-signed records transition into contracts and collections
- records are not deleted from the DB when signed

### G. Contracts and Collections Flow

Visible to:

- supervisor
- sales manager
- finance manager

Edit permissions:

- supervisor/sales manager edit contract terms
- finance manager edits collected amount/date

Constraints:

- existing uploaded files are add-only
- many contract fields lock after first set
- `revised_collection_date` remains editable

### H. Pending Collections Flow

Built by `build_pending_collections`.

Shows:

- invoiced but not collected
- yet to be invoiced
- totals for each section

## 8. Key Modules and What They Own

### [leadgen/services.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/services.py)

This is the most important file for business logic.

Key responsibilities:

- phone normalization
- datetime parsing
- Exotel import
- call metric refresh
- invalid-number and no-answer escalation
- reporting
- email delivery
- SendGrid diagnostics
- calendar invite generation
- meeting invitation sending
- reminder email sending and reminder dashboard assembly
- sales conversation sync
- contract sync
- invoice due email sending
- call outcome transitions
- meeting outcome transitions

If a change affects workflow behavior, start here.

### [leadgen/views.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/views.py)

Contains all route handlers and user-facing workflow entry points.

Important split:

- `/supervisor/...`
- `/staff/...`
- `/sales/...`
- `/contracts/...`

### [leadgen/forms.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/forms.py)

Contains nontrivial validation and data shaping for:

- role-specific user forms
- LinkedIn URL normalization
- prospect review and reassignment
- call outcomes
- reminder logging
- sales contacts/brands/files
- contracts/installments
- finance collection updates

### [leadgen/models.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/models.py)

Contains all business entities and many core invariants.

### [leadgen/tests.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/tests.py)

Single large test module covering:

- prospect flow
- imports
- reminder flow
- email delivery behavior
- sales pipeline
- contracts
- supervisor/staff views

When adding features, expand this file unless you intentionally split tests.

## 9. URL / Navigation Summary

### Public/Auth

- `/login/`
- `/logout/`
- `/accounts/google/login/` via allauth

### Supervisor

- `/supervisor/`
- `/supervisor/staff/`
- `/supervisor/sales-managers/`
- `/supervisor/finance-managers/`
- `/supervisor/prospects/`
- `/supervisor/prospects/review/`
- `/supervisor/prospects/invalid/`
- `/supervisor/prospects/supervisor-action/`
- `/supervisor/imports/`
- `/supervisor/meetings/`
- `/supervisor/reminders/`
- `/supervisor/reports/`
- `/supervisor/daily-targets/`
- `/supervisor/settings/`

### Lead Gen Staff

- `/staff/dashboard/`
- `/staff/prospects/`
- `/staff/prospects/add/`
- `/staff/prospects/<id>/update-call/`
- `/staff/meetings/`
- `/staff/meetings/<id>/reminders/`

### Sales

- `/sales/`
- `/sales/add/`
- `/sales/<id>/`

### Contracts / Finance

- `/contracts/`
- `/contracts/add/`
- `/contracts/<id>/`
- `/contracts/pending-collections/`

## 10. Email and Calendar Behavior

### Sender

Configured in env:

- `DEFAULT_FROM_EMAIL=products@inditech.co.in`
- `REPLY_TO_EMAIL=bhavesh.kataria@inditech.co.in`

### Meeting invites

- Sent to prospect
- CC includes supervisor, scheduling staff member, all active sales managers, and configured extra sales emails
- `.ics` invite attached
- subject currently:
  - `20-min: Outcome-Linked Campaign – Discussion with <Company Name>`

### Meeting reminder emails

- Sent automatically to prospect only
- two automated types:
  - 24-hour reminder
  - same-day 9 a.m. reminder

### Did-not-happen email

- goes to scheduling staff member
- supervisor is CC’d

### Invoice due email

- goes to supervisor, assigned sales manager, and all active finance managers

### Diagnostics

Use:

```bash
/var/www/.venv/bin/python manage.py send_test_email_diagnostic --to gopala.krishnan@inditech.co.in
```

to debug production email delivery.

## 11. Fixed Meeting Platform Links

Configured in settings/env:

- Teams link
- Zoom link
- Zoom Meeting ID
- Zoom Passcode

When a meeting is scheduled, the user selects the platform from a dropdown. The meeting email and `.ics` include the corresponding link.

## 12. Local Development Environment

### Local repo

- Path: `/Users/gk/Documents/Codex-projects/Leadgen-tracking-system`

### Local virtualenv

- Path: `/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/.venv`

### Local DB

- Default local DB file: `/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/db.sqlite3`

### Local env loading

Settings load env in this order:

1. `LEADGEN_ENV_FILE` if set
2. `/var/www/secrets/.leadgen_env`
3. repo-local `.env`

This means the same settings file works for both local and production, but local development normally relies on a repo-local `.env`.

### Standard local commands

```bash
cd /Users/gk/Documents/Codex-projects/Leadgen-tracking-system
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py runserver
./.venv/bin/python manage.py test leadgen.tests
./.venv/bin/python manage.py check
```

## 13. Remote Production Environment

### Paths

- app dir: `/var/www/leadgen-tracking-system`
- venv: `/var/www/.venv`
- env file: `/var/www/secrets/.leadgen_env`

### Service layer

- Gunicorn service: `leadgen-tracking.service`
- Nginx proxies to Gunicorn on `127.0.0.1:8001`
- health endpoint: `/health/`

### Production deploy sequence

From [`.github/workflows/deploy.yml`](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/.github/workflows/deploy.yml):

1. GitHub Actions runs on push to `main`
2. It assumes AWS role via OIDC
3. It sends an SSM command to EC2 instance `i-042b772db54dd2178`
4. EC2 runs:
   - `git fetch origin`
   - `git reset --hard origin/main`
   - `pip install -r requirements.txt`
   - `python manage.py migrate`
   - `python manage.py collectstatic --noinput`
   - `systemctl restart leadgen-tracking.service`

### Important production warning

Any push to `main` is production-impacting because it auto-deploys.

There is no PR-only staging workflow in this repo right now.

## 14. Scheduled / Operational Commands

These are important because several features depend on server-side recurring execution.

### Meeting reminder emails

Should be run on a schedule, ideally every 15 minutes:

```bash
cd /var/www/leadgen-tracking-system && /var/www/.venv/bin/python manage.py send_due_meeting_reminder_emails
```

### Invoice due notifications

Should be run daily:

```bash
cd /var/www/leadgen-tracking-system && /var/www/.venv/bin/python manage.py send_invoice_due_notifications
```

### Bootstrap supervisor

Useful locally or in a reset environment:

```bash
./.venv/bin/python manage.py bootstrap_supervisor
```

## 15. Template / UI Structure

- Shared layout: [templates/base.html](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/templates/base.html)
- Role-specific screens live under:
  - `templates/leadgen/`
- Email templates live under:
  - `templates/emails/`
- Static CSS lives in:
  - [static/css/app.css](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/static/css/app.css)

The app uses one consistent visual style across lead gen, sales, finance, and supervisor screens. When adding UI, preserve the existing look and feel.

## 16. Current Important Behaviors / Gotchas

1. Prospect deletion is a hard delete.
   - Supervisor can delete any prospect from `/supervisor/prospects/`.
   - This removes the prospect and dependent meeting/status history.

2. Invalid numbers are not deleted.
   - They are moved to `workflow_status = invalid_number`
   - They disappear from staff dashboards.

3. Five unanswered attempts do not delete or invalidate automatically.
   - They move to the supervisor-action queue.
   - Supervisor can either reassign or mark invalid.

4. Staff dashboards hide:
   - invalid numbers
   - supervisor-action-required prospects

5. New meeting schedule creates a new `Meeting`.
   - This is why reminder cycles restart naturally on reschedule.

6. There is one large test file.
   - Refactoring tests into modules is possible, but not yet done.

7. `db.sqlite3` exists in the local repo.
   - Be careful not to treat local SQLite data as production truth.

8. The deployment workflow uses `git reset --hard origin/main` on the server.
   - Never make manual code edits on the server that you expect to keep.

9. Finance/sales manager forms must bind their target role before validation.
   - This was previously broken and has been fixed.

10. The login flow is customized to avoid the extra allauth confirmation screen.

## 17. Recent Feature History

Recent major commits:

- `389727e` Simplify meeting invitation email
- `42616ac` Add supervisor prospect management page
- `2030dda` Fix non-staff manager form validation
- `c88da63` Add meeting reminder tracking workflow
- `6c57b97` Add supervisor daily targets report
- `fc2f5d9` Add meeting platform selection to invites
- `113ce7a` Handle invalid and unanswered prospect imports
- `5725bc5` Add supervisor prospect assignment controls
- `5148076` Add email diagnostics command

This is useful context when figuring out where a feature was last touched.

## 18. Recommended First Steps For Any New Thread

1. Read this handoff.
2. Read [README.md](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/README.md).
3. Check current branch and latest commits:

```bash
git branch --show-current
git log --oneline -n 15
```

4. Run:

```bash
./.venv/bin/python manage.py check
./.venv/bin/python manage.py test leadgen.tests
```

5. If the task touches workflow behavior, read:
   - [leadgen/services.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/services.py)
   - [leadgen/views.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/views.py)
   - [leadgen/models.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/models.py)

6. If the task touches auth, read:
   - [leadgen/adapters.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/adapters.py)
   - [leadgen/account_adapter.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/account_adapter.py)
   - [leadgen/middleware.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/leadgen/middleware.py)

7. If the task touches deploy/ops, read:
   - [config/settings.py](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/config/settings.py)
   - [deploy/ec2-deploy.sh](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/deploy/ec2-deploy.sh)
   - [deploy/systemd/leadgen-tracking.service](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/deploy/systemd/leadgen-tracking.service)
   - [deploy/nginx/leadgen-tracking.conf](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/deploy/nginx/leadgen-tracking.conf)
   - [.github/workflows/deploy.yml](/Users/gk/Documents/Codex-projects/Leadgen-tracking-system/.github/workflows/deploy.yml)

## 19. Suggested Bootstrap Prompt For A New Thread

Use this in the new thread:

```text
We are working in /Users/gk/Documents/Codex-projects/Leadgen-tracking-system.
Before making changes, read /Users/gk/Documents/Codex-projects/Leadgen-tracking-system/docs/PROJECT_HANDOFF.md and /Users/gk/Documents/Codex-projects/Leadgen-tracking-system/README.md.
Assume pushes to main auto-deploy to production on EC2, so be deliberate about changes.
After reading, summarize the relevant parts of the architecture for the task before editing.
```
