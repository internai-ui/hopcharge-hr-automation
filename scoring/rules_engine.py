"""
scoring/rules_engine.py — comprehensive offline candidate scoring engine.

Architecture: CONTENT DOMINATES.
  concept coverage  : 75-80% of category score
  answer length     : 10-15% gentle bonus
  depth/style       :  5-10% (almost irrelevant)

Every concept group covers: formal English, informal, Indian English,
Hinglish phrases, abbreviations, story-form, telegraphic answers,
domain vocabulary, and common paraphrases. The goal is that NO
reasonable expression of the RIGHT IDEA should score zero.
"""

from __future__ import annotations
import json, logging, re
from scoring.providers import AIProvider
from scoring import linguistics as L
from scoring.linguistics import Concept

logger = logging.getLogger("volt_cv.scoring")


# ══════════════════════════════════════════════════════════════════════════
# CONCEPT DATABASE — the largest possible coverage per idea
# Each group has multiple Concept objects covering the same idea from
# different angles. coverage_score() picks the best and adds breadth bonus.
# ══════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────
# GROUP: EMPATHY — every way to show you understand / care
# ─────────────────────────────────────────────────────────────────────────
C_EMPATHY = [
    Concept("listening", [
        # Direct listening
        "listen", "listening", "hear them", "hear them out", "hear you out",
        "hear their concern", "let them speak", "let them talk", "let them finish",
        "give them time to", "give them a chance to", "not interrupt",
        "without interrupting", "allow them to", "let them explain",
        "attentive", "attentively", "active listen", "carefully listen",
        # Informal
        "hear them", "lend an ear", "give ear", "all ears",
        "listen carefully", "listen patiently", "listen first",
        "first listen", "start by listening",
    ], strong_forms=["hear them out", "active listen", "listen first"]),

    Concept("acknowledge_feeling", [
        # Direct acknowledgment
        "acknowledge", "acknowledg", "understand", "i understand",
        "i see why", "i realise", "i realize", "recognise", "recognize",
        "validate", "their frustration is valid", "valid point",
        "completely understand", "totally understand", "fully understand",
        # Empathy expressions
        "empathi", "empathize", "empathise", "feel your", "feel their",
        "put myself in their shoes", "in their position", "in their place",
        "understand where they are coming from", "see their side",
        "understand their perspective", "their point of view",
        "makes sense that", "that makes sense", "i get why",
        "i get it", "i get that", "get where they are coming from",
        "understand where they", "understand why they",
        # Indian English
        "samajh", "i understand their pain", "understand the situation",
        "understand the problem", "understand their issue",
        "understand their concern", "understand what happened",
        "know how they feel", "know what they are going through",
    ], strong_forms=["empathi", "acknowledg", "in their shoes", "validate"]),

    Concept("calm_demeanor", [
        # Composure words
        "stay calm", "remain calm", "be calm", "keep calm",
        "stay composed", "remain composed", "stay cool", "keep cool",
        "level headed", "level-headed", "composed", "composure",
        "patient", "patience", "patiently", "with patience",
        "not get defensive", "without getting defensive", "not argue",
        "not fight back", "not react angrily", "not raise voice",
        "keep my cool", "cool down", "de-escalate", "de escalate",
        "defuse", "diffuse the situation",
        # Tone/approach words
        "calm tone", "soft tone", "gentle tone", "polite tone",
        "professional tone", "soothing voice", "reassuring voice",
        "calm manner", "composed manner", "steady manner",
        # Informal
        "chill", "chilled", "relaxed", "take it easy", "breathe",
        "not panicking", "without panic", "without losing my temper",
        "without getting angry", "not get upset",
    ], strong_forms=["stay calm", "remain calm", "de-escalate", "patient"]),

    Concept("apology_empathy", [
        "sorry", "apologize", "apologise", "apologi", "apology",
        "my apologies", "i am sorry", "we are sorry", "regret",
        "sorry for the inconvenience", "sorry for the trouble",
        "sorry for the delay", "sorry you had to", "sorry this happened",
        "sorry for the experience", "sorry for the frustration",
        "sincerely apologize", "deeply apologize",
        # Indian English apology
        "sorry for any trouble", "sorry for the problem",
        "inconvenience caused", "trouble caused", "issue caused",
        "sorry for the issue", "apologies for the",
    ], strong_forms=["apologize", "apologi", "sincerely apologize"]),

    Concept("genuine_concern", [
        "care", "care about", "genuinely care", "truly care",
        "concern", "concerned", "genuine concern",
        "here to help", "want to help", "here for you",
        "on your side", "for the customer", "customer matters",
        "you matter", "your experience matters", "your concern matters",
        "make it right", "make things right", "make it up",
        "do right by", "they deserve better", "not okay",
        "this should not happen", "not acceptable", "not right",
        "not fair", "understand your frustration",
        # Reassurance
        "reassure", "assure", "assure you", "assure them",
        "rest assured", "i assure", "you can trust",
        "will be taken care of", "in good hands", "i am here",
    ], strong_forms=["make it right", "reassure", "genuinely care", "here for you"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: COMMUNICATION & CLARITY
# ─────────────────────────────────────────────────────────────────────────
C_COMMUNICATION = [
    Concept("explain_clearly", [
        # Direct explanation words
        "explain", "clarify", "clear", "clearly", "inform", "communicate",
        "tell them", "let them know", "make them aware", "ensure they know",
        "walk them through", "take them through", "guide them through",
        "go through", "go over", "break it down", "break down",
        "lay out", "lay it out", "spell out", "outline",
        "describe", "detail", "elaborate", "specify",
        "put it simply", "in simple terms", "plain language",
        "easy to understand", "in layman terms", "simply put",
        # Transparency
        "transparent", "transparency", "honest", "honestly explain",
        "open communication", "keep them informed", "keep updated",
        "update them", "provide information", "share information",
        "give clarity", "give information",
        # Informal
        "tell them what is going on", "fill them in",
        "let them know what happened", "explain the situation",
        "explain what is happening", "tell the customer",
    ], strong_forms=["walk them through", "break it down", "explain clearly", "transparent"]),

    Concept("professional_manner", [
        "professional", "professionally", "proper manner", "appropriately",
        "polite", "politely", "courteous", "courteously", "respectful",
        "respectfully", "with respect", "with care", "with patience",
        "right approach", "correct approach", "proper approach",
        "correct way", "right way", "properly", "formally",
        "good manner", "in a good way", "positively",
        "positive language", "positive attitude",
        # Tone
        "good tone", "right tone", "appropriate tone", "suitable tone",
        "professional tone", "friendly tone",
    ], strong_forms=["professional", "respectful", "courteous"]),

    Concept("structured_response", [
        "step by step", "one by one", "systematically", "methodically",
        "structured", "organized", "organised", "in order",
        "point by point", "clearly structured", "well structured",
        "in a clear manner", "proper sequence", "logical order",
        "properly organized", "in stages", "in phases",
        "structured approach", "systematic approach",
    ], strong_forms=["step by step", "systematically", "structured approach"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: RESOLUTION — every way to say you will fix the problem
# ─────────────────────────────────────────────────────────────────────────
C_RESOLUTION = [
    Concept("fix_solve", [
        # Standard resolution words
        "resolve", "solution", "fix", "solve", "sort", "sort out",
        "sort it", "sort this", "get it sorted", "get sorted",
        "address", "address the issue", "address the problem",
        "deal with", "handle", "handle it", "take care of",
        "take care", "manage", "remedy", "remediate", "rectify",
        "correct", "correct the issue", "correct the problem",
        "find a solution", "come up with a solution", "offer a solution",
        "provide a solution", "give them a solution", "find a fix",
        "find a way", "work it out", "work out a solution",
        "get it done", "get it fixed", "get it resolved",
        "help them", "help fix", "help resolve", "assist them",
        # Outcome focused
        "make sure it is fixed", "ensure it gets resolved",
        "ensure resolution", "make sure it works",
        "until it is resolved", "till it is fixed",
        # Informal
        "sort it out", "deal with it", "get on it", "on it",
        "will sort", "will fix", "will handle", "will take care",
        "get this done", "get them sorted",
        # Domain specific
        "close the ticket", "close the case", "resolve the ticket",
        "resolve the case", "issue resolution", "problem resolution",
    ], strong_forms=["resolve", "fix", "sort it out", "solution", "get it sorted"]),

    Concept("ownership_resolution", [
        "take ownership", "my responsibility", "responsible for",
        "i am responsible", "personally ensure", "personally handle",
        "accountable", "i am accountable", "take charge",
        "own the problem", "own it", "on me", "my job to fix",
        "i will take care of it", "i will handle it personally",
        "i promise to", "i commit to", "i guarantee",
        "see it through", "see through to resolution",
        "follow through", "see it to completion",
        "will not rest until", "will ensure it is resolved",
        "make it my priority",
    ], strong_forms=["take ownership", "accountable", "personally ensure", "i guarantee"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: PROBLEM SOLVING — investigation and logical thinking
# ─────────────────────────────────────────────────────────────────────────
C_PROBLEM_SOLVING = [
    Concept("investigate", [
        # Investigation words
        "investigate", "investigation", "look into", "check", "verify",
        "find out", "figure out", "diagnose", "identify", "identify the issue",
        "understand the issue", "understand the problem", "understand what happened",
        "understand the root", "root cause", "root cause analysis",
        "analyse", "analyze", "analysis", "examine", "inspect",
        "review", "review the situation", "assess", "assess the situation",
        "evaluate", "trace", "trace the issue", "trace back",
        "pinpoint", "pinpoint the issue", "determine", "determine what",
        "see what happened", "see what went wrong", "find what went wrong",
        "check what happened", "check what went wrong", "find the cause",
        "get to the bottom", "get to the root", "understand why",
        "find why", "check why", "look at what happened",
        "look into the matter", "look into this",
        # Technical troubleshooting
        "troubleshoot", "troubleshooting", "debug", "debugging",
        "run checks", "run a check", "run diagnostics",
        "check logs", "check records", "check history",
        "check the system", "check our system", "look up",
        "look it up", "pull up the record", "pull up the details",
        # Indian English
        "see what is the issue", "check what is the problem",
        "verify the details", "check the details",
        "look at the records", "check the records",
        "go through the records", "verify the transaction",
    ], strong_forms=["root cause", "investigate", "troubleshoot", "diagnose", "trace the issue"]),

    Concept("logical_reasoning", [
        "because", "so that", "step by step", "first check", "rule out",
        "narrow down", "determine whether", "depending on", "if it is",
        "logically", "systematically", "methodically", "process of elimination",
        "check each", "one by one", "in order", "sequentially",
        "reason", "reasoning", "logical approach", "structured approach",
        "following process", "standard process", "due process",
        "based on", "based on the information", "based on what",
        "once i know", "after checking", "after verifying",
        "if the issue is", "if it turns out", "if it is a",
        "depending on the cause", "depending on what",
        "either it is", "it could be", "most likely",
        "ruling out", "after ruling out", "having checked",
    ], strong_forms=["rule out", "step by step", "process of elimination", "logically"]),

    Concept("data_checking", [
        "transaction details", "transaction history", "payment details",
        "check the transaction", "verify the payment", "payment status",
        "check payment", "check order", "check booking",
        "account details", "account status", "account history",
        "check account", "verify account", "verify identity",
        "check the record", "pull up the record", "access the record",
        "check our database", "check system", "check portal",
        "check the app", "check the platform", "check our end",
        "on our side", "from our end", "from our system",
        "internally check", "internal check", "backend check",
        "server check", "check logs", "error logs",
    ], strong_forms=["transaction details", "check the record", "verify the payment"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: ESCALATION — every way to say you will get help / involve the team
# ─────────────────────────────────────────────────────────────────────────
C_ESCALATION = [
    Concept("escalate_action", [
        # Direct escalation
        "escalate", "escalation", "raise it", "raise the issue",
        "raise the ticket", "raise a ticket", "raise a case",
        "raise it to", "raise with", "forward to", "transfer to",
        "refer to", "refer the issue", "loop in", "bring in",
        "involve", "involve the", "get involved", "involve team",
        # Getting help from others
        "get help", "get help from", "ask for help", "seek help",
        "seek assistance", "ask senior", "consult senior",
        "involve senior", "involve manager", "involve supervisor",
        "get manager", "get supervisor", "bring in manager",
        # Team mentions
        "technical team", "tech team", "technical support",
        "it team", "backend team", "development team",
        "support team", "operations team", "senior team",
        "concerned team", "right team", "specialist", "expert",
        "team lead", "team leader", "shift manager", "floor manager",
        # Informal escalation
        "get someone else", "get another person", "hand over to",
        "pass it to", "hand it off", "not in my scope",
        "beyond my capacity", "beyond my scope", "beyond my level",
        "need expert", "need specialist", "higher level",
        "next level", "above my pay grade", "need technical help",
    ], strong_forms=["escalate", "raise a ticket", "technical team", "involve the team"]),

    Concept("process_knowledge", [
        "process", "procedure", "protocol", "standard procedure",
        "company policy", "as per policy", "chain of command",
        "proper channel", "official channel", "correct channel",
        "right process", "follow the process", "follow process",
        "as per guidelines", "guidelines", "sop",
        "standard operating procedure", "playbook",
        "escalation matrix", "escalation path", "escalation protocol",
        "hierarchy", "reporting structure", "line of reporting",
        "as we are supposed to", "as per our process",
        "according to our process", "through proper",
    ], strong_forms=["chain of command", "sop", "standard operating procedure", "escalation matrix"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: CUSTOMER COMMUNICATION — keeping customers informed
# ─────────────────────────────────────────────────────────────────────────
C_CUSTOMER_HANDLING = [
    Concept("keep_informed", [
        "keep them informed", "keep customer informed", "keep informed",
        "update the customer", "update them", "keep updated",
        "give updates", "provide updates", "regular updates",
        "status updates", "progress updates", "timely updates",
        "let them know", "tell the customer", "inform customer",
        "inform the customer", "customer is aware", "customer knows",
        "keep them in the loop", "in the loop", "loop them in",
        "keep them posted", "stay in touch", "keep in touch",
        "proactively update", "proactively inform", "proactively communicate",
        "not leave them hanging", "not keep them waiting",
        "transparent with customer", "communicate progress",
        "share the update", "give an update", "provide an update",
        "update on status", "update on progress",
        # Time-bound updates
        "every hour", "every 30 minutes", "every few hours",
        "at regular intervals", "periodically update", "timely manner",
        "within the hour", "as soon as i know",
    ], strong_forms=["keep them informed", "proactively update", "in the loop", "regular updates"]),

    Concept("customer_experience", [
        "customer experience", "good experience", "positive experience",
        "smooth experience", "best experience", "ensure good",
        "satisfy the customer", "customer satisfaction",
        "happy customer", "customer is happy", "customer happy",
        "customer feels", "make them feel", "feel heard",
        "feel valued", "feel respected", "feel supported",
        "they should not feel", "they should know", "they should feel",
        "customer comfort", "their comfort", "their peace of mind",
        "peace of mind", "no worries", "not to worry",
        "everything is fine", "will be taken care of",
    ], strong_forms=["customer satisfaction", "feel heard", "feel valued", "peace of mind"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: MOTIVATION — every reason someone might want this role
# ─────────────────────────────────────────────────────────────────────────
C_MOTIVATION = [
    Concept("genuine_interest", [
        # Strong interest words
        "passionate", "passion", "deeply passionate", "very passionate",
        "genuinely interested", "genuinely", "sincerely interested",
        "truly interested", "really interested", "very interested",
        "love", "love to", "love helping", "love working with",
        "enjoy", "enjoy helping", "enjoy working", "enjoy problem",
        "excited", "very excited", "excited about", "really excited",
        "enthusiastic", "enthusiasm", "always wanted", "always wanted to",
        "drawn to", "attracted to", "appeals to me", "fascinates me",
        "interested in", "keen to", "keen on", "motivated by",
        "driven by", "inspired by", "inspired to", "aspire",
        "aspiration", "dream", "dream of", "goal", "my goal",
        "been wanting", "have been wanting", "since long", "for long",
        "for a long time wanted", "wanted to work in",
        # Meaningful work
        "meaningful", "find it meaningful", "fulfilling",
        "find it fulfilling", "rewarding", "find it rewarding",
        "purpose", "sense of purpose", "satisfaction",
        "job satisfaction", "it matters to me", "matters to me",
        "care about this", "care about helping",
    ], strong_forms=["passionate", "genuinely", "love", "meaningful", "fulfilling", "aspire"]),

    Concept("helping_people", [
        "help people", "help customers", "help others", "helping people",
        "helping customers", "helping others", "assist people",
        "assist customers", "serve customers", "serve people",
        "people person", "people oriented", "people first",
        "enjoy helping", "love helping", "like helping",
        "satisfaction of helping", "happy when i help",
        "good at helping", "natural at helping",
        "make a difference", "make an impact", "impact lives",
        "change lives", "add value", "add value to",
        "contribute to", "contribute positively",
        "smile on their face", "happy customer",
        "when customer is happy", "their satisfaction",
        "seeing them satisfied", "seeing them happy",
        # Indian English
        "like talking to people", "enjoy talking to people",
        "like dealing with people", "enjoy dealing with",
        "customer centric", "customer focused", "customer first",
    ], strong_forms=["make a difference", "people person", "love helping", "customer centric"]),

    Concept("career_growth", [
        "career", "career growth", "career development", "career path",
        "career opportunity", "grow", "growth", "grow my career",
        "build my career", "develop", "develop myself", "develop skills",
        "skill development", "enhance skills", "improve skills",
        "learn", "learning", "keep learning", "continuous learning",
        "gain experience", "get experience", "build experience",
        "long term", "long-term", "long run", "future",
        "5 years", "10 years", "career goal", "my goal is",
        "stepping stone", "foundation", "first step", "starting point",
        "progress", "advance", "advancement", "promotion",
        "grow within", "grow in this", "opportunity to grow",
        "good opportunity", "great opportunity", "right opportunity",
        "this aligns", "aligns with my", "suits my goals",
        "fits my goals", "matches my goals", "right fit",
        "suits me", "right for me", "good fit for me",
        "improve myself", "challenge myself", "push myself",
    ], strong_forms=["career growth", "stepping stone", "this aligns", "long term", "develop"]),

    Concept("company_role_fit", [
        "hopcharge", "ev", "electric vehicle", "ev industry",
        "ev space", "electric vehicle industry", "electric vehicles",
        "charging", "ev charging", "charging station", "ev sector",
        "startup", "growing company", "dynamic company", "growing startup",
        "innovative", "innovative company", "future of transport",
        "future of mobility", "sustainable", "sustainability",
        "green energy", "clean energy", "green tech", "clean tech",
        "renewable", "eco friendly", "environment", "environmental",
        "mission", "company mission", "mission driven",
        "values", "company values", "values align", "believe in",
        "excited about the company", "excited about ev",
        "love what hopcharge does", "love the company",
        "great company", "good company", "leading company",
        "fast growing", "fast paced", "dynamic environment",
        "want to be part of", "want to join", "be part of the journey",
    ], strong_forms=["hopcharge", "electric vehicle", "mission", "values align", "sustainable"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: ROLE UNDERSTANDING — customer support domain awareness
# ─────────────────────────────────────────────────────────────────────────
C_ROLE_UNDERSTANDING_CS = [
    Concept("support_role_knowledge", [
        "customer support", "customer service", "support role",
        "customer facing", "frontline", "front line", "first line",
        "first point of contact", "first contact", "point of contact",
        "handling queries", "resolving queries", "answering queries",
        "handling complaints", "resolving complaints",
        "resolving issues", "handling issues", "customer issues",
        "customer queries", "customer complaints", "customer concerns",
        "customer escalations", "inbound calls", "outbound calls",
        "voice support", "chat support", "email support",
        "multi channel", "omnichannel", "help desk", "service desk",
        "call center", "call centre", "contact center",
        "ticketing", "ticket management", "case management",
        "support experience", "customer experience",
        "service experience", "b2c support", "b2b support",
        "working with customers", "dealing with customers",
        "interacting with customers", "customer interactions",
        "after sales", "post sales", "customer retention",
        "customer success", "client servicing",
    ], strong_forms=["customer support", "customer service", "first point of contact", "frontline"]),

    Concept("ev_domain_awareness", [
        "ev", "electric vehicle", "ev charging", "charging station",
        "hopcharge", "electric car", "e-vehicle", "ev industry",
        "ev space", "sustainable transport", "clean mobility",
        "green energy", "renewable energy", "sustainable energy",
        "charging infrastructure", "charging network", "charging unit",
        "battery", "range", "range anxiety", "charging time",
        "fast charging", "ev adoption", "ev market",
        "future of transport", "future of mobility",
        "electric mobility", "clean tech", "green tech",
        "ev technology", "charging technology",
    ], strong_forms=["electric vehicle", "ev charging", "hopcharge", "charging station"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: PRIORITIZATION — managing multiple tasks by importance
# ─────────────────────────────────────────────────────────────────────────
C_PRIORITIZATION = [
    Concept("urgency_priority", [
        "urgent", "urgency", "prioritize", "prioritise", "priority",
        "most critical", "most important", "most urgent",
        "time sensitive", "time-sensitive", "deadline", "time bound",
        "immediate", "first handle", "first address", "tackle first",
        "deal with first", "address first", "handle first",
        "high priority", "top priority", "first priority", "number one priority",
        "most pressing", "pressing issue", "quick response", "fast response",
        "asap", "as soon as possible", "without delay", "right away",
        "time constraint", "within deadline", "before deadline",
        "urgent tasks first", "urgent first", "emergency first",
        "critical tasks first", "by deadline", "by urgency",
        # Assessment language
        "assess urgency", "evaluate urgency", "check urgency",
        "based on urgency", "order of urgency", "in order of urgency",
        "rank by urgency", "sort by urgency",
    ], strong_forms=["prioritize", "high priority", "most critical", "urgent first"]),

    Concept("impact_priority", [
        "business impact", "customer impact", "high impact",
        "most impact", "biggest impact", "largest impact",
        "affects most", "affects the most", "most affected",
        "number of people", "how many affected", "more customers",
        "most customers", "revenue impact", "revenue", "revenue loss",
        "severity", "severity level", "level of severity",
        "consequences", "impact assessment", "impact analysis",
        "which is more important", "which matters more",
        "weigh the tasks", "weigh my options", "evaluate tasks",
        "based on importance", "based on impact", "by importance",
        "order of importance", "rank by importance",
        "critical vs non critical", "important vs less important",
        "must do vs can wait",
    ], strong_forms=["business impact", "customer impact", "severity", "impact assessment"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: DECISION MAKING — being decisive under pressure
# ─────────────────────────────────────────────────────────────────────────
C_DECISION = [
    Concept("decisive_action", [
        "decide", "decision", "make a decision", "take a decision",
        "make a call", "take a call", "call it", "go with",
        "choose", "choice", "pick", "select", "opt for",
        "commit to", "go ahead with", "move forward with",
        "without panic", "calmly decide", "quickly decide",
        "swift decision", "quick decision", "fast decision",
        "on the spot", "think on my feet", "quick thinking",
        "not overthink", "not hesitate", "without hesitation",
        "confidently", "with confidence", "clear headed",
        "assess and act", "evaluate and act", "think and act",
        # Adaptive decision making
        "adapt", "flexible", "flexibility", "adjust",
        "adjust the plan", "course correct", "pivot",
        "change approach", "different approach",
        "re-evaluate", "reassess",
        "based on the situation", "situation dependent",
        "context based", "case by case",
    ], strong_forms=["make a decision", "without hesitation", "think on my feet", "decisive"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: OPERATIONAL THINKING — logistics and operations vocabulary
# ─────────────────────────────────────────────────────────────────────────
C_OPERATIONAL = [
    Concept("ops_vocabulary", [
        # Core operations words
        "dispatch", "dispatching", "allocate", "allocation",
        "resource", "resources", "resource management", "resource allocation",
        "schedule", "scheduling", "route", "routing", "reroute",
        "logistics", "logistical", "coordinate", "coordination",
        "assign", "assignment", "deploy", "deployment",
        "fleet", "fleet management", "vehicle fleet",
        "queue", "queue management", "manage queue",
        "capacity", "capacity planning", "capacity management",
        "workflow", "workflow management", "work flow",
        "bandwidth", "bandwidth management", "throughput",
        "efficiency", "operational efficiency", "optimize",
        "optimise", "optimization", "streamline",
        # Field operations
        "field", "field team", "field operations", "on ground",
        "ground team", "ground level", "field agent",
        "pilot site", "pilot", "deployment site",
        # Planning and tracking
        "track", "tracking", "monitor", "monitoring", "oversee",
        "supervise", "manage operations", "operations management",
        "real time", "real-time", "live tracking", "live monitoring",
        "gps", "gps tracking", "location tracking",
        # Process vocabulary
        "manpower", "staff allocation", "crew management",
        "workload distribution", "load balancing",
        "task distribution", "task allocation", "work distribution",
    ], strong_forms=["dispatch", "allocate", "logistics", "fleet management", "resource management"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: CRISIS HANDLING — emergency response
# ─────────────────────────────────────────────────────────────────────────
C_CRISIS = [
    Concept("immediate_response", [
        # Urgency and speed
        "immediately", "immediate", "right away", "at once", "asap",
        "straight away", "instantly", "instantly respond", "quick response",
        "fast response", "swift response", "rapid response",
        "without delay", "no time to waste", "urgently",
        "first thing", "very first thing", "before anything else",
        "as a priority", "on priority", "top priority",
        "act fast", "act quickly", "act immediately", "act now",
        "respond fast", "respond immediately", "respond asap",
        "swift action", "quick action", "immediate action",
        "crisis response", "emergency response",
        # First response language
        "first step would be", "first i would", "immediately i would",
        "my first action", "first action", "first thing i do",
        "right away i would", "without wasting time",
        "as soon as i get to know", "as soon as this happens",
    ], strong_forms=["immediately", "right away", "asap", "without delay", "act immediately"]),

    Concept("team_alert", [
        "alert", "alert the team", "notify", "notify the team",
        "inform", "inform the team", "raise alarm", "raise alert",
        "flag", "flag the issue", "flag it immediately", "red flag",
        "escalate", "escalate immediately", "escalate to management",
        "report", "report it", "report the issue", "report to",
        "bring it to attention", "bring to notice", "bring to notice of",
        "management needs to know", "team needs to know",
        "everyone needs to know", "loop in", "loop in the team",
        "bring everyone in", "get the team on it", "involve the team",
        "coordinate with team", "coordinate with management",
    ], strong_forms=["alert", "notify", "escalate immediately", "flag it immediately"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: OPERATIONS DOMAIN UNDERSTANDING
# ─────────────────────────────────────────────────────────────────────────
C_OPS_UNDERSTANDING = [
    Concept("operations_domain", [
        "logistics", "supply chain", "supply chain management",
        "operations", "operations management", "ops management",
        "fleet management", "fleet operations", "vehicle management",
        "coordination", "coordination management", "cross functional",
        "dispatch management", "delivery management", "distribution",
        "inventory", "inventory management", "warehouse",
        "procurement", "vendor management", "vendor coordination",
        "last mile", "last mile delivery", "ground operations",
        "field operations", "site management", "site operations",
        "project coordination", "project management", "resource planning",
        "manpower management", "workforce management",
        "capacity planning", "demand planning", "supply planning",
        "route optimization", "route planning", "network planning",
        "operational excellence", "process optimization",
        "lean operations", "efficient operations",
        # EV specific
        "ev charging operations", "charging network management",
        "charging station management", "ev fleet management",
        "charging infrastructure", "deployment of charging",
        "pilot management", "pilot operations",
    ], strong_forms=["supply chain", "fleet management", "logistics", "operations management"]),
]


# ─────────────────────────────────────────────────────────────────────────
# GROUP: DOCUMENTATION — recording and tracking
# ─────────────────────────────────────────────────────────────────────────
C_DOCUMENTATION = [
    Concept("record_keeping", [
        "document", "documentation", "record", "recording",
        "log", "logging", "log it", "note", "note it", "note down",
        "jot down", "write down", "write it down", "put it in writing",
        "track", "tracking", "keep track", "maintain record",
        "keep a record", "keep records", "update records",
        "raise ticket", "raise a ticket", "create ticket",
        "open ticket", "log a ticket", "create case", "open case",
        "incident report", "file a report", "create report",
        "report the incident", "incident logging",
        "capture", "capture the issue", "capture details",
        "register", "register the issue", "entry in system",
        "update the system", "update the log", "system entry",
        "mark it", "tag it", "tag in system", "record in system",
    ], strong_forms=["document", "incident report", "raise a ticket", "log it"]),

    Concept("process_chain", [
        "chain of command", "proper channel", "official channel",
        "correct channel", "through proper", "following protocol",
        "standard procedure", "as per protocol", "as per sop",
        "follow the process", "right process", "proper process",
        "hierarchy", "reporting hierarchy", "organizational hierarchy",
        "line manager", "reporting manager", "report to",
        "report up", "up the chain", "within the framework",
        "within the system", "within guidelines",
    ], strong_forms=["chain of command", "standard procedure", "proper channel"]),
]


# ══════════════════════════════════════════════════════════════════════════
# SCORING ARCHITECTURE
# Weights: concepts 78%, length 12%, depth 10% (at neutral tuning)
# ══════════════════════════════════════════════════════════════════════════

# ── Tunable offline scoring (strictness + creativeness) ─────────────────────
# The HR admin sets a per-role STRICTNESS and CREATIVENESS (0-100, 50 = neutral)
# in Admin → "Scoring tuning per role". The scoring engine copies them onto the
# provider instance; RulesEngineProvider.complete_json() publishes them here at
# the start of every call so the module-level scorers can read them. Scoring
# runs sequentially within a process (FastAPI calls score_* synchronously and
# the rules engine does no awaiting), so a module-level holder is safe.
#
# CRUCIAL PROPERTY: at 50/50 every formula below reduces EXACTLY to the original
# constants, so a neutral configuration reproduces prior offline scores
# bit-for-bit. Only a moved slider changes anything.
_TUNING = {"strictness": 50, "creativeness": 50}


def _tuning_axes() -> tuple:
    """Return (s, c) in [-1, 1]; +s = stricter, +c = more creative."""
    s = (max(0, min(100, int(_TUNING.get("strictness", 50)))) - 50) / 50.0
    c = (max(0, min(100, int(_TUNING.get("creativeness", 50)))) - 50) / 50.0
    return s, c


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


# Base blend weights (must sum to 1.0) — the historical neutral values.
_W_CONCEPT, _W_LENGTH, _W_DEPTH = 0.78, 0.12, 0.10
# How far creativeness shifts weight off concept-matching onto expression.
# Bounded (0.20) so the weights stay non-negative across the full slider range.
_CREATIVE_SHIFT = 0.20


def _blend_weights(c: float) -> tuple:
    """Creativeness rebalances concept-match vs expression (length+depth).

    c>0 (creative): trust the exact domain keywords less and reward substance /
    variety more — this lets unconventional but genuinely good answers score.
    c<0 (literal): concept keywords dominate even more. c=0 returns the exact
    historical weights (0.78 / 0.12 / 0.10)."""
    shift = c * _CREATIVE_SHIFT
    expr = _W_LENGTH + _W_DEPTH
    cw = _W_CONCEPT - shift
    lw = _W_LENGTH + shift * (_W_LENGTH / expr)
    dw = _W_DEPTH + shift * (_W_DEPTH / expr)
    return _clamp(cw, 0.05, 0.98), max(0.0, lw), max(0.0, dw)


def _score(concept_strength: float, answer: str, norm: str) -> float:
    """
    Core scoring formula, shaped by the strictness + creativeness knobs.
    Concepts dominate. Length gives a gentle bonus. Depth barely matters.
    Any genuine attempt (5+ words) gets a floor. At neutral tuning (50/50) this
    is byte-identical to the original fixed-weight formula.
    """
    s, c = _tuning_axes()
    length  = L.length_bonus(answer)
    depth   = L.answer_depth(answer)
    wc      = L.word_count(answer)

    cw, lw, dw = _blend_weights(c)
    raw = (concept_strength * cw) + (length * lw) + (depth * dw)

    # Creativeness rewards original, varied phrasing (only when raised).
    if c > 0:
        raw += c * 0.12 * L.lexical_diversity(answer)

    # Strictness gain: a strict bar trims the score, a lenient bar lifts it (±15%).
    raw *= (1.0 - s * 0.15)

    # Strictness-scaled floors: a lenient bar rewards any genuine attempt; a
    # strict bar barely floors, so weak answers must earn their marks.
    if wc >= 5:
        raw = max(raw, _clamp(0.35 - s * 0.20, 0.10, 0.55))
    elif wc >= 2:
        raw = max(raw, _clamp(0.20 - s * 0.15, 0.03, 0.40))

    return max(0.0, min(raw, 1.0))


def _empathy_score(a, n, q):
    concepts = L.coverage_score(n, C_EMPATHY)
    # Bonus for politeness and ownership signals
    bonus = (L.signal_politeness(n) * 0.08 + L.signal_effort(n) * 0.05)
    return _score(min(concepts + bonus, 1.0), a, n)

def _communication_score(a, n, q):
    concepts = L.coverage_score(n, C_COMMUNICATION)
    step_boost = L.signal_steps(n) * 0.10
    return _score(min(concepts + step_boost, 1.0), a, n)

def _resolution_score(a, n, q):
    concepts = L.coverage_score(n, C_RESOLUTION)
    own_boost = L.signal_ownership(n) * 0.08
    fu_boost  = L.signal_followup(n) * 0.07
    return _score(min(concepts + own_boost + fu_boost, 1.0), a, n)

def _problem_solving_score(a, n, q):
    concepts = L.coverage_score(n, C_PROBLEM_SOLVING)
    spec_boost = L.signal_specificity(a) * 0.10
    return _score(min(concepts + spec_boost, 1.0), a, n)

def _escalation_awareness_score(a, n, q):
    concepts = L.coverage_score(n, C_ESCALATION)
    return _score(concepts, a, n)

def _customer_handling_score(a, n, q):
    concepts = L.coverage_score(n, C_CUSTOMER_HANDLING)
    fu_boost = L.signal_followup(n) * 0.08
    return _score(min(concepts + fu_boost, 1.0), a, n)

def _motivation_score(a, n, q):
    concepts = L.coverage_score(n, C_MOTIVATION)
    return _score(concepts, a, n)

def _role_understanding_score(a, n, q):
    concepts = L.best_concept(n, C_ROLE_UNDERSTANDING_CS)
    spec_boost = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec_boost, 1.0), a, n)

def _communication_quality_score(a, n, q):
    # Quality-heavy — rewards articulate writing but generous floor.
    # Tuning-aware: creativeness leans further into depth/variety, strictness
    # trims and lowers the floor. Neutral (50/50) == original formula.
    s, c = _tuning_axes()
    comm_concepts = L.coverage_score(n, C_COMMUNICATION)
    depth = L.answer_depth(a)
    length = L.length_bonus(a)
    wc = L.word_count(a)
    depth_w = 0.35 + max(0.0, c) * 0.10
    raw = (comm_concepts * 0.45) + (depth * depth_w) + (length * 0.20)
    if c > 0:
        raw += c * 0.10 * L.lexical_diversity(a)
    raw *= (1.0 - s * 0.15)
    if wc >= 5:
        raw = max(raw, _clamp(0.38 - s * 0.20, 0.10, 0.55))
    return max(0.0, min(raw, 1.0))

def _prioritization_score(a, n, q):
    concepts = L.coverage_score(n, C_PRIORITIZATION)
    urg_boost = L.signal_urgency(n) * 0.08
    return _score(min(concepts + urg_boost, 1.0), a, n)

def _decision_making_score(a, n, q):
    concepts = L.best_concept(n, C_DECISION)
    own_boost = L.signal_ownership(n) * 0.08
    return _score(min(concepts + own_boost, 1.0), a, n)

def _operational_thinking_score(a, n, q):
    concepts = L.best_concept(n, C_OPERATIONAL)
    spec_boost = L.signal_specificity(a) * 0.10
    return _score(min(concepts + spec_boost, 1.0), a, n)

def _crisis_handling_score(a, n, q):
    concepts = L.coverage_score(n, C_CRISIS)
    urg_boost = L.signal_urgency(n) * 0.08
    own_boost = L.signal_ownership(n) * 0.05
    return _score(min(concepts + urg_boost + own_boost, 1.0), a, n)

def _escalation_process_score(a, n, q):
    doc_score = L.best_concept(n, C_DOCUMENTATION)
    esc_score = L.best_concept(n, C_ESCALATION)
    combined  = max(doc_score, esc_score) * 0.70 + min(doc_score, esc_score) * 0.20
    step_boost = L.signal_steps(n) * 0.05
    return _score(min(combined + step_boost, 1.0), a, n)

def _operations_understanding_score(a, n, q):
    concepts = L.best_concept(n, C_OPS_UNDERSTANDING)
    spec_boost = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec_boost, 1.0), a, n)

def _stakeholder_communication_score(a, n, q):
    cust = L.best_concept(n, C_CUSTOMER_HANDLING)
    esc  = L.best_concept(n, C_ESCALATION)
    combined = max(cust, esc) * 0.60 + min(cust, esc) * 0.25
    fu_boost = L.signal_followup(n) * 0.08
    return _score(min(combined + fu_boost, 1.0), a, n)


# ══════════════════════════════════════════════════════════════════════════
# NEW-ROLE CONCEPT DATABASES — Sales, Leadership, Founder's Office, Ops,
# Field/Technical and AI Engineering competencies. Same design philosophy:
# CONTENT DOMINATES, every idea covered from many angles (formal, informal,
# Indian English, domain vocabulary, abbreviations). All scorers route through
# _score(), so the strictness + creativeness knobs apply automatically.
# ══════════════════════════════════════════════════════════════════════════

# ── SALES: results / track record ──────────────────────────────────────────
C_SALES_RESULTS = [
    Concept("closed_business", [
        "closed", "close the deal", "won the deal", "won the account",
        "acquired", "onboarded", "signed", "signed up", "converted",
        "brought in", "landed", "secured the client", "secured the deal",
        "bagged", "clinched", "cracked the deal", "sealed the deal",
        "deal worth", "contract worth", "order worth", "account worth",
        "biggest client", "largest client", "key account", "marquee client",
        "enterprise client", "flagship client",
    ], strong_forms=["closed the deal", "acquired", "won the account", "signed"]),
    Concept("quantified_result", [
        "revenue", "crore", "lakh", "lakhs", "cr ", "million", "arr", "mrr",
        "quota", "target achieved", "achieved target", "exceeded target",
        "overachieved", "beat target", "% of target", "percent of target",
        "hit my number", "hit the number", "grew", "growth of", "increased by",
        "worth rs", "worth ₹", "value of the deal", "deal size", "ticket size",
        "top performer", "number one", "highest sales", "record sales",
    ], strong_forms=["exceeded target", "crore", "overachieved", "top performer"]),
]

# ── SALES: negotiation / objection handling ────────────────────────────────
C_NEGOTIATION = [
    Concept("value_over_price", [
        "value", "value proposition", "value for money", "roi",
        "return on investment", "total cost of ownership", "tco",
        "long term savings", "cost benefit", "worth the price",
        "you get what you pay", "cheaper is not better", "quality over price",
        "differentiate", "differentiation", "unique", "our advantage",
        "usp", "competitive advantage", "superior", "better service",
        "premium", "justify the price", "justify the cost", "price is justified",
        "not just about price", "beyond price", "reframe", "shift the conversation",
    ], strong_forms=["value proposition", "roi", "total cost of ownership", "differentiate"]),
    Concept("negotiation_tactics", [
        "negotiate", "negotiation", "win win", "win-win", "middle ground",
        "common ground", "meet in the middle", "concession", "trade off",
        "trade-off", "give and take", "bundle", "add value", "sweeten the deal",
        "understand their need", "understand the requirement", "dig deeper",
        "ask questions", "uncover", "pain point", "budget", "decision maker",
        "walk away", "best alternative", "batna", "anchor", "hold firm",
        "not drop the price", "protect margin", "protect the margin",
    ], strong_forms=["win win", "value proposition", "understand their need", "batna"]),
]

# ── SALES: full sales process / methodology ────────────────────────────────
C_SALES_PROCESS = [
    Concept("pipeline_stages", [
        "lead generation", "generate leads", "prospecting", "prospect",
        "qualify", "qualification", "bant", "discovery", "needs analysis",
        "demo", "demonstration", "presentation", "pitch", "proposal",
        "quotation", "quote", "negotiation", "closing", "close the sale",
        "onboarding", "handover", "after sales", "follow up", "nurture",
        "funnel", "sales funnel", "pipeline", "stages", "sales cycle",
        "cold call", "cold outreach", "outreach", "warm lead", "inbound",
        "outbound", "crm", "track the lead", "move the deal",
    ], strong_forms=["sales funnel", "qualification", "pipeline", "sales cycle"]),
]

# ── SALES: target orientation / recovery under pressure ────────────────────
C_TARGET_DRIVE = [
    Concept("catch_up_plan", [
        "catch up", "make up the gap", "close the gap", "bridge the gap",
        "recover", "recovery plan", "course correct", "back on track",
        "ramp up", "double down", "push harder", "increase activity",
        "more calls", "more meetings", "more leads", "aggressive",
        "revisit pipeline", "chase pending", "follow up on hot leads",
        "prioritise big deals", "focus on closable", "low hanging fruit",
        "upsell", "cross sell", "existing customers", "reactivate",
        "daily target", "break down the target", "week on week", "hustle",
        "extra effort", "extend hours", "not panic", "analyse what went wrong",
    ], strong_forms=["recovery plan", "close the gap", "ramp up", "double down"]),
]

# ── SALES: prospecting / new business opportunities ────────────────────────
C_PROSPECTING = [
    Concept("opportunity_identification", [
        "identify opportunities", "new markets", "market research",
        "target segment", "segment", "customer segment", "untapped",
        "new geography", "new territory", "expand into", "partnership",
        "partnerships", "channel partner", "tie up", "collaboration",
        "referral", "referrals", "network", "networking", "cold outreach",
        "linkedin", "database", "lead list", "corporate", "fleet",
        "housing societies", "apartments", "offices", "b2b", "b2c",
        "competitor customers", "market gap", "demand", "trends",
        "events", "exhibitions", "campaigns", "digital marketing",
    ], strong_forms=["market research", "target segment", "partnerships", "new markets"]),
]

# ── SALES: hunter mentality / why succeed ──────────────────────────────────
C_SALES_MOTIVATION = [
    Concept("sales_drive", [
        "target oriented", "target driven", "goal oriented", "hunter",
        "go getter", "self motivated", "driven", "ambitious", "competitive",
        "resilient", "resilience", "never give up", "persistent", "persistence",
        "thick skin", "handle rejection", "bounce back", "hard working",
        "result oriented", "results driven", "closer", "love sales",
        "passion for sales", "relationship builder", "people skills",
        "convincing", "persuasive", "confident", "track record",
        "proven record", "consistent performer", "exceed expectations",
    ], strong_forms=["target driven", "hunter", "proven record", "handle rejection"]),
]

# ── LEADERSHIP: revenue ownership / P&L ────────────────────────────────────
C_REVENUE_OWNERSHIP = [
    Concept("owned_number", [
        "revenue target", "revenue responsibility", "p and l", "p&l",
        "profit and loss", "top line", "bottom line", "quota", "annual target",
        "annual revenue", "budget", "cost centre", "revenue of", "managed revenue",
        "responsible for revenue", "carried a target", "carried the number",
        "crore", "million", "arr", "book of business", "portfolio",
        "delivered revenue", "grew revenue", "revenue growth", "margin",
        "profitability", "financial target", "sales target of",
    ], strong_forms=["p&l", "revenue target", "carried a target", "revenue growth"]),
]

# ── LEADERSHIP: strategy / go-to-market / expansion ────────────────────────
C_SALES_STRATEGY = [
    Concept("gtm_strategy", [
        "strategy", "strategic", "go to market", "gtm", "market penetration",
        "penetration", "expansion", "expand", "scale", "scaling", "roll out",
        "phased approach", "pilot", "market entry", "segmentation",
        "positioning", "value proposition", "channel strategy", "distribution",
        "partnerships", "playbook", "roadmap", "blueprint", "framework",
        "market share", "competitive analysis", "competitor analysis",
        "pricing strategy", "brand", "demand generation", "hiring plan",
        "local team", "on ground team", "city launch", "metro", "geography",
    ], strong_forms=["go to market", "market penetration", "expansion", "segmentation"]),
]

# ── LEADERSHIP: managing / motivating teams ────────────────────────────────
C_TEAM_LEADERSHIP = [
    Concept("develop_team", [
        "motivate", "motivation", "coach", "coaching", "mentor", "mentoring",
        "train", "training", "upskill", "develop the team", "one on one",
        "1 on 1", "feedback", "constructive feedback", "performance review",
        "performance management", "set clear goals", "clear targets",
        "kpi", "kra", "accountability", "incentive", "reward", "recognition",
        "celebrate wins", "lead by example", "hand holding", "support the team",
        "identify weakness", "root cause of underperformance", "pip",
        "improvement plan", "empower", "delegate", "build the team",
        "team building", "culture", "morale", "engage the team",
    ], strong_forms=["coaching", "one on one", "performance management", "lead by example"]),
]

# ── LEADERSHIP: key-account retention / churn handling ─────────────────────
C_KEY_ACCOUNT = [
    Concept("retain_account", [
        "retain", "retention", "save the account", "save the client",
        "win back", "win them back", "not lose", "prevent churn", "churn",
        "understand their concern", "root cause of dissatisfaction",
        "service recovery", "action plan", "improvement plan", "sla",
        "dedicated support", "account manager", "escalate internally",
        "senior management involvement", "leadership meeting", "review meeting",
        "regular reviews", "qbr", "relationship", "trust", "rebuild trust",
        "value delivered", "roi", "compensate", "make it right", "commitment",
        "long term partnership", "not just a vendor", "strategic partner",
    ], strong_forms=["retention", "service recovery", "prevent churn", "rebuild trust"]),
]

# ── LEADERSHIP: forecasting / data-driven planning ─────────────────────────
C_FORECASTING = [
    Concept("forecast_method", [
        "forecast", "forecasting", "pipeline", "weighted pipeline",
        "conversion rate", "win rate", "historical data", "past trends",
        "run rate", "projection", "project the numbers", "bottoms up",
        "top down", "crm data", "stage wise", "probability", "likelihood",
        "expected close", "commit", "best case", "worst case", "sales velocity",
        "seasonality", "quarter", "monthly", "trend analysis", "data driven",
        "assumptions", "leading indicators", "activity metrics",
    ], strong_forms=["weighted pipeline", "conversion rate", "historical data", "sales velocity"]),
]

# ── LEADERSHIP: readiness to lead an organisation ──────────────────────────
C_LEADERSHIP_READINESS = [
    Concept("proven_leader", [
        "led a team", "led teams", "managed a team", "built a team",
        "scaled", "grew the business", "track record", "proven track record",
        "delivered results", "consistently", "vision", "strategic thinking",
        "big picture", "data driven decisions", "own the outcome",
        "accountability", "cross functional", "stakeholder management",
        "board", "leadership experience", "p&l ownership", "ready to lead",
        "step up", "next level", "handled pressure", "turned around",
        "transformation", "change management", "inspire", "set direction",
    ], strong_forms=["proven track record", "scaled", "p&l ownership", "led teams"]),
]

# ── FOUNDER'S OFFICE: resourcefulness / self-starter ───────────────────────
C_RESOURCEFULNESS = [
    Concept("self_starter", [
        "figured it out", "figure out", "self starter", "took initiative",
        "took ownership", "on my own", "without guidance", "without help",
        "little direction", "no direction", "minimal supervision",
        "autonomous", "independently", "self driven", "resourceful",
        "learned on the go", "learnt quickly", "picked up", "self taught",
        "researched", "google", "youtube", "trial and error", "found a way",
        "made it happen", "got it done", "hustle", "jugaad", "improvise",
        "proactive", "did not wait", "stepped up", "owned the problem",
    ], strong_forms=["took initiative", "self starter", "figured it out", "resourceful"]),
]

# ── FOUNDER'S OFFICE / MT: data-driven problem solving ─────────────────────
C_DATA_ANALYSIS = [
    Concept("analytical", [
        "data", "data driven", "analyse", "analyze", "analysis", "metrics",
        "numbers", "excel", "spreadsheet", "pivot", "pivot table",
        "dashboard", "insights", "insight", "trend", "trends", "pattern",
        "correlation", "root cause", "hypothesis", "segment the data",
        "break down the numbers", "kpi", "measure", "measurable", "quantify",
        "benchmark", "compare", "sql", "query", "visualise", "chart",
        "report", "evidence", "fact based", "sample", "survey", "a b test",
        "experiment", "statistically", "power bi", "tableau",
    ], strong_forms=["data driven", "root cause", "insights", "pivot table"]),
]

# ── FOUNDER'S OFFICE / MT: stakeholder juggling ────────────────────────────
C_STAKEHOLDER_MGMT = [
    Concept("manage_stakeholders", [
        "manage expectations", "set expectations", "communicate",
        "keep everyone informed", "align", "alignment", "priorities",
        "prioritise", "negotiate deadlines", "realistic timeline",
        "clarify requirement", "understand what each", "buy time",
        "push back politely", "manage upwards", "coordinate", "sync",
        "single point of contact", "delegate", "escalate to founder",
        "get clarity from", "confirm priority", "which is most urgent",
        "transparent", "under promise over deliver", "keep them updated",
    ], strong_forms=["manage expectations", "align", "negotiate deadlines", "prioritise"]),
]

# ── FOUNDER'S OFFICE: fit / why founder's office ───────────────────────────
C_FOUNDER_FIT = [
    Concept("startup_generalist", [
        "fast paced", "fast-paced", "high growth", "steep learning",
        "learning curve", "wear many hats", "generalist", "cross functional",
        "exposure", "0 to 1", "zero to one", "build from scratch",
        "entrepreneurial", "founder mindset", "ownership", "direct impact",
        "close to decision", "strategic", "big picture", "variety",
        "dynamic", "ambiguity", "no two days same", "high responsibility",
        "accelerate my growth", "learn from the founder", "front row seat",
        "solve real problems", "end to end", "autonomy",
    ], strong_forms=["fast paced", "wear many hats", "zero to one", "founder mindset"]),
]

# ── COMMON: biggest achievement ────────────────────────────────────────────
C_ACHIEVEMENT = [
    Concept("accomplishment", [
        "achieved", "accomplished", "proud", "proudest", "award", "awarded",
        "recognition", "recognised", "topper", "rank", "gold medal",
        "scholarship", "led", "built", "launched", "founded", "started",
        "grew", "increased", "improved", "delivered", "won", "first place",
        "best", "milestone", "breakthrough", "against all odds", "overcame",
        "from scratch", "resulted in", "impact", "successfully", "beat the odds",
        "record", "promoted", "fastest", "biggest",
    ], strong_forms=["achieved", "led", "award", "resulted in"]),
]

# ── OPS: performance / SLA improvement ─────────────────────────────────────
C_PERFORMANCE_IMPROVEMENT = [
    Concept("improve_performance", [
        "root cause", "identify the reason", "why sla", "analyse the gap",
        "training", "coaching", "set clear targets", "kpi", "monitor",
        "daily review", "track performance", "feedback", "one on one",
        "process improvement", "streamline", "remove bottleneck",
        "automate", "checklist", "sop", "standard operating procedure",
        "accountability", "ownership", "incentive", "reward performance",
        "reallocate", "balance workload", "capacity", "escalate blockers",
        "measure", "benchmark", "continuous improvement", "corrective action",
    ], strong_forms=["root cause", "process improvement", "sop", "corrective action"]),
]

# ── FAE: troubleshooting / diagnostics ─────────────────────────────────────
C_TROUBLESHOOTING = [
    Concept("diagnose_fault", [
        "check the power", "check power supply", "check the supply",
        "check connection", "check connections", "check wiring", "loose connection",
        "isolate", "isolate the fault", "narrow down", "step by step",
        "systematic", "test", "test the", "multimeter", "measure voltage",
        "check voltage", "check current", "continuity", "check the fuse",
        "circuit breaker", "reset", "reboot", "restart", "error code",
        "diagnostic", "diagnostics", "check logs", "fault finding",
        "rule out", "process of elimination", "root cause", "trace the fault",
        "check the charger", "check the connector", "check the cable",
    ], strong_forms=["multimeter", "isolate the fault", "check the power", "continuity"]),
]

# ── FAE: technical / electrical knowledge ──────────────────────────────────
C_TECHNICAL_KNOWLEDGE = [
    Concept("electrical_knowledge", [
        "voltage", "current", "ampere", "amp", "watt", "kw", "kwh",
        "power", "circuit", "load", "phase", "three phase", "single phase",
        "earthing", "grounding", "neutral", "live wire", "insulation",
        "resistance", "ohm", "short circuit", "overload", "mcb", "rccb",
        "breaker", "fuse", "relay", "contactor", "transformer", "inverter",
        "battery", "lithium ion", "cell", "bms", "charge cycle", "soc",
        "state of charge", "connector", "cable", "gauge", "rating",
        "charger", "ac charging", "dc charging", "fast charging", "panel",
    ], strong_forms=["voltage", "earthing", "three phase", "bms", "load"]),
]

# ── FAE: inspection / QA / commissioning ───────────────────────────────────
C_INSPECTION_QA = [
    Concept("inspection_checklist", [
        "checklist", "inspect", "inspection", "verify", "verification",
        "test", "testing", "commissioning", "pre commissioning", "sign off",
        "quality check", "qc", "qa", "standard", "standards", "compliance",
        "as per code", "rating", "tolerance", "measurement", "documentation",
        "record", "report", "earthing test", "insulation test", "load test",
        "polarity", "torque", "tightness", "labelling", "safety check",
        "functional test", "trial run", "handover", "customer handover",
        "visual inspection", "physical check", "acceptance",
    ], strong_forms=["checklist", "commissioning", "insulation test", "sign off"]),
]

# ── FAE: safety awareness ──────────────────────────────────────────────────
C_SAFETY = [
    Concept("safety_precautions", [
        "safety", "precaution", "ppe", "personal protective", "gloves",
        "insulated gloves", "safety shoes", "helmet", "goggles",
        "power off", "switch off", "turn off the supply", "isolate the supply",
        "lockout", "tagout", "loto", "de energise", "discharge",
        "no live working", "earthing", "grounding", "insulation",
        "dry hands", "avoid water", "rubber mat", "warning sign",
        "barricade", "trained personnel", "follow protocol", "sop",
        "double check", "test before touch", "first aid", "fire extinguisher",
        "avoid short circuit", "wear safety", "safe distance",
    ], strong_forms=["power off", "ppe", "isolate the supply", "lockout"]),
]

# ── AI: model debugging / drift ────────────────────────────────────────────
C_ML_DEBUGGING = [
    Concept("diagnose_model", [
        "data drift", "concept drift", "distribution shift", "data quality",
        "training data", "input data", "feature", "features", "overfitting",
        "underfitting", "retrain", "retraining", "monitoring", "monitor",
        "evaluation", "validation", "test set", "metrics", "precision",
        "recall", "confusion matrix", "false positive", "false negative",
        "pipeline", "logs", "error analysis", "root cause", "compare",
        "baseline", "a b test", "check the data", "label", "labelling",
        "class imbalance", "outlier", "preprocessing", "leakage",
        "production data", "real world data", "edge case",
    ], strong_forms=["data drift", "training data", "overfitting", "error analysis"]),
]

# ── AI: project depth / end-to-end build ───────────────────────────────────
C_AI_PROJECT_DEPTH = [
    Concept("end_to_end_ml", [
        "end to end", "dataset", "data collection", "data cleaning",
        "preprocessing", "feature engineering", "model", "train the model",
        "training", "hyperparameter", "tuning", "evaluation", "accuracy",
        "deploy", "deployment", "production", "api", "inference", "pipeline",
        "architecture", "neural network", "cnn", "rnn", "lstm", "transformer",
        "fine tune", "fine-tune", "pytorch", "tensorflow", "scikit",
        "experiment", "iteration", "results", "improved accuracy",
        "real world", "scaled", "challenge", "solved",
    ], strong_forms=["end to end", "feature engineering", "deployment", "fine tune"]),
]

# ── AI: solution design (chatbot / RAG / architecture) ─────────────────────
C_AI_SOLUTION_DESIGN = [
    Concept("ai_architecture", [
        "architecture", "rag", "retrieval augmented", "retrieval-augmented",
        "vector database", "vector db", "embeddings", "embedding", "llm",
        "large language model", "fine tune", "prompt", "prompt engineering",
        "context", "knowledge base", "chatbot", "conversational",
        "intent", "nlp", "pipeline", "api", "openai", "claude", "gemini",
        "hugging face", "langchain", "llamaindex", "semantic search",
        "resume parsing", "screening", "ranking", "matching", "guardrails",
        "hallucination", "evaluation", "feedback loop", "human in the loop",
        "integration", "scalable", "latency", "cost", "database",
    ], strong_forms=["rag", "vector database", "embeddings", "prompt engineering"]),
]

# ── AI: ML fundamentals (ML vs DL etc.) ────────────────────────────────────
C_ML_FUNDAMENTALS = [
    Concept("ml_concepts", [
        "machine learning", "deep learning", "neural network", "neural networks",
        "feature engineering", "handcrafted features", "manual features",
        "representation learning", "layers", "hidden layers", "many layers",
        "supervised", "unsupervised", "reinforcement", "algorithm",
        "decision tree", "random forest", "svm", "regression", "classification",
        "large data", "big data", "gpu", "backpropagation", "gradient",
        "automatically learns", "learns features", "structured data",
        "unstructured data", "image", "text", "subset of", "subset of ai",
        "subset of machine learning", "cnn", "rnn", "transformer",
    ], strong_forms=["deep learning", "neural network", "feature engineering",
                     "representation learning"]),
]

# ── AI: ethics / bias / fairness ───────────────────────────────────────────
C_AI_ETHICS = [
    Concept("fairness_bias", [
        "bias", "biased", "fairness", "fair", "unfair", "training data",
        "data bias", "representative", "underrepresented", "false negative",
        "false positive", "threshold", "confidence", "explainability",
        "explainable", "interpretability", "transparency", "audit",
        "human in the loop", "human review", "manual review", "second check",
        "override", "recheck the model", "check the criteria", "features used",
        "proxy", "discrimination", "ethical", "accountability", "recall",
        "why it rejected", "explain the decision", "look at the reason",
        "re evaluate", "feedback", "correct the model", "retrain",
    ], strong_forms=["bias", "false negative", "human in the loop", "explainability"]),
]

# ── AI: business impact / ROI ──────────────────────────────────────────────
C_AI_BUSINESS_IMPACT = [
    Concept("business_value", [
        "business impact", "business value", "roi", "return on investment",
        "automate", "automation", "efficiency", "save time", "save cost",
        "reduce cost", "increase revenue", "productivity", "scale",
        "faster", "reduce manual", "streamline", "customer experience",
        "predict", "prediction", "forecast", "recommendation", "personalise",
        "reduce churn", "conversion", "lead scoring", "screening",
        "demand forecasting", "route optimisation", "predictive maintenance",
        "high impact", "measurable impact", "solve a real problem",
        "value to the business", "bottom line", "competitive advantage",
    ], strong_forms=["business impact", "roi", "automate", "predictive maintenance"]),
]


# ── New-role category scorers ──────────────────────────────────────────────
def _sales_track_record_score(a, n, q):
    concepts = L.coverage_score(n, C_SALES_RESULTS)
    spec = L.signal_specificity(a) * 0.12          # numbers / deal sizes
    return _score(min(concepts + spec, 1.0), a, n)

def _negotiation_score(a, n, q):
    concepts = L.coverage_score(n, C_NEGOTIATION)
    return _score(concepts, a, n)

def _sales_process_score(a, n, q):
    concepts = L.coverage_score(n, C_SALES_PROCESS)
    step_boost = L.signal_steps(n) * 0.10
    return _score(min(concepts + step_boost, 1.0), a, n)

def _target_drive_score(a, n, q):
    concepts = L.coverage_score(n, C_TARGET_DRIVE)
    urg = L.signal_urgency(n) * 0.06
    return _score(min(concepts + urg, 1.0), a, n)

def _prospecting_score(a, n, q):
    concepts = L.coverage_score(n, C_PROSPECTING)
    spec = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec, 1.0), a, n)

def _sales_motivation_score(a, n, q):
    concepts = L.coverage_score(n, C_SALES_MOTIVATION)
    return _score(concepts, a, n)

def _revenue_ownership_score(a, n, q):
    concepts = L.coverage_score(n, C_REVENUE_OWNERSHIP)
    spec = L.signal_specificity(a) * 0.12
    return _score(min(concepts + spec, 1.0), a, n)

def _sales_strategy_score(a, n, q):
    concepts = L.coverage_score(n, C_SALES_STRATEGY)
    step_boost = L.signal_steps(n) * 0.06
    return _score(min(concepts + step_boost, 1.0), a, n)

def _team_leadership_score(a, n, q):
    concepts = L.coverage_score(n, C_TEAM_LEADERSHIP)
    own = L.signal_ownership(n) * 0.05
    return _score(min(concepts + own, 1.0), a, n)

def _key_account_retention_score(a, n, q):
    concepts = L.coverage_score(n, C_KEY_ACCOUNT)
    own = L.signal_ownership(n) * 0.06
    return _score(min(concepts + own, 1.0), a, n)

def _forecasting_score(a, n, q):
    concepts = L.coverage_score(n, C_FORECASTING)
    spec = L.signal_specificity(a) * 0.10
    return _score(min(concepts + spec, 1.0), a, n)

def _leadership_readiness_score(a, n, q):
    concepts = L.coverage_score(n, C_LEADERSHIP_READINESS)
    return _score(concepts, a, n)

def _resourcefulness_score(a, n, q):
    concepts = L.coverage_score(n, C_RESOURCEFULNESS)
    own = L.signal_ownership(n) * 0.06
    return _score(min(concepts + own, 1.0), a, n)

def _data_analysis_score(a, n, q):
    concepts = L.coverage_score(n, C_DATA_ANALYSIS)
    spec = L.signal_specificity(a) * 0.12
    return _score(min(concepts + spec, 1.0), a, n)

def _stakeholder_management_score(a, n, q):
    concepts = L.coverage_score(n, C_STAKEHOLDER_MGMT)
    return _score(concepts, a, n)

def _founder_fit_score(a, n, q):
    concepts = L.coverage_score(n, C_FOUNDER_FIT)
    return _score(concepts, a, n)

def _achievement_score(a, n, q):
    concepts = L.coverage_score(n, C_ACHIEVEMENT)
    spec = L.signal_specificity(a) * 0.10
    return _score(min(concepts + spec, 1.0), a, n)

def _performance_improvement_score(a, n, q):
    concepts = L.coverage_score(n, C_PERFORMANCE_IMPROVEMENT)
    step_boost = L.signal_steps(n) * 0.06
    return _score(min(concepts + step_boost, 1.0), a, n)

def _troubleshooting_score(a, n, q):
    concepts = L.coverage_score(n, C_TROUBLESHOOTING)
    spec = L.signal_specificity(a) * 0.10
    step_boost = L.signal_steps(n) * 0.06
    return _score(min(concepts + spec + step_boost, 1.0), a, n)

def _technical_knowledge_score(a, n, q):
    concepts = L.coverage_score(n, C_TECHNICAL_KNOWLEDGE)
    spec = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec, 1.0), a, n)

def _inspection_quality_score(a, n, q):
    concepts = L.coverage_score(n, C_INSPECTION_QA)
    step_boost = L.signal_steps(n) * 0.08
    return _score(min(concepts + step_boost, 1.0), a, n)

def _safety_awareness_score(a, n, q):
    concepts = L.coverage_score(n, C_SAFETY)
    return _score(concepts, a, n)

def _ml_debugging_score(a, n, q):
    concepts = L.coverage_score(n, C_ML_DEBUGGING)
    spec = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec, 1.0), a, n)

def _ai_project_depth_score(a, n, q):
    concepts = L.coverage_score(n, C_AI_PROJECT_DEPTH)
    spec = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec, 1.0), a, n)

def _ai_solution_design_score(a, n, q):
    concepts = L.coverage_score(n, C_AI_SOLUTION_DESIGN)
    return _score(concepts, a, n)

def _ml_fundamentals_score(a, n, q):
    concepts = L.coverage_score(n, C_ML_FUNDAMENTALS)
    return _score(concepts, a, n)

def _ai_ethics_bias_score(a, n, q):
    concepts = L.coverage_score(n, C_AI_ETHICS)
    return _score(concepts, a, n)

def _ai_business_impact_score(a, n, q):
    concepts = L.coverage_score(n, C_AI_BUSINESS_IMPACT)
    spec = L.signal_specificity(a) * 0.08
    return _score(min(concepts + spec, 1.0), a, n)


# ── Category dispatcher ─────────────────────────────────────────────────
CATEGORY_RULES = {
    # ── Original customer-support / operations competencies ──
    "empathy":                   _empathy_score,
    "communication":             _communication_score,
    "resolution":                _resolution_score,
    "problem_solving":           _problem_solving_score,
    "escalation_awareness":      _escalation_awareness_score,
    "customer_handling":         _customer_handling_score,
    "motivation":                _motivation_score,
    "role_understanding":        _role_understanding_score,
    "communication_quality":     _communication_quality_score,
    "prioritization":            _prioritization_score,
    "decision_making":           _decision_making_score,
    "operational_thinking":      _operational_thinking_score,
    "crisis_handling":           _crisis_handling_score,
    "escalation_process":        _escalation_process_score,
    "operations_understanding":  _operations_understanding_score,
    "stakeholder_communication": _stakeholder_communication_score,
    # ── Sales / business development ──
    "sales_track_record":        _sales_track_record_score,
    "negotiation":               _negotiation_score,
    "sales_process":             _sales_process_score,
    "target_drive":              _target_drive_score,
    "prospecting":               _prospecting_score,
    "sales_motivation":          _sales_motivation_score,
    # ── Sales leadership (DGM / GM) ──
    "revenue_ownership":         _revenue_ownership_score,
    "sales_strategy":            _sales_strategy_score,
    "team_leadership":           _team_leadership_score,
    "key_account_retention":     _key_account_retention_score,
    "forecasting":               _forecasting_score,
    "leadership_readiness":      _leadership_readiness_score,
    # ── Founder's office / management trainee ──
    "resourcefulness":           _resourcefulness_score,
    "data_analysis":             _data_analysis_score,
    "stakeholder_management":    _stakeholder_management_score,
    "founder_fit":               _founder_fit_score,
    "achievement":               _achievement_score,
    # ── Operations supervisor (extra) ──
    "performance_improvement":   _performance_improvement_score,
    # ── Field application engineer ──
    "troubleshooting":           _troubleshooting_score,
    "technical_knowledge":       _technical_knowledge_score,
    "inspection_quality":        _inspection_quality_score,
    "safety_awareness":          _safety_awareness_score,
    # ── AI engineer ──
    "ml_debugging":              _ml_debugging_score,
    "ai_project_depth":          _ai_project_depth_score,
    "ai_solution_design":        _ai_solution_design_score,
    "ml_fundamentals":           _ml_fundamentals_score,
    "ai_ethics_bias":            _ai_ethics_bias_score,
    "ai_business_impact":        _ai_business_impact_score,
}


# ══════════════════════════════════════════════════════════════════════════
# GUIDE-DERIVED FALLBACK for custom / new roles
# ══════════════════════════════════════════════════════════════════════════

def _guide_concepts(guide: str) -> list:
    stop = {
        "award", "high", "low", "marks", "mark", "score", "only", "if", "the",
        "for", "and", "with", "candidate", "evidence", "clear", "mid", "absent",
        "partial", "a", "an", "to", "of", "or", "is", "in", "on", "they",
        "their", "no", "not", "very", "just", "some", "more", "less", "than",
        "does", "this", "that", "when", "what", "would", "should", "could",
    }
    words = [w for w in re.findall(r"[a-z]{3,}", guide.lower()) if w not in stop]
    uniq = list(dict.fromkeys(words))[:20]
    return [Concept("guide", uniq)] if uniq else []


# ══════════════════════════════════════════════════════════════════════════
# THE PROVIDER
# ══════════════════════════════════════════════════════════════════════════

class RulesEngineProvider(AIProvider):
    """Offline deterministic scorer with comprehensive concept database."""
    name = "rules-engine"

    def complete_json(self, system: str, user: str) -> dict:
        # Publish this role's tuning so the module-level scorers can read it.
        # (Set by engine.score_ai on the provider instance; default neutral.)
        # NB: use an explicit None-check, not `or 50` — a legitimate 0 (fully
        # lenient / fully literal) is falsy and must NOT be coerced to neutral.
        _st = getattr(self, "strictness", 50)
        _cr = getattr(self, "creativeness", 50)
        _TUNING["strictness"]   = int(_st if _st is not None else 50)
        _TUNING["creativeness"] = int(_cr if _cr is not None else 50)
        s_axis, _c_axis = _tuning_axes()
        nudge = _clamp(0.15 - s_axis * 0.15, 0.0, 0.35)   # strict → smaller nudge

        cats   = self._extract("categories", user, as_json=True) or []
        answer = self._extract("answer", user) or ""
        answer = "" if answer.strip() == "(no answer provided)" else answer

        norm    = L.normalise(answer)
        quality = L.answer_depth(answer)

        category_scores, strengths, weaknesses = {}, [], []

        for c in cats:
            cid, label, mx = c.get("id", ""), c["label"], c["max"]

            scorer = CATEGORY_RULES.get(cid)
            if scorer:
                strength = scorer(answer, norm, quality)
            else:
                gc = _guide_concepts(c.get("guide", ""))
                if gc:
                    cs = L.best_concept(norm, gc)
                    strength = _score(cs, answer, norm)
                else:
                    # Truly unknown category — reward effort
                    wc = L.word_count(answer)
                    strength = min(L.answer_depth(answer) + (0.20 if wc >= 10 else 0), 0.75)

            if not norm:
                strength = 0.0

            # Convert to points — strictness-scaled nudge before rounding
            # (neutral nudge is +0.15, matching the original generous default).
            pts = round(strength * mx + nudge)
            if pts == 0 and strength > 0.05:
                pts = 1
            pts = min(pts, mx)

            category_scores[label] = {"score": pts, "max": mx}

            ratio = pts / mx if mx else 0
            if ratio >= 0.58:
                strengths.append(label)
            elif ratio <= 0.28:
                weaknesses.append(label)

        total = sum(v["score"] for v in category_scores.values())
        reasoning = self._build_reasoning(answer, norm, quality, category_scores)

        return {
            "score": total,
            "category_scores": category_scores,
            "strengths": strengths[:8],
            "weaknesses": weaknesses[:3],
            "reasoning": reasoning,
        }

    def validate(self):
        from scoring.providers import ValidationResult
        return ValidationResult(
            ok=True, status="ok",
            message="✓ Rules Engine active — comprehensive offline scoring. No API key required.",
            model_echo="rules-engine")

    @staticmethod
    def _extract(tag: str, text: str, as_json: bool = False):
        m = re.search(fr"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
        if not m:
            return None
        raw = m.group(1).strip()
        return json.loads(raw) if as_json else raw

    @staticmethod
    def _build_reasoning(answer, norm, quality, cat_scores) -> str:
        wc = L.word_count(answer)
        if not norm:
            return "No answer provided — no marks awarded."
        bits = [f"Rules-engine: {wc}-word answer (depth {quality:.2f})."]
        for label, o in cat_scores.items():
            pct = round(o["score"] / o["max"] * 100) if o["max"] else 0
            v = "strong" if pct >= 60 else "partial" if pct >= 32 else "limited"
            bits.append(f"{label}: {o['score']}/{o['max']} ({v}).")
        return " ".join(bits)
