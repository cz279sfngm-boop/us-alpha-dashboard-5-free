# Supabase on-demand AI gateway

This function restores true on-demand AI Alpha Research without exposing the Gemini key in GitHub Pages.

Security model:
- Deploy with `verify_jwt=true`.
- Enable Supabase Anonymous Sign-Ins for the personal dashboard, or use a normal authenticated user.
- Store `GEMINI_API_KEY` as a Supabase Edge Function secret, never in the repository or browser.
- Set `ALLOWED_ORIGIN=https://cz279sfngm-boop.github.io`.
- The function accepts only whitelisted evidence fields from the current scanner dataset.
- The LLM prompt explicitly forbids model-memory company facts.

The scanner's pre-generated deterministic report remains the fallback if the gateway is unavailable.
