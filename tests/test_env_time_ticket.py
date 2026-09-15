"""
Environmental Scientist Time Ticket parsing.

The template bills like the survey ticket but names its people differently and,
critically, gives both scientists the same field labels ("Travel:", "Work:",
"Notes:", "Total:"). They are distinguishable only by the group Title, so the
tests that matter here are the ones proving scientist 2's hours are not a copy
of scientist 1's.
"""
import json
import unittest

from etl_time_tickets import (
    ENV_FORM_TYPE_ID,
    FORM_TYPE_IDS,
    _extract_field,
    parse_env_time_ticket,
    parse_ticket,
)


def worker(first, last):
    return json.dumps([{"Id": "x", "FirstName": first, "LastName": last, "JobTitle": "ES"}])


def item(label, value):
    return {"Content": label, "Value": value}


# Shaped after form 14-09-2026_HE&FY_DFT, submitted 2026-09-14.
def env_content(**over):
    s1 = over.get("s1", {"Travel": "1.5", "Work": "10.5", "Notes": ".5", "Total": "12.5"})
    s2 = over.get("s2", {"Travel": "2",   "Work": "9",    "Notes": "None", "Total": "11"})
    return {
        "Groups": [
            {"Title": "Revisions (modifications made)", "Items": [
                item("What were the changes made to the original ticket that was submitted?", "None")]},
            {"Title": "General Information", "Items": [
                item("Date:", over.get("date", "September 14, 2026")),
                item("Client:", "Whitecap"),
                item("Job No:", "250071"),
                item("Location:", "18-69-6-6 & 22-69-6-6"),
                item("Project Manager:", "Amanda Olanski"),
                item("Environmental Scientist 1", worker("Haley", "Ehler")),
                item("Environmental Scientist 2", worker("Fred", "Young")),
                item("Field Equipment (Day):", "1"),
                item("Truck km:", "100"),
                item("Truck hours:", "12"),
                item("ATV/UTV/Snowmobile:", "None"),
            ]},
            {"Title": "Environmental Scientist 1", "Items": [
                item("Travel:", s1["Travel"]), item("Work:", s1["Work"]),
                item("Notes:", s1["Notes"]), item("Total:", s1["Total"]),
                item("Subsistence", "None")]},
            {"Title": "Environmental Scientist 2", "Items": [
                item("Travel:", s2["Travel"]), item("Work:", s2["Work"]),
                item("Notes:", s2["Notes"]), item("Total:", s2["Total"]),
                item("Subsistence", "None")]},
            {"Title": "Notes", "Items": [item("Additional Comments:", "None")]},
            {"Title": "DESCRIPTION", "Items": [
                item("Details:", "met crew at GP office"),
                item("Approval:", "None"),
                item("Approval Date:", "None")]},
        ]
    }


FORM = {"Id": "form-1", "Label": "14-09-2026_HE&FY_DFT",
        "LocationId": "loc-1", "CreatedOn": "2026-09-14T15:00:00", "IsDeleted": False}


class TestEnvTimeTicket(unittest.TestCase):

    def test_scientists_map_to_crew_chief_and_assistant(self):
        row = parse_env_time_ticket(FORM, env_content())
        self.assertEqual(row["crew_chief"], "Haley Ehler")
        self.assertEqual(row["assistant"], "Fred Young")

    def test_each_scientist_keeps_their_own_hours(self):
        # The bug this template invites: identical labels in two groups, so an
        # unscoped lookup returns scientist 1's numbers for both people.
        row = parse_env_time_ticket(FORM, env_content())
        self.assertEqual(row["cc_travel_hrs"], 1.5)
        self.assertEqual(row["cc_work_hrs"], 10.5)
        self.assertEqual(row["cc_notes_hrs"], 0.5)
        self.assertEqual(row["cc_total_hrs"], 12.5)
        self.assertEqual(row["sa_travel_hrs"], 2.0)
        self.assertEqual(row["sa_work_hrs"], 9.0)
        self.assertEqual(row["sa_total_hrs"], 11.0)
        self.assertNotEqual(row["cc_total_hrs"], row["sa_total_hrs"])

    def test_group_scoped_extract_ignores_other_groups(self):
        c = env_content(s1={"Travel": "1", "Work": "2", "Notes": "None", "Total": "3"},
                        s2={"Travel": "4", "Work": "5", "Notes": "None", "Total": "9"})
        self.assertEqual(_extract_field(c, "Total", "Environmental Scientist 1"), "3")
        self.assertEqual(_extract_field(c, "Total", "Environmental Scientist 2"), "9")

    def test_unscoped_extract_still_returns_first_match(self):
        # Unchanged behaviour for the survey template, which has no repeats.
        self.assertEqual(_extract_field(env_content(), "Total"), "12.5")

    def test_general_information_fields(self):
        row = parse_env_time_ticket(FORM, env_content())
        self.assertEqual(row["ticket_date"], "2026-09-14")
        self.assertEqual(row["client"], "Whitecap")
        self.assertEqual(row["job_no"], "250071")
        self.assertEqual(row["project_manager"], "Amanda Olanski")
        self.assertEqual(row["wellsite_location"], "18-69-6-6 & 22-69-6-6")

    def test_field_equipment_maps_to_survey_equipment_day(self):
        row = parse_env_time_ticket(FORM, env_content())
        self.assertEqual(row["survey_equipment_day"], 1.0)
        self.assertEqual(row["truck_km"], 100.0)
        self.assertEqual(row["truck_hours"], 12.0)

    def test_fields_absent_from_this_template_are_null(self):
        row = parse_env_time_ticket(FORM, env_content())
        for col in ("client_field_rep", "pipe_locator_hrs", "chainsaw_hrs",
                    "jackhammer_hrs", "marker_posts", "iron_posts"):
            self.assertIsNone(row[col], col)

    def test_row_is_tagged_environmental(self):
        self.assertEqual(parse_env_time_ticket(FORM, env_content())["ticket_type"],
                         "environmental")

    def test_dispatch_by_ticket_type(self):
        row = parse_ticket(FORM, env_content(), "environmental")
        self.assertEqual(row["crew_chief"], "Haley Ehler")
        self.assertEqual(row["ticket_type"], "environmental")

    def test_survey_rows_are_tagged_survey(self):
        survey = {"Groups": [{"Title": "General Information", "Items": [
            item("Date:", "September 14, 2026"), item("Crew Chief:", worker("Rod", "Lane"))]}]}
        row = parse_ticket({"Id": "f2", "Label": "", "LocationId": None,
                            "CreatedOn": None, "IsDeleted": False}, survey, "survey")
        self.assertEqual(row["ticket_type"], "survey")
        self.assertEqual(row["crew_chief"], "Rod Lane")
        self.assertIsNone(row["sa_notes_hrs"])

    def test_both_templates_are_registered(self):
        self.assertEqual(FORM_TYPE_IDS[ENV_FORM_TYPE_ID], "environmental")
        self.assertEqual(len(FORM_TYPE_IDS), 2)

    def test_upsert_sql_covers_every_parsed_column(self):
        from etl_time_tickets import UPSERT_SQL
        for col in parse_env_time_ticket(FORM, env_content()):
            self.assertIn(f"%({col})s", UPSERT_SQL, col)

    def test_solo_scientist_leaves_assistant_empty(self):
        c = env_content(s2={"Travel": "None", "Work": "None", "Notes": "None", "Total": "None"})
        c["Groups"][1]["Items"][6] = item("Environmental Scientist 2", "None")
        row = parse_env_time_ticket(FORM, c)
        self.assertEqual(row["crew_chief"], "Haley Ehler")
        self.assertIsNone(row["assistant"])
        self.assertIsNone(row["sa_total_hrs"])


if __name__ == "__main__":
    unittest.main()
