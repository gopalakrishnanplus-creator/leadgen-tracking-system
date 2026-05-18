import csv

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from leadgen.models import MarketingEmailCampaign, PharmaManager
from leadgen.services import marketing_email_recipients


def _split_targets(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _multi_slot_query(values, prefix, max_slots, lookup):
    query = Q()
    for value in values:
        for index in range(1, max_slots + 1):
            query |= Q(**{f"{prefix}_{index}__{lookup}": value})
    return query


class Command(BaseCommand):
    help = "Audit a marketing email campaign's targeted recipient selection against the current pharma manager database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--campaign-id",
            type=int,
            help="MarketingEmailCampaign id to audit. Defaults to latest molecule/formulation targeted campaign.",
        )
        parser.add_argument(
            "--csv",
            default="",
            help="Optional path to write the recomputed exact recipient list as CSV.",
        )

    def handle(self, *args, **options):
        campaign_id = options.get("campaign_id")
        if campaign_id:
            campaign = MarketingEmailCampaign.objects.select_related("playbook").filter(pk=campaign_id).first()
            if campaign is None:
                raise CommandError(f"No marketing email campaign found with id={campaign_id}.")
        else:
            campaign = (
                MarketingEmailCampaign.objects.select_related("playbook")
                .filter(campaign_type=MarketingEmailCampaign.TYPE_MOLECULE_TARGETED)
                .order_by("-sent_at")
                .first()
            )
            if campaign is None:
                raise CommandError("No targeted marketing email campaigns found.")

        therapy_areas = _split_targets(campaign.target_therapy_areas)
        molecules = _split_targets(campaign.target_molecules)
        exact_recipients = list(
            marketing_email_recipients(
                campaign.playbook,
                campaign.campaign_type,
                therapy_areas=therapy_areas,
                molecules=molecules,
            )
        )

        exact_query = Q()
        if therapy_areas:
            exact_query |= _multi_slot_query(therapy_areas, "therapy_area", 5, "iexact")
        if molecules:
            exact_query |= _multi_slot_query(molecules, "molecule", 10, "iexact")

        contains_query = Q()
        if therapy_areas:
            contains_query |= _multi_slot_query(therapy_areas, "therapy_area", 5, "icontains")
        if molecules:
            contains_query |= _multi_slot_query(molecules, "molecule", 10, "icontains")

        exact_all = PharmaManager.objects.filter(exact_query).count() if exact_query else 0
        contains_all = PharmaManager.objects.filter(contains_query).count() if contains_query else 0
        contains_subscribed_with_email = (
            PharmaManager.objects.filter(contains_query, unsubscribed=False).exclude(email="").count()
            if contains_query
            else 0
        )

        exact_emails = {recipient.email.lower() for recipient in exact_recipients}
        contains_extra = []
        if contains_query:
            contains_extra = list(
                PharmaManager.objects.filter(contains_query, unsubscribed=False)
                .exclude(email="")
                .exclude(email__in=exact_emails)
                .order_by("name", "company_name")
                .values_list("name", "company_name", "email")[:100]
            )

        self.stdout.write(f"Campaign id: {campaign.pk}")
        self.stdout.write(f"Campaign type: {campaign.get_campaign_type_display()}")
        self.stdout.write(f"Sent at: {campaign.sent_at}")
        self.stdout.write(f"Playbook: {campaign.playbook.title}")
        self.stdout.write(f"Selected therapy areas: {therapy_areas or '-'}")
        self.stdout.write(f"Selected molecules/formulations: {molecules or '-'}")
        self.stdout.write(f"Stored sent count: {campaign.recipient_count}")
        self.stdout.write(f"Stored failed count: {campaign.failed_count}")
        self.stdout.write(f"Recomputed exact subscribed recipients with email: {len(exact_recipients)}")
        self.stdout.write(f"All exact database matches, including unsubscribed/blank email: {exact_all}")
        self.stdout.write(f"All partial contains matches, including unsubscribed/blank email: {contains_all}")
        self.stdout.write(f"Partial contains subscribed matches with email: {contains_subscribed_with_email}")

        if campaign.recipient_count == len(exact_recipients):
            self.stdout.write(self.style.SUCCESS("Stored sent count matches the current exact recipient query."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Stored sent count does not match the current exact recipient query. "
                    "The database may have changed since the campaign was sent, or some sends failed."
                )
            )

        if contains_extra:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(contains_extra)} partial-match subscribed records are not exact-match recipients "
                    "(showing up to 100)."
                )
            )
            for name, company_name, email in contains_extra:
                self.stdout.write(f"- {name} / {company_name} / {email}")

        csv_path = options.get("csv")
        if csv_path:
            with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(["name", "company_name", "email", "therapy_areas", "molecules"])
                for recipient in exact_recipients:
                    writer.writerow(
                        [
                            recipient.name,
                            recipient.company_name,
                            recipient.email,
                            ", ".join(recipient.therapy_areas),
                            ", ".join(recipient.molecules),
                        ]
                    )
            self.stdout.write(self.style.SUCCESS(f"Exact recipient CSV written to {csv_path}"))
