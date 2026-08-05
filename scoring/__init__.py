"""
scoring/ — AI provider configuration, shared by CV parsing (ai_resume_parser.py).

Candidate/form-response scoring has been removed from this app; this package
now only holds the provider-connection plumbing (enable toggle, provider/
model/API key, validation) that ai_resume_parser.py uses for AI-assisted
resume parsing. The "scoring" name is legacy, kept to avoid an unrelated
import-path rename.

Plugs into the main FastAPI app:

    from scoring.router import router as scoring_router
    app.include_router(scoring_router)

Layers:
    config_store.py  encrypted provider config (Fernet) + feature toggle
    providers.py     AIProvider / Claude / OpenAI / Gemini / Groq / HuggingFace + factory
    router.py        HTTP endpoints for the above

Persistence (file-based, matching the rest of the app):
    output/ai_config.json     provider config (key encrypted)
    output/.ai_secret.key      Fernet key (chmod 600)
"""

from scoring.router import router  # noqa: F401
