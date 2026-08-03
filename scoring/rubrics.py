"""
scoring/rubrics.py — data-driven role-specific scoring rubrics.

Built from the actual Google Form ("Hopcharge Interview Round 0", updated
recruitment form). Section 1 is common (personal info + education + "Role
applying for"); each subsequent section belongs to one role.

Roles & their form sections:
  Section 2  Business Development Manager
  Section 3  Deputy General Manager
  Section 4  Management Trainee (Founder's Office)
  Section 5  General Manager – Sales
  Section 6  Sales Manager (Retail)
  Section 7  Operations Supervisor
  Section 8  Field Application Engineer
  Section 9  AI Engineer
  Section 10 Customer Support Executive   (carried over — see below)
  Section 11 Operations Specialist        (carried over — see below)

Two ORIGINAL rubrics (customer_support_executive, operations_specialist) map to
the form's Section 10 / 11 unchanged and keep their original 40 objective + 60
AI split. The EIGHT roles added for the updated form (see DEFAULT_RUBRICS.update
below) use a 30 objective + 70 AI split. Every role totals 100.

Marking scheme is deliberately lenient:
  - Partial credit wherever possible
  - Benefit of the doubt on yes/no questions
  - AI questions reward intent and structure, not perfection
  - Strictness + creativeness are tunable per role (Admin → Scoring tuning)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config import OUTPUT_DIR

logger = logging.getLogger("volt_cv.scoring")
RUBRICS_FILE: Path = OUTPUT_DIR / "rubrics.json"


DEFAULT_RUBRICS: dict = {

    # ─────────────────────────────────────────────────────────────
    # CUSTOMER SUPPORT EXECUTIVE
    # Form: Section 1 (personal/edu) + Section 2 (CSE) + Section 4 (final)
    # ─────────────────────────────────────────────────────────────
    "customer_support_executive": {
        "role_name": "Customer Support Executive",
        "objective_max": 40,
        "ai_max": 60,

        "objective_rules": [

            # ── SECTION 2: Language ──────────────────────────── (10 pts)
            {
                "id": "eng_fluency",
                "label": "English Fluency",
                "match": ["rate your fluency in english", "fluency in english", "english fluency", "english"],
                "strategy": "rating",
                "rating_table": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5},
                "max": 5
            },
            {
                "id": "hindi_fluency",
                "label": "Hindi Fluency",
                "match": ["rate your fluency in hindi", "fluency in hindi", "hindi fluency", "hindi"],
                "strategy": "rating",
                "rating_table": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5},
                "max": 5
            },

            # ── SECTION 2: Other language ─────────────────────── (5 pts)
            # "do you know any other language? If yes, which one?"
            {
                "id": "other_language",
                "label": "Other Language",
                "match": ["other language", "any other language", "which one", "languages known"],
                "strategy": "contains",
                "contains_table": [
                    {"any": ["tamil", "telugu", "kannada", "malayalam", "south indian"], "points": 5},
                    {"any": ["marathi", "bengali", "gujarati", "punjabi", "odia", "assamese"], "points": 4},
                    {"any": ["yes", "french", "german", "spanish", "arabic", "japanese"], "points": 3},
                    {"any": ["no", "none","nil","nothing",  "only hindi", "only english"], "points": 1},
                ],
                "max": 5
            },

            # ── SECTION 2: Experience ─────────────────────────── (20 pts)
            # "Have you handled customer queries over phone, WhatsApp, or chat before?"
            {
                "id": "handled_queries",
                "label": "Handled Customer Queries",
                "match": ["handled customer queries", "queries over phone", "whatsapp", "phone, whatsapp"],
                "strategy": "yesno",
                "yes_points": 5,
                "max": 5
            },
            # "Have you worked in B2C customer service, hospitality, or a support role?"
            # (Most predictive objective signal for a support hire — weighted highest.)
            {
                "id": "b2c_experience",
                "label": "B2C / Support Experience",
                "match": ["b2c customer service", "hospitality", "support role", "b2c", "worked in b2c"],
                "strategy": "yesno",
                "yes_points": 10,
                "max": 10
            },
            # "Have you done follow-up calls to collect feedback or reviews?"
            {
                "id": "followup_calls",
                "label": "Follow-up Calls Experience",
                "match": ["follow-up calls", "follow up calls", "feedback or reviews", "collect feedback"],
                "strategy": "yesno",
                "yes_points": 5,
                "max": 5
            },

            # ── SECTION 1: Graduation (used for basic eligibility) ── (5 pts)
            {
                "id": "graduation",
                "label": "Graduation",
                "match": ["graduation", "degree details", "final cgpa", "graduation / degree"],
                "strategy": "contains",
                "contains_table": [
                    {"any": ["b.tech", "b.e", "b.sc", "b.a", "b.com", "bba", "bca", "mba", "m.tech",
                             "bachelor", "graduate", "completed", "2020", "2021", "2022", "2023", "2024"], "points": 5},
                    {"any": ["pursuing", "final year", "incomplete"], "points": 3},
                    {"any": ["no", "not completed", "dropped"], "points": 0},
                ],
                "max": 5
            },
        ],

        # ── AI QUESTIONS (60 pts total — 3 questions × 20 pts) ──────────

        "ai_questions": [
            # SECTION 2: "How do you handle an angry customer?"
            {
                "id": "q1_angry_customer",
                "total": 20,
                "match": ["angry customer", "handle an angry", "difficult customer"],
                "categories": [
                    {
                        "id": "empathy",
                        "label": "Empathy & Calm",
                        "max": 8,
                        "guide": (
                            "8: Clearly acknowledges frustration, stays calm, shows genuine concern. "
                            "5-7: Mentions listening or understanding but not all three. "
                            "3-4: Some effort to calm the customer but vague. "
                            "1-2: Minimal empathy, mostly dismissive. "
                            "0: No empathy at all, rude or confrontational. "
                            "BE LENIENT — reward intent, not perfect wording."
                        )
                    },
                    {
                        "id": "communication",
                        "label": "Communication",
                        "max": 6,
                        "guide": (
                            "6: Clear structure, professional tone, explains steps. "
                            "4-5: Understandable, mostly clear but missing structure. "
                            "2-3: Answer is there but poorly structured. "
                            "1: Very brief but shows some communication effort. "
                            "0: Incoherent or blank."
                        )
                    },
                    {
                        "id": "resolution",
                        "label": "Resolution Mindset",
                        "max": 6,
                        "guide": (
                            "6: Takes ownership, proposes solution, mentions follow-up. "
                            "4-5: Mentions solving the issue but not follow-up. "
                            "2-3: Shows intent to resolve but no concrete steps. "
                            "1: Vague mention of helping. "
                            "0: No mention of resolution."
                        )
                    },
                ]
            },

            # SECTION 2: "What would you do if a customer reports a payment failure or app issue?"
            {
                "id": "q2_payment_failure",
                "total": 20,
                "match": ["payment failure", "app issue", "payment", "reports a payment"],
                "categories": [
                    {
                        "id": "problem_solving",
                        "label": "Problem Solving",
                        "max": 8,
                        "guide": (
                            "8: Investigates, verifies, logical troubleshooting steps, identifies root cause. "
                            "5-7: Tries to investigate but skips some steps. "
                            "3-4: Shows awareness something needs to be checked. "
                            "1-2: Jumps to solution without investigation. "
                            "0: No problem-solving shown."
                        )
                    },
                    {
                        "id": "escalation_awareness",
                        "label": "Escalation Awareness",
                        "max": 6,
                        "guide": (
                            "6: Knows when to escalate, involves technical team, follows process. "
                            "4-5: Mentions escalation but not clearly structured. "
                            "2-3: Vague mention of getting help. "
                            "1: Some awareness of escalation. "
                            "0: No mention of escalation."
                        )
                    },
                    {
                        "id": "customer_handling",
                        "label": "Customer Handling",
                        "max": 6,
                        "guide": (
                            "6: Keeps customer informed, reassures, professional throughout. "
                            "4-5: Updates customer but not proactively. "
                            "2-3: Some customer communication mentioned. "
                            "1: Acknowledges customer exists. "
                            "0: No mention of keeping customer informed."
                        )
                    },
                ]
            },

            # SECTION 4 (Final): "Why do you want to work in this role?"
            {
                "id": "q3_motivation",
                "total": 20,
                "match": ["why do you want", "want to work in this role", "motivation", "why this role"],
                "categories": [
                    {
                        "id": "motivation",
                        "label": "Motivation & Interest",
                        "max": 8,
                        "guide": (
                            "8: Genuine passion, customer-centric, clear career goal. "
                            "5-7: Shows real interest with some specifics. "
                            "3-4: Generic but positive — 'I like helping people' still counts. "
                            "1-2: Very vague but not negative. "
                            "0: 'Need a job' / purely financial / no interest shown."
                        )
                    },
                    {
                        "id": "role_understanding",
                        "label": "Role Understanding",
                        "max": 6,
                        "guide": (
                            "6: Understands the role involves customer support, EV/tech domain awareness. "
                            "4-5: Understands support role but no domain mention. "
                            "2-3: Vague understanding of what the role is. "
                            "1: Minimal understanding but not wrong. "
                            "0: Completely misunderstands the role."
                        )
                    },
                    {
                        "id": "communication_quality",
                        "label": "Communication Quality",
                        "max": 6,
                        "guide": (
                            "6: Well-written, clear, professional. "
                            "4-5: Mostly clear with minor issues. "
                            "2-3: Understandable but rough. "
                            "1: Very brief but makes sense. "
                            "0: Incoherent."
                        )
                    },
                ]
            },
        ],
    },


    # ─────────────────────────────────────────────────────────────
    # OPERATIONS SPECIALIST
    # Form: Section 1 (personal/edu) + Section 3 (Ops) + Section 4 (final)
    # ─────────────────────────────────────────────────────────────
    "operations_specialist": {
        "role_name": "Operations Specialist",
        "objective_max": 40,
        "ai_max": 60,

        "objective_rules": [

            # ── SECTION 3: Tools & Skills ─────────────────────── (20 pts)
            # "Have you managed live dashboards, routing, or scheduling?"
            {
                "id": "dashboard_exp",
                "label": "Dashboard / Routing / Scheduling",
                "match": ["managed live dashboards", "routing", "scheduling", "dashboard", "live dashboards"],
                "strategy": "yesno",
                "yes_points": 7,
                "max": 7
            },
            # "Rate your proficiency in Excel or Google Sheets" (1-5 scale)
            {
                "id": "excel_rating",
                "label": "Excel / Google Sheets Proficiency",
                "match": ["proficiency in excel", "excel or google sheets", "excel", "google sheets"],
                "strategy": "rating",
                "rating_table": {"1": 1, "2": 3, "3": 5, "4": 7, "5": 8},
                "max": 8
            },
            # "Have you used GPS tracking tools or route planning tools?"
            {
                "id": "gps_tools",
                "label": "GPS / Route Planning Tools",
                "match": ["gps tracking", "route planning", "gps tracking tools", "gps"],
                "strategy": "yesno",
                "yes_points": 5,
                "max": 5
            },

            # ── SECTION 3: Ops Experience ─────────────────────── (10 pts)
            # "Have you prepared daily operational reports before?"
            {
                "id": "ops_reports",
                "label": "Operational Reports Experience",
                "match": ["daily operational reports", "operational reports", "prepared daily"],
                "strategy": "yesno",
                "yes_points": 5,
                "max": 5
            },

            # ── SECTION 1: Graduation ─────────────────────────── (5 pts)
            {
                "id": "graduation",
                "label": "Graduation",
                "match": ["graduation", "degree details", "final cgpa", "graduation / degree"],
                "strategy": "contains",
                "contains_table": [
                    {"any": ["b.tech", "b.e", "b.sc", "b.a", "b.com", "bba", "bca", "mba", "m.tech",
                             "bachelor", "graduate", "completed", "2020", "2021", "2022", "2023", "2024"], "points": 5},
                    {"any": ["pursuing", "final year", "incomplete"], "points": 3},
                    {"any": ["no", "not completed", "dropped"], "points": 0},
                ],
                "max": 5
            },

            # ── SECTION 1: English Fluency (Ops also needs communication) ── (5 pts)
            {
                "id": "eng_fluency",
                "label": "English Proficiency",
                "match": ["rate your fluency in english", "fluency in english", "english fluency", "english"],
                "strategy": "rating",
                "rating_table": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5},
                "max": 5
            },

            # ── SECTION 2 / Shared: Follow-up / reporting habits ── (5 pts)
            # Many ops candidates will have a follow-up reports habit
            {
                "id": "followup_calls",
                "label": "Follow-up / Feedback Experience",
                "match": ["follow-up calls", "follow up calls", "feedback or reviews", "collect feedback"],
                "strategy": "yesno",
                "yes_points": 5,
                "max": 5
            },
        ],

        # ── AI QUESTIONS (60 pts total) ──────────────────────────────────

        "ai_questions": [
            # SECTION 3: "How do you prioritize tasks when multiple requests come in at once?"
            {
                "id": "q1_simultaneous_requests",
                "total": 20,
                "match": ["prioritize tasks", "multiple requests", "come in at once", "simultaneously", "prioritize"],
                "categories": [
                    {
                        "id": "prioritization",
                        "label": "Prioritization",
                        "max": 8,
                        "guide": (
                            "8: Prioritises by urgency, business impact, customer impact — shows clear logic. "
                            "5-7: Has a method but not fully articulated. "
                            "3-4: Tries to prioritise but relies on gut feeling. "
                            "1-2: Shows some awareness tasks have different importance. "
                            "0: First-come-first-served with no reasoning."
                        )
                    },
                    {
                        "id": "decision_making",
                        "label": "Decision Making",
                        "max": 6,
                        "guide": (
                            "6: Clear, decisive, without panic, can make trade-offs. "
                            "4-5: Decisive but could be clearer on reasoning. "
                            "2-3: Shows they can make decisions but not confidently. "
                            "1: Some decision-making indicated. "
                            "0: Indecisive or avoids making calls."
                        )
                    },
                    {
                        "id": "operational_thinking",
                        "label": "Operational Thinking",
                        "max": 6,
                        "guide": (
                            "6: Uses ops vocabulary — dispatch, allocate, queue, schedule, logistics. "
                            "4-5: Ops-aware but informal language. "
                            "2-3: Some structured thinking without ops terms. "
                            "1: General common sense with no ops context. "
                            "0: No operational framing at all."
                        )
                    },
                ]
            },

            # SECTION 3: "What would you do if a charging unit is delayed or a field pilot faces an issue?"
            {
                "id": "q2_charging_delay",
                "total": 20,
                "match": ["charging unit", "delayed", "field pilot", "pilot faces an issue", "charging unit is delayed"],
                "categories": [
                    {
                        "id": "crisis_handling",
                        "label": "Crisis Handling",
                        "max": 8,
                        "guide": (
                            "8: Acts immediately, escalates correctly, owns the issue. "
                            "5-7: Responds urgently and escalates but missing one element. "
                            "3-4: Shows urgency but response is vague. "
                            "1-2: Acknowledges the problem exists. "
                            "0: Passive or dismissive."
                        )
                    },
                    {
                        "id": "escalation_process",
                        "label": "Escalation & Documentation",
                        "max": 6,
                        "guide": (
                            "6: Uses chain of command, documents the incident. "
                            "4-5: Escalates but doesn't mention documentation. "
                            "2-3: Mentions getting help but informally. "
                            "1: Some awareness of needing to inform others. "
                            "0: No escalation awareness."
                        )
                    },
                    {
                        "id": "communication",
                        "label": "Stakeholder Communication",
                        "max": 6,
                        "guide": (
                            "6: Updates both customer and internal team proactively. "
                            "4-5: Updates one side but not both. "
                            "2-3: Mentions communicating but vaguely. "
                            "1: Some mention of informing someone. "
                            "0: No communication plan."
                        )
                    },
                ]
            },

            # SECTION 4 (Final): "Why do you want to work in this role?"
            {
                "id": "q3_motivation",
                "total": 20,
                "match": ["why do you want", "want to work in this role", "motivation", "why this role"],
                "categories": [
                    {
                        "id": "motivation",
                        "label": "Motivation & Interest",
                        "max": 8,
                        "guide": (
                            "8: Genuine passion for operations/logistics, specific goals. "
                            "5-7: Real interest with some specifics about ops. "
                            "3-4: Generic but positive motivation — still counts. "
                            "1-2: Vague but not negative. "
                            "0: No interest or purely financial."
                        )
                    },
                    {
                        "id": "operations_understanding",
                        "label": "Operations Understanding",
                        "max": 6,
                        "guide": (
                            "6: Mentions logistics, fleet, coordination, supply chain, EV ops. "
                            "4-5: Understands it's an ops role but generic. "
                            "2-3: Vague understanding. "
                            "1: At least knows it's not customer support. "
                            "0: Completely off target."
                        )
                    },
                    {
                        "id": "communication_quality",
                        "label": "Communication Quality",
                        "max": 6,
                        "guide": (
                            "6: Well-written, structured, professional. "
                            "4-5: Clear with minor issues. "
                            "2-3: Understandable but rough. "
                            "1: Very brief but makes sense. "
                            "0: Incoherent."
                        )
                    },
                ]
            },
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════════
# ROLES ADDED FOR "Hopcharge Interview Round 0" (updated recruitment form)
#
# The updated Google Form keeps Customer Support Executive (Section 10) and
# Operations Specialist (Section 11) — their sections match the two rubrics
# above unchanged. It ADDS eight new roles, each with a dedicated section.
#
# Marking scheme for every new role: 30 objective + 70 AI = 100.
#   objective : deterministic rules on the section's rating / yes-no / single-
#               choice / multi-select questions (+ a shared graduation check).
#   ai        : the section's situational / descriptive questions, scored by the
#               category rubric guides. Offline (rules engine) has dedicated
#               concept scorers for every category id used below; AI mode reads
#               the same guides. All AI category ids resolve to a real offline
#               scorer in scoring/rules_engine.py CATEGORY_RULES.
# ══════════════════════════════════════════════════════════════════════════

def _grad_rule(max_points: int = 5) -> dict:
    """Shared 'Graduation' objective rule (Section 1) used by the new roles."""
    return {
        "id": "graduation",
        "label": "Graduation",
        "match": ["graduation", "degree details", "final cgpa", "graduation / degree"],
        "strategy": "contains",
        "contains_table": [
            {"any": ["b.tech", "b.e", "b.sc", "b.a", "b.com", "bba", "bca", "mba", "m.tech",
                     "bachelor", "graduate", "completed", "post graduate", "pg",
                     "2019", "2020", "2021", "2022", "2023", "2024", "2025"],
             "points": max_points},
            {"any": ["pursuing", "final year", "incomplete", "appearing"],
             "points": max(0, max_points - 2)},
            {"any": ["no", "not completed", "dropped", "drop out"], "points": 0},
        ],
        "max": max_points,
    }


DEFAULT_RUBRICS.update({

    # ─────────────────────────────────────────────────────────────
    # BUSINESS DEVELOPMENT MANAGER  (Form Section 2)
    # ─────────────────────────────────────────────────────────────
    "business_development_manager": {
        "role_name": "Business Development Manager",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "bd_experience", "label": "Sales / BD Experience",
             "match": ["years of sales/business development experience",
                       "sales/business development experience",
                       "business development experience", "sales experience"],
             "strategy": "contains", "max": 8,
             "contains_table": [
                 {"any": ["5+", "more than 5", "above 5"], "points": 8},
                 {"any": ["3–5", "3-5", "3 to 5"], "points": 7},
                 {"any": ["1–3", "1-3", "1 to 3"], "points": 5},
                 {"any": ["less than 1", "<1"], "points": 3},
                 {"any": ["fresher", "no experience"], "points": 2},
             ]},
            {"id": "sales_types", "label": "Types of Sales Handled",
             "match": ["which type of sales have you handled", "type of sales have you handled",
                       "type of sales"],
             "strategy": "multi", "per": 1, "max": 6,
             "options": ["b2b", "b2c", "enterprise", "channel", "government",
                         "inside sales", "field sales"],
             "bonus": {"any": ["enterprise", "government"], "points": 1}},
            {"id": "negotiation_confidence", "label": "Negotiation Confidence",
             "match": ["confidence in negotiating high-value deals",
                       "confidence in negotiating", "negotiating high-value deals"],
             "strategy": "rating", "max": 6,
             "rating_table": {"1": 1, "2": 2, "3": 4, "4": 5, "5": 6}},
            {"id": "crm_used", "label": "CRM Software Used",
             "match": ["used any crm software", "crm software", "any crm"],
             "strategy": "multi", "per": 2, "max": 5,
             "options": ["salesforce", "hubspot", "zoho", "freshsales", "leadsquared",
                         "dynamics"]},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "bd_largest_client", "total": 14,
             "match": ["describe the largest client you have successfully acquired",
                       "largest client you have successfully acquired", "largest client"],
             "categories": [
                 {"id": "sales_track_record", "label": "Sales Track Record", "max": 9,
                  "guide": ("9: names a real, sizeable client with concrete outcome / deal "
                            "size / revenue. 6-8: real acquisition, some specifics. 3-5: "
                            "vague or small, little proof. 1-2: generic claim. 0: none / "
                            "irrelevant.")},
                 {"id": "communication_quality", "label": "Clarity", "max": 5,
                  "guide": ("5: crisp, structured, credible. 3-4: understandable. 1-2: "
                            "rough. 0: incoherent / blank.")},
             ]},
            {"id": "bd_price_objection", "total": 14,
             "match": ["competitor is 20% cheaper", "20% cheaper", "how will you handle the negotiation",
                       "enterprise client says your competitor"],
             "categories": [
                 {"id": "negotiation", "label": "Negotiation / Value Selling", "max": 9,
                  "guide": ("9: reframes to value/ROI/TCO & differentiation, holds margin, "
                            "explores needs. 6-8: value focus but thin. 3-5: mostly matches "
                            "price / discounts. 1-2: just caves. 0: none.")},
                 {"id": "communication", "label": "Persuasive Communication", "max": 5,
                  "guide": ("5: confident, structured, customer-centric. 3-4: reasonable. "
                            "1-2: weak. 0: none.")},
             ]},
            {"id": "bd_sales_process", "total": 14,
             "match": ["describe your complete sales process", "from generating a lead until closing",
                       "complete sales process"],
             "categories": [
                 {"id": "sales_process", "label": "Sales Process / Pipeline", "max": 9,
                  "guide": ("9: clear lead→qualify→pitch→proposal→negotiate→close→follow-up "
                            "pipeline. 6-8: most stages. 3-5: partial. 1-2: vague. 0: none.")},
                 {"id": "communication", "label": "Structured Explanation", "max": 5,
                  "guide": ("5: logical, step-by-step. 3-4: okay. 1-2: disjointed. 0: none.")},
             ]},
            {"id": "bd_target_recovery", "total": 14,
             "match": ["month's sales target is ₹1 crore", "achieved only ₹20 lakh",
                       "1 crore", "end of week 2"],
             "categories": [
                 {"id": "target_drive", "label": "Target Recovery / Drive", "max": 9,
                  "guide": ("9: concrete catch-up plan — pipeline review, prioritise closable "
                            "deals, ramp activity, upsell. 6-8: solid intent. 3-5: generic "
                            "'work harder'. 1-2: passive. 0: none.")},
                 {"id": "problem_solving", "label": "Analysis of Gap", "max": 5,
                  "guide": ("5: diagnoses why behind, acts on it. 3-4: some analysis. 1-2: "
                            "none. 0: ignores the gap.")},
             ]},
            {"id": "bd_new_opportunities", "total": 14,
             "match": ["identify new business opportunities for hopcharge",
                       "new business opportunities", "business opportunities for hopcharge"],
             "categories": [
                 {"id": "prospecting", "label": "Opportunity Identification", "max": 9,
                  "guide": ("9: concrete channels — segments, partnerships, corporate/fleet, "
                            "referrals, market research relevant to EV charging. 6-8: some "
                            "real ideas. 3-5: generic. 1-2: vague. 0: none.")},
                 {"id": "sales_motivation", "label": "Drive / Ownership", "max": 5,
                  "guide": ("5: proactive, hunter mindset. 3-4: willing. 1-2: passive. 0: none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # DEPUTY GENERAL MANAGER  (Form Section 3 — sales leadership)
    # ─────────────────────────────────────────────────────────────
    "deputy_general_manager": {
        "role_name": "Deputy General Manager",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "dgm_experience", "label": "Sales Leadership Experience",
             "match": ["total years of experience in sales leadership",
                       "years of experience in sales leadership", "sales leadership"],
             "strategy": "contains", "max": 7,
             "contains_table": [
                 {"any": ["10+", "more than 10", "above 10"], "points": 7},
                 {"any": ["6–10", "6-10"], "points": 6},
                 {"any": ["4–6", "4-6"], "points": 5},
                 {"any": ["2–4", "2-4"], "points": 3},
                 {"any": ["less than 2", "<2"], "points": 2},
             ]},
            {"id": "dgm_reports", "label": "Direct Reports",
             "match": ["how many people have directly reported to you",
                       "people have directly reported to you", "directly reported"],
             "strategy": "contains", "max": 6,
             "contains_table": [
                 {"any": ["more than 20", "20+"], "points": 6},
                 {"any": ["11–20", "11-20"], "points": 5},
                 {"any": ["6–10", "6-10"], "points": 4},
                 {"any": ["1–5", "1-5"], "points": 2},
                 {"any": ["none"], "points": 0},
             ]},
            {"id": "dgm_channels", "label": "Sales Channels Managed",
             "match": ["which sales channels have you managed", "sales channels have you managed",
                       "sales channels"],
             "strategy": "multi", "per": 1, "max": 7,
             "options": ["b2b", "b2c", "channel", "corporate", "government", "oem", "dealer"],
             "bonus": {"any": ["corporate", "oem", "government"], "points": 1}},
            {"id": "dgm_revenue_resp", "label": "Revenue Target Responsibility",
             "match": ["responsible for revenue targets", "been responsible for revenue"],
             "strategy": "yesno", "yes_points": 5, "max": 5},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "dgm_largest_target", "total": 14,
             "match": ["largest annual revenue target you personally managed",
                       "annual revenue target you personally", "revenue target you personally managed"],
             "categories": [
                 {"id": "revenue_ownership", "label": "Revenue Ownership", "max": 9,
                  "guide": ("9: quantified target owned (crore/₹) with delivery context. 6-8: "
                            "real number, thin context. 3-5: vague scale. 1-2: no ownership. "
                            "0: none.")},
                 {"id": "leadership_readiness", "label": "Leadership Scale", "max": 5,
                  "guide": ("5: led sizeable team/territory to the number. 3-4: some. 1-2: "
                            "minimal. 0: none.")},
             ]},
            {"id": "dgm_revenue_strategy", "total": 14,
             "match": ["one sales strategy that significantly increased revenue",
                       "sales strategy that significantly increased revenue",
                       "significantly increased revenue"],
             "categories": [
                 {"id": "sales_strategy", "label": "Revenue Strategy", "max": 9,
                  "guide": ("9: specific strategy (GTM, segment, channel, pricing) with cause→"
                            "effect on revenue. 6-8: real but generic. 3-5: buzzwords. 1-2: "
                            "vague. 0: none.")},
                 {"id": "communication_quality", "label": "Clarity", "max": 5,
                  "guide": ("5: crisp, structured. 3-4: okay. 1-2: rough. 0: none.")},
             ]},
            {"id": "dgm_region_underperf", "total": 14,
             "match": ["one region continuously misses its targets while another exceeds",
                       "region continuously misses its targets", "region continuously misses"],
             "categories": [
                 {"id": "team_leadership", "label": "Team / Region Turnaround", "max": 9,
                  "guide": ("9: diagnoses root cause, coaches, sets targets, replicates the "
                            "winning region's playbook. 6-8: solid. 3-5: generic. 1-2: blames. "
                            "0: none.")},
                 {"id": "performance_improvement", "label": "Corrective Action", "max": 5,
                  "guide": ("5: concrete measurable actions. 3-4: some. 1-2: none. 0: ignores.")},
             ]},
            {"id": "dgm_key_client_churn", "total": 14,
             "match": ["key enterprise client worth ₹10 crore annually threatens to terminate",
                       "worth ₹10 crore annually", "threatens to terminate the contract"],
             "categories": [
                 {"id": "key_account_retention", "label": "Account Retention", "max": 9,
                  "guide": ("9: root-cause the dissatisfaction, service-recovery plan, senior "
                            "engagement, rebuild trust. 6-8: solid. 3-5: generic apology. 1-2: "
                            "passive. 0: none.")},
                 {"id": "crisis_handling", "label": "Urgency / Ownership", "max": 5,
                  "guide": ("5: acts immediately, owns it. 3-4: some urgency. 1-2: slow. 0: none.")},
             ]},
            {"id": "dgm_forecast", "total": 14,
             "match": ["how do you forecast quarterly sales", "forecast quarterly sales",
                       "forecast quarterly"],
             "categories": [
                 {"id": "forecasting", "label": "Forecasting Method", "max": 9,
                  "guide": ("9: pipeline/weighted stages, conversion & historical data, run-rate. "
                            "6-8: some method. 3-5: gut feel. 1-2: vague. 0: none.")},
                 {"id": "data_analysis", "label": "Data Rigour", "max": 5,
                  "guide": ("5: data-driven, uses CRM/metrics. 3-4: some. 1-2: none. 0: none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # MANAGEMENT TRAINEE (Founder's Office)  (Form Section 4)
    # ─────────────────────────────────────────────────────────────
    "management_trainee_founders_office": {
        "role_name": "Management Trainee (Founder's Office)",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "mt_excel", "label": "Excel Proficiency",
             "match": ["proficiency in microsoft excel", "microsoft excel"],
             "strategy": "rating", "max": 7,
             "rating_table": {"1": 1, "2": 3, "3": 4, "4": 6, "5": 7}},
            {"id": "mt_ppt", "label": "PowerPoint Proficiency",
             "match": ["proficiency in powerpoint presentations", "powerpoint presentations",
                       "powerpoint"],
             "strategy": "rating", "max": 6,
             "rating_table": {"1": 1, "2": 2, "3": 4, "4": 5, "5": 6}},
            {"id": "mt_coordinated", "label": "Coordinated a Project",
             "match": ["coordinated multiple people to complete a project",
                       "coordinated multiple people", "coordinated multiple"],
             "strategy": "yesno", "yes_points": 4, "max": 4},
            {"id": "mt_founders", "label": "Worked with Founders / Senior Mgmt",
             "match": ["worked directly with founders, senior management or department heads",
                       "worked directly with founders", "directly with founders"],
             "strategy": "yesno", "yes_points": 8, "max": 8},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "mt_little_guidance", "total": 14,
             "match": ["given very little guidance but still completed the task",
                       "very little guidance but still completed", "very little guidance"],
             "categories": [
                 {"id": "resourcefulness", "label": "Resourcefulness / Ownership", "max": 9,
                  "guide": ("9: real example of figuring it out with minimal direction, took "
                            "initiative, delivered. 6-8: solid. 3-5: generic. 1-2: needed "
                            "hand-holding. 0: none.")},
                 {"id": "achievement", "label": "Result Delivered", "max": 5,
                  "guide": ("5: clear successful outcome. 3-4: some. 1-2: unclear. 0: none.")},
             ]},
            {"id": "mt_prioritise", "total": 14,
             "match": ["founder asks you to prepare a report by 6 pm",
                       "another department asks for urgent help", "prepare a report by 6 pm"],
             "categories": [
                 {"id": "prioritization", "label": "Prioritisation", "max": 9,
                  "guide": ("9: clear logic — the Founder's deadline vs urgency/impact, "
                            "communicates & sets expectations, protects both. 6-8: reasonable. "
                            "3-5: gut feel. 1-2: freezes. 0: none.")},
                 {"id": "stakeholder_management", "label": "Expectation Management", "max": 5,
                  "guide": ("5: proactively aligns/negotiates with both. 3-4: some. 1-2: none. "
                            "0: ignores.")},
             ]},
            {"id": "mt_data_problem", "total": 14,
             "match": ["solved a business problem using data", "business problem using data",
                       "time you solved a business problem"],
             "categories": [
                 {"id": "data_analysis", "label": "Data-Driven Problem Solving", "max": 9,
                  "guide": ("9: concrete data, analysis method, insight → decision → result. "
                            "6-8: solid. 3-5: mentions data loosely. 1-2: no data. 0: none.")},
                 {"id": "communication_quality", "label": "Clarity", "max": 5,
                  "guide": ("5: crisp narrative. 3-4: okay. 1-2: rough. 0: none.")},
             ]},
            {"id": "mt_three_heads", "total": 14,
             "match": ["three department heads asked you for different deliverables",
                       "three department heads", "different deliverables at the same time"],
             "categories": [
                 {"id": "stakeholder_management", "label": "Stakeholder Juggling", "max": 9,
                  "guide": ("9: clarifies priority/urgency, aligns expectations, escalates to "
                            "Founder if needed, negotiates realistic timelines. 6-8: solid. "
                            "3-5: generic. 1-2: overwhelmed. 0: none.")},
                 {"id": "decision_making", "label": "Decisiveness", "max": 5,
                  "guide": ("5: makes a clear call calmly. 3-4: some. 1-2: indecisive. 0: none.")},
             ]},
            {"id": "mt_why_founders_office", "total": 14,
             # Apostrophe-free fallbacks included so the match survives Google
             # Forms auto-converting the straight ' in "Founder's" to a curly ’.
             "match": ["work in a founder's office instead of a normal corporate",
                       "office instead of a normal corporate role",
                       "instead of a normal corporate role",
                       "normal corporate role", "founder's office instead"],
             "categories": [
                 {"id": "founder_fit", "label": "Founder's-Office Fit", "max": 9,
                  "guide": ("9: understands fast-paced, generalist, high-ownership, 0→1, "
                            "learning exposure, direct impact. 6-8: partial. 3-5: generic. "
                            "1-2: shallow. 0: misunderstands.")},
                 {"id": "motivation", "label": "Genuine Motivation", "max": 5,
                  "guide": ("5: authentic, specific. 3-4: positive but generic. 1-2: vague. "
                            "0: purely financial / none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # GENERAL MANAGER – SALES  (Form Section 5)
    # ─────────────────────────────────────────────────────────────
    "general_manager_sales": {
        "role_name": "General Manager – Sales",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "gm_leadership_years", "label": "Leadership Experience",
             "match": ["total years of leadership experience", "years of leadership experience",
                       "leadership experience"],
             "strategy": "contains", "max": 7,
             "contains_table": [
                 {"any": ["15+", "more than 15", "above 15"], "points": 7},
                 {"any": ["10–15", "10-15"], "points": 6},
                 {"any": ["8–10", "8-10"], "points": 5},
                 {"any": ["5–7", "5-7"], "points": 3},
                 {"any": ["less than 5", "<5"], "points": 2},
             ]},
            {"id": "gm_revenue_managed", "label": "Annual Revenue Owned",
             "match": ["what annual revenue have you been directly responsible for",
                       "annual revenue have you been directly responsible",
                       "revenue have you been directly responsible"],
             "strategy": "contains", "max": 7,
             "contains_table": [
                 {"any": ["above ₹100", "above 100", "100 crore", "more than 100"], "points": 7},
                 {"any": ["50–100", "50-100", "₹50–100"], "points": 6},
                 {"any": ["20–50", "20-50", "₹20–50"], "points": 5},
                 {"any": ["5–20", "5-20", "₹5–20"], "points": 3},
                 {"any": ["less than ₹5", "less than 5"], "points": 2},
             ]},
            {"id": "gm_team_size", "label": "Sales Professionals Led",
             "match": ["how many sales professionals have you led",
                       "sales professionals have you led", "professionals have you led"],
             "strategy": "contains", "max": 6,
             "contains_table": [
                 {"any": ["more than 50", "50+"], "points": 6},
                 {"any": ["26–50", "26-50"], "points": 5},
                 {"any": ["11–25", "11-25"], "points": 4},
                 {"any": ["1–10", "1-10"], "points": 2},
                 {"any": ["none"], "points": 0},
             ]},
            {"id": "gm_segments", "label": "Business Segments Handled",
             "match": ["which business segments have you handled", "business segments have you handled",
                       "business segments"],
             "strategy": "multi", "per": 1, "max": 5,
             "options": ["enterprise", "b2c", "government", "fleet", "channel", "oem",
                         "strategic", "international"],
             "bonus": {"any": ["enterprise", "international", "strategic"], "points": 1}},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "gm_growth_initiative", "total": 14,
             "match": ["biggest revenue growth initiative you have ever led",
                       "revenue growth initiative you have ever led", "biggest revenue growth initiative"],
             "categories": [
                 {"id": "revenue_ownership", "label": "Revenue Growth Owned", "max": 9,
                  "guide": ("9: quantified growth initiative personally led with results. 6-8: "
                            "real, thin numbers. 3-5: vague. 1-2: no ownership. 0: none.")},
                 {"id": "sales_strategy", "label": "Strategic Depth", "max": 5,
                  "guide": ("5: clear strategic levers. 3-4: some. 1-2: buzzwords. 0: none.")},
             ]},
            {"id": "gm_three_metros", "total": 14,
             "match": ["expand into three new metropolitan cities within the next 12 months",
                       "three new metropolitan cities", "build the sales strategy"],
             "categories": [
                 {"id": "sales_strategy", "label": "Go-to-Market Strategy", "max": 9,
                  "guide": ("9: phased city launch, segmentation, hiring/team, channels, "
                            "targets, EV-charging fit. 6-8: solid. 3-5: generic. 1-2: vague. "
                            "0: none.")},
                 {"id": "leadership_readiness", "label": "Execution Leadership", "max": 5,
                  "guide": ("5: owns execution, teams, milestones. 3-4: some. 1-2: minimal. "
                            "0: none.")},
             ]},
            {"id": "gm_enterprise_unhappy", "total": 14,
             "match": ["largest enterprise customer is unhappy with service quality",
                       "unhappy with service quality", "considering switching to a competitor"],
             "categories": [
                 {"id": "key_account_retention", "label": "Account Save", "max": 9,
                  "guide": ("9: diagnose, service-recovery, senior engagement, SLA, rebuild "
                            "trust, prevent churn. 6-8: solid. 3-5: generic. 1-2: passive. "
                            "0: none.")},
                 {"id": "crisis_handling", "label": "Urgency / Ownership", "max": 5,
                  "guide": ("5: immediate ownership. 3-4: some. 1-2: slow. 0: none.")},
             ]},
            {"id": "gm_sixty_percent", "total": 14,
             "match": ["achieved only 60% of its quarterly sales target",
                       "60% of its quarterly sales target", "over the next 90 days"],
             "categories": [
                 {"id": "target_drive", "label": "90-Day Recovery Plan", "max": 9,
                  "guide": ("9: structured 90-day plan — pipeline, prioritise, coach team, "
                            "remove blockers, weekly review. 6-8: solid. 3-5: generic. 1-2: "
                            "vague. 0: none.")},
                 {"id": "team_leadership", "label": "Mobilising the Team", "max": 5,
                  "guide": ("5: rallies & manages team performance. 3-4: some. 1-2: none. "
                            "0: none.")},
             ]},
            {"id": "gm_weekly_kpis", "total": 14,
             "match": ["which kpis would you monitor every week", "kpis would you monitor every week",
                       "monitor every week"],
             "categories": [
                 {"id": "forecasting", "label": "KPI / Pipeline Command", "max": 9,
                  "guide": ("9: right leading & lagging KPIs — pipeline, conversion, win-rate, "
                            "revenue vs target, activity. 6-8: several. 3-5: a couple. 1-2: "
                            "vague. 0: none.")},
                 {"id": "data_analysis", "label": "Data Orientation", "max": 5,
                  "guide": ("5: measurable, data-driven. 3-4: some. 1-2: none. 0: none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # SALES MANAGER (Retail)  (Form Section 6 — inside/retail sales)
    # ─────────────────────────────────────────────────────────────
    "sales_manager_retail": {
        "role_name": "Sales Manager (Retail)",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "sm_experience", "label": "Total Sales Experience",
             "match": ["total sales experience", "sales experience"],
             "strategy": "contains", "max": 9,
             "contains_table": [
                 {"any": ["more than 5", "5+"], "points": 9},
                 {"any": ["3–5", "3-5"], "points": 7},
                 {"any": ["1–3", "1-3"], "points": 5},
                 {"any": ["less than 1", "<1"], "points": 3},
                 {"any": ["fresher"], "points": 2},
             ]},
            {"id": "sm_channels", "label": "Sales Channels Worked In",
             "match": ["which sales channels have you worked in", "sales channels have you worked in",
                       "sales channels"],
             "strategy": "multi", "per": 1, "max": 8,
             "options": ["inside sales", "digital", "inbound", "outbound", "retail",
                         "subscription", "saas", "ev industry", "mobility", "fintech"],
             "bonus": {"any": ["saas", "subscription", "ev industry"], "points": 1}},
            {"id": "sm_crm", "label": "CRM Platforms Used",
             "match": ["which crm platforms have you used", "crm platforms have you used",
                       "crm platforms"],
             "strategy": "multi", "per": 2, "max": 8,
             "options": ["salesforce", "hubspot", "zoho", "freshsales", "leadsquared",
                         "dynamics"]},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "sm_think_about_it", "total": 14,
             "match": ["i'll think about it", "convert this customer without sounding pushy",
                       "without sounding pushy"],
             "categories": [
                 {"id": "negotiation", "label": "Objection Handling / Close", "max": 9,
                  "guide": ("9: uncovers the real hesitation, adds value, creates urgency "
                            "without pressure, asks for the close. 6-8: solid. 3-5: generic. "
                            "1-2: pushy or gives up. 0: none.")},
                 {"id": "customer_handling", "label": "Customer Rapport", "max": 5,
                  "guide": ("5: warm, trust-building. 3-4: okay. 1-2: weak. 0: none.")},
             ]},
            {"id": "sm_conversion_drop", "total": 14,
             "match": ["conversion rate has dropped from 28% to 16%", "28% to 16%",
                       "how would you investigate and improve it"],
             "categories": [
                 {"id": "data_analysis", "label": "Diagnose the Drop", "max": 9,
                  "guide": ("9: segments the funnel, checks lead quality/source/team/pitch, "
                            "uses data to find cause. 6-8: solid. 3-5: guesses. 1-2: vague. "
                            "0: none.")},
                 {"id": "performance_improvement", "label": "Improvement Actions", "max": 5,
                  "guide": ("5: concrete corrective actions. 3-4: some. 1-2: none. 0: none.")},
             ]},
            {"id": "sm_inside_process", "total": 14,
             "match": ["describe your complete inside-sales process",
                       "from receiving a lead until onboarding", "inside-sales process"],
             "categories": [
                 {"id": "sales_process", "label": "Inside-Sales Process", "max": 9,
                  "guide": ("9: lead→qualify→pitch→follow-up→close→onboarding with CRM "
                            "discipline. 6-8: most stages. 3-5: partial. 1-2: vague. 0: none.")},
                 {"id": "communication", "label": "Structured Explanation", "max": 5,
                  "guide": ("5: logical & clear. 3-4: okay. 1-2: rough. 0: none.")},
             ]},
            {"id": "sm_improve_exec", "total": 14,
             "match": ["inside sales executive consistently missing targets",
                       "improve the performance of an inside sales executive",
                       "consistently missing targets"],
             "categories": [
                 {"id": "team_leadership", "label": "Coaching / Performance Mgmt", "max": 9,
                  "guide": ("9: diagnoses root cause, coaches, shadows calls, sets targets & "
                            "reviews, motivates. 6-8: solid. 3-5: generic. 1-2: just pressure. "
                            "0: none.")},
                 {"id": "target_drive", "label": "Results Orientation", "max": 5,
                  "guide": ("5: focused on measurable improvement. 3-4: some. 1-2: none. "
                            "0: none.")},
             ]},
            {"id": "sm_why_hire", "total": 14,
             "match": ["why should hopcharge hire you for this role", "why should hopcharge hire you",
                       "should hopcharge hire you"],
             "categories": [
                 {"id": "sales_motivation", "label": "Drive / Fit", "max": 9,
                  "guide": ("9: target-driven, resilient, relevant proof & genuine fit for a "
                            "retail/inside sales role. 6-8: solid. 3-5: generic. 1-2: weak. "
                            "0: none.")},
                 {"id": "communication_quality", "label": "Persuasiveness", "max": 5,
                  "guide": ("5: confident & clear. 3-4: okay. 1-2: weak. 0: none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # OPERATIONS SUPERVISOR  (Form Section 7)
    # ─────────────────────────────────────────────────────────────
    "operations_supervisor": {
        "role_name": "Operations Supervisor",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "os_experience", "label": "Operations / Fleet Experience",
             "match": ["years of experience do you have in operations, logistics, fleet",
                       "operations, logistics, fleet management or mobility",
                       "operations, logistics"],
             "strategy": "contains", "max": 8,
             "contains_table": [
                 {"any": ["more than 8", "8+"], "points": 8},
                 {"any": ["5–8", "5-8"], "points": 7},
                 {"any": ["2–5", "2-5"], "points": 5},
                 {"any": ["less than 2", "<2"], "points": 3},
                 {"any": ["fresher"], "points": 2},
             ]},
            {"id": "os_areas", "label": "Operational Areas Managed",
             "match": ["which operational areas have you managed", "operational areas have you managed",
                       "operational areas"],
             "strategy": "multi", "per": 1, "max": 7,
             "options": ["fleet", "logistics", "field", "shift", "team supervision", "sla",
                         "vendor", "escalation"],
             "bonus": {"any": ["sla", "fleet", "vendor"], "points": 1}},
            {"id": "os_excel", "label": "Excel / Sheets Proficiency",
             "match": ["proficiency in microsoft excel / google sheets",
                       "excel / google sheets", "excel or google sheets", "google sheets"],
             "strategy": "rating", "max": 6,
             "rating_table": {"1": 1, "2": 2, "3": 4, "4": 5, "5": 6}},
            {"id": "os_mis", "label": "MIS / Dashboard Reporting",
             "match": ["prepared mis reports or operational dashboards",
                       "mis reports or operational dashboards", "mis reports"],
             "strategy": "contains", "max": 4,
             "contains_table": [
                 {"any": ["daily"], "points": 4},
                 {"any": ["weekly"], "points": 3},
                 {"any": ["monthly"], "points": 2},
                 {"any": ["never"], "points": 0},
             ]},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "os_operator_late", "total": 14,
             "match": ["will be two hours late for an important customer session",
                       "charging operator informs you", "two hours late"],
             "categories": [
                 {"id": "crisis_handling", "label": "Crisis Response", "max": 9,
                  "guide": ("9: acts immediately — arrange backup/reschedule, own it, minimise "
                            "customer impact. 6-8: urgent, one gap. 3-5: vague. 1-2: passive. "
                            "0: none.")},
                 {"id": "stakeholder_communication", "label": "Customer + Team Comms", "max": 5,
                  "guide": ("5: proactively informs customer AND team. 3-4: one side. 1-2: "
                            "minimal. 0: none.")},
             ]},
            {"id": "os_fleet_util", "total": 14,
             "match": ["fleet utilization has dropped from 92% to 70%", "92% to 70%",
                       "fleet utilization"],
             "categories": [
                 {"id": "problem_solving", "label": "Root-Cause Investigation", "max": 9,
                  "guide": ("9: investigates causes — downtime, demand, routing, staffing — "
                            "with data. 6-8: solid. 3-5: guesses. 1-2: vague. 0: none.")},
                 {"id": "performance_improvement", "label": "Improvement Plan", "max": 5,
                  "guide": ("5: concrete actions to restore utilisation. 3-4: some. 1-2: none. "
                            "0: none.")},
             ]},
            {"id": "os_customer_escalation", "total": 14,
             "match": ["your process for handling a customer escalation",
                       "handling a customer escalation", "customer escalation"],
             "categories": [
                 {"id": "escalation_process", "label": "Escalation Process", "max": 9,
                  "guide": ("9: acknowledge, own, resolve, escalate via chain, document, "
                            "follow-up. 6-8: most. 3-5: partial. 1-2: vague. 0: none.")},
                 {"id": "customer_handling", "label": "Customer Handling", "max": 5,
                  "guide": ("5: keeps customer informed & reassured. 3-4: some. 1-2: minimal. "
                            "0: none.")},
             ]},
            {"id": "os_sla_miss", "total": 14,
             "match": ["team repeatedly misses sla targets", "repeatedly misses sla targets",
                       "how will you improve performance"],
             "categories": [
                 {"id": "performance_improvement", "label": "SLA Performance Fix", "max": 9,
                  "guide": ("9: root-cause, targets, monitoring, coaching, process/SOP fixes, "
                            "accountability. 6-8: solid. 3-5: generic. 1-2: just pressure. "
                            "0: none.")},
                 {"id": "team_leadership", "label": "Team Management", "max": 5,
                  "guide": ("5: motivates & manages the team. 3-4: some. 1-2: none. 0: none.")},
             ]},
            {"id": "os_daily_kpis", "total": 14,
             "match": ["kpis would you monitor every day as an operations supervisor",
                       "monitor every day as an operations supervisor", "monitor every day"],
             "categories": [
                 {"id": "operational_thinking", "label": "Ops KPI Command", "max": 9,
                  "guide": ("9: relevant daily KPIs — utilisation, SLA, uptime, on-time, "
                            "escalations, cost. 6-8: several. 3-5: a couple. 1-2: vague. "
                            "0: none.")},
                 {"id": "data_analysis", "label": "Metrics Orientation", "max": 5,
                  "guide": ("5: measurable, data-driven. 3-4: some. 1-2: none. 0: none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # FIELD APPLICATION ENGINEER  (Form Section 8)
    # ─────────────────────────────────────────────────────────────
    "field_application_engineer": {
        "role_name": "Field Application Engineer",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "fae_familiarity", "label": "Electrical Systems Familiarity",
             "match": ["how familiar are you with electrical systems and components",
                       "familiar are you with electrical systems", "electrical systems and components"],
             "strategy": "rating", "max": 8,
             "rating_table": {"1": 1, "2": 3, "3": 5, "4": 7, "5": 8}},
            {"id": "fae_components", "label": "Components Worked On",
             # NB: "lithium" is NOT a counting option — the form label
             # "Lithium-ion Batteries" already contains "batter", so counting
             # both would double-score one checkbox. Lithium is rewarded via the
             # bonus instead (specialised skill), keeping one tick = one point.
             "match": ["which of the following have you worked on", "have you worked on"],
             "strategy": "multi", "per": 1, "max": 8,
             "options": ["panel", "wiring", "motor", "batter", "charger",
                         "testing", "multimeter"],
             "bonus": {"any": ["lithium", "charger", "multimeter"], "points": 1}},
            {"id": "fae_installation", "label": "Installation / Commissioning",
             "match": ["participated in installation, testing or commissioning",
                       "installation, testing or commissioning", "commissioning activities"],
             "strategy": "contains", "max": 4,
             "contains_table": [
                 {"any": ["frequently"], "points": 4},
                 {"any": ["sometimes"], "points": 3},
                 {"any": ["internship", "college"], "points": 2},
                 {"any": ["never"], "points": 0},
             ]},
            {"id": "fae_excel", "label": "Excel / Word / Sheets Proficiency",
             "match": ["proficiency in microsoft excel, word and google sheets",
                       "microsoft excel, word and google sheets", "excel, word"],
             "strategy": "rating", "max": 5,
             "rating_table": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5}},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "fae_system_stops", "total": 14,
             "match": ["charging system suddenly stops working after successful installation",
                       "suddenly stops working after successful installation",
                       "explain your troubleshooting process"],
             "categories": [
                 {"id": "troubleshooting", "label": "Troubleshooting Process", "max": 9,
                  "guide": ("9: systematic — check power/supply, connections, error codes, "
                            "isolate the fault, test with instruments, rule out step by step. "
                            "6-8: solid. 3-5: partial. 1-2: guesswork. 0: none.")},
                 {"id": "technical_knowledge", "label": "Technical Grounding", "max": 5,
                  "guide": ("5: correct electrical/charging concepts. 3-4: some. 1-2: shaky. "
                            "0: none.")},
             ]},
            {"id": "fae_inspection", "total": 14,
             "match": ["inspect an electrical installation before customer handover",
                       "describe your inspection checklist", "before customer handover"],
             "categories": [
                 {"id": "inspection_quality", "label": "Inspection Checklist / QA", "max": 9,
                  "guide": ("9: structured checklist — earthing/insulation/load tests, "
                            "connections, ratings, labelling, functional trial, documentation. "
                            "6-8: solid. 3-5: partial. 1-2: vague. 0: none.")},
                 {"id": "safety_awareness", "label": "Safety in Inspection", "max": 5,
                  "guide": ("5: safety checks integral. 3-4: some. 1-2: minimal. 0: none.")},
             ]},
            {"id": "fae_battery_drop", "total": 14,
             "match": ["battery performance has dropped significantly after two months",
                       "battery performance has dropped", "how would you investigate the issue"],
             "categories": [
                 {"id": "technical_knowledge", "label": "Battery / Systems Knowledge", "max": 9,
                  "guide": ("9: relevant reasoning — charge cycles, BMS, temperature, load, "
                            "cell health, usage pattern, measurements. 6-8: solid. 3-5: "
                            "partial. 1-2: shaky. 0: none.")},
                 {"id": "troubleshooting", "label": "Investigation Method", "max": 5,
                  "guide": ("5: systematic diagnosis. 3-4: some. 1-2: guesswork. 0: none.")},
             ]},
            {"id": "fae_tech_problem", "total": 14,
             "match": ["technical problem you solved during your internship, project or college",
                       "technical problem you solved", "internship, project or college laboratory"],
             "categories": [
                 {"id": "problem_solving", "label": "Technical Problem Solving", "max": 9,
                  "guide": ("9: real problem, logical diagnosis, action, outcome. 6-8: solid. "
                            "3-5: generic. 1-2: vague. 0: none.")},
                 {"id": "technical_knowledge", "label": "Technical Depth", "max": 5,
                  "guide": ("5: sound technical detail. 3-4: some. 1-2: thin. 0: none.")},
             ]},
            {"id": "fae_safety", "total": 14,
             "match": ["safety precautions should always be followed while working on electrical",
                       "safety precautions should always be followed", "working on electrical systems"],
             "categories": [
                 {"id": "safety_awareness", "label": "Electrical Safety", "max": 9,
                  "guide": ("9: power-off/isolate supply, PPE, insulated tools, earthing, "
                            "lockout, test-before-touch, no live working. 6-8: several. 3-5: "
                            "a couple. 1-2: vague. 0: none.")},
                 {"id": "technical_knowledge", "label": "Correctness", "max": 5,
                  "guide": ("5: technically correct precautions. 3-4: mostly. 1-2: shaky. "
                            "0: none.")},
             ]},
        ],
    },

    # ─────────────────────────────────────────────────────────────
    # AI ENGINEER  (Form Section 9)
    # ─────────────────────────────────────────────────────────────
    "ai_engineer": {
        "role_name": "AI Engineer",
        "objective_max": 30, "ai_max": 70,
        "objective_rules": [
            {"id": "ai_technologies", "label": "AI/ML Technologies",
             "match": ["which ai/ml technologies have you worked with",
                       "ai/ml technologies have you worked with", "technologies have you worked with"],
             "strategy": "multi", "per": 1, "max": 8,
             "options": ["python", "tensorflow", "pytorch", "scikit", "opencv", "langchain",
                         "llamaindex", "hugging face", "openai", "claude", "gemini"],
             "bonus": {"any": ["pytorch", "langchain", "openai", "hugging face"], "points": 1}},
            {"id": "ai_python", "label": "Python Proficiency",
             "match": ["rate your python proficiency", "python proficiency"],
             "strategy": "rating", "max": 7,
             "rating_table": {"1": 1, "2": 3, "3": 4, "4": 6, "5": 7}},
            {"id": "ai_from_scratch", "label": "Built AI/ML Project from Scratch",
             "match": ["have you built an ai/ml project from scratch",
                       "built an ai/ml project from scratch", "project from scratch"],
             "strategy": "contains", "max": 5,
             "contains_table": [
                 {"any": ["multiple production", "production projects"], "points": 5},
                 {"any": ["personal projects"], "points": 4},
                 {"any": ["internship projects"], "points": 4},
                 {"any": ["academic projects"], "points": 3},
                 {"any": ["never"], "points": 0},
             ]},
            {"id": "ai_areas", "label": "AI Areas Worked On",
             "match": ["which areas have you worked on", "areas have you worked on"],
             "strategy": "multi", "per": 1, "max": 5,
             "options": ["machine learning", "deep learning", "nlp", "computer vision",
                         "recommendation", "generative", "rag", "chatbot", "predictive"],
             "bonus": {"any": ["generative", "rag", "nlp"], "points": 1}},
            _grad_rule(5),
        ],
        "ai_questions": [
            {"id": "ai_accuracy_drop", "total": 14,
             "match": ["deployed ai model suddenly drops from 95% accuracy to 72%",
                       "95% accuracy to 72%", "how would you investigate"],
             "categories": [
                 {"id": "ml_debugging", "label": "Model Debugging", "max": 9,
                  "guide": ("9: checks data drift / data quality / pipeline / features / "
                            "label issues, compares to baseline, error analysis, retrain. "
                            "6-8: solid. 3-5: partial. 1-2: guesswork. 0: none.")},
                 {"id": "data_analysis", "label": "Analytical Rigour", "max": 5,
                  "guide": ("5: metric-driven, systematic. 3-4: some. 1-2: none. 0: none.")},
             ]},
            {"id": "ai_challenging_project", "total": 14,
             "match": ["most challenging ai project you have built", "most challenging ai project",
                       "challenging ai project"],
             "categories": [
                 {"id": "ai_project_depth", "label": "Project Depth", "max": 9,
                  "guide": ("9: end-to-end — data, modelling, tuning, evaluation, deployment, "
                            "real challenge & result. 6-8: solid. 3-5: shallow. 1-2: vague. "
                            "0: none.")},
                 {"id": "communication_quality", "label": "Clarity", "max": 5,
                  "guide": ("5: explains technically & clearly. 3-4: okay. 1-2: rough. 0: none.")},
             ]},
            {"id": "ai_hr_chatbot", "total": 14,
             "match": ["build an ai chatbot for hopcharge's hr recruitment platform",
                       "ai chatbot for hopcharge", "hr recruitment platform"],
             "categories": [
                 {"id": "ai_solution_design", "label": "Solution Design", "max": 9,
                  "guide": ("9: sensible architecture — LLM/RAG, knowledge base, embeddings, "
                            "intents, integration, guardrails, evaluation. 6-8: solid. 3-5: "
                            "surface. 1-2: vague. 0: none.")},
                 {"id": "ai_ethics_bias", "label": "Fairness / Guardrails", "max": 5,
                  "guide": ("5: addresses bias, human-in-loop, privacy for HR use. 3-4: some. "
                            "1-2: none. 0: ignores.")},
             ]},
            {"id": "ai_ml_vs_dl", "total": 14,
             "match": ["difference between machine learning and deep learning",
                       "machine learning and deep learning"],
             "categories": [
                 {"id": "ml_fundamentals", "label": "ML/DL Fundamentals", "max": 9,
                  "guide": ("9: correct — DL is a subset using neural nets/many layers, "
                            "auto feature learning vs handcrafted, data/compute needs. 6-8: "
                            "mostly right. 3-5: partial. 1-2: wrong-ish. 0: none.")},
                 {"id": "communication_quality", "label": "Explanation Clarity", "max": 5,
                  "guide": ("5: crisp, accurate. 3-4: okay. 1-2: muddled. 0: none.")},
             ]},
            {"id": "ai_30_day_impact", "total": 14,
             "match": ["30 days to build an ai solution that creates the highest business impact",
                       "highest business impact for hopcharge", "what would you build, and why"],
             "categories": [
                 {"id": "ai_business_impact", "label": "Business Impact", "max": 9,
                  "guide": ("9: identifies a high-value EV/HR/ops problem and a feasible AI "
                            "solution with clear ROI/impact. 6-8: solid. 3-5: generic. 1-2: "
                            "vague. 0: none.")},
                 {"id": "ai_solution_design", "label": "Feasibility", "max": 5,
                  "guide": ("5: realistic, buildable in scope. 3-4: some. 1-2: hand-wavy. "
                            "0: none.")},
             ]},
        ],
    },

})


# ──────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────

def _ensure_seeded() -> dict:
    """Load rubrics.json, seeding it on first use. Any DEFAULT_RUBRICS role that
    is MISSING from the file is merged in and persisted — so shipping a new role
    in code makes it appear automatically (in the dev folder AND in every packaged
    copy on next launch) WITHOUT clobbering rubrics an admin customised via the UI
    or extra roles they added themselves."""
    if not RUBRICS_FILE.exists():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(RUBRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_RUBRICS, f, indent=2)
        logger.info("Seeded rubrics.json with %d default roles", len(DEFAULT_RUBRICS))
        return json.loads(json.dumps(DEFAULT_RUBRICS))   # detached copy
    with open(RUBRICS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    missing = [k for k in DEFAULT_RUBRICS if k not in data]
    if missing:
        for k in missing:
            data[k] = DEFAULT_RUBRICS[k]
        with open(RUBRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Merged %d new default role(s) into rubrics.json: %s",
                    len(missing), ", ".join(missing))
    return data


def all_rubrics() -> dict:
    return _ensure_seeded()


def list_roles() -> list[dict]:
    rubrics = _ensure_seeded()
    return [{"key": k, "role_name": v["role_name"],
             "objective_max": v["objective_max"], "ai_max": v["ai_max"]}
            for k, v in rubrics.items()]


def get_rubric(role_key: str) -> dict | None:
    return _ensure_seeded().get(role_key)


def upsert_rubric(role_key: str, rubric: dict) -> dict:
    rubrics = _ensure_seeded()
    rubrics[role_key] = rubric
    with open(RUBRICS_FILE, "w", encoding="utf-8") as f:
        json.dump(rubrics, f, indent=2)
    logger.info("Upserted rubric: %s", role_key)
    return rubric
