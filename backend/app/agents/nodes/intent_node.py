import requests


def llama_classify(text: str) -> str:
    """
    Uses your local Ollama server to classify incident text.
    No API key needed. Runs fully on your laptop.
    """
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.1",   # or "phi3", "mistral", etc.
                "prompt": f"""
You are an incident classifier for a production reliability agent.

Classify the incident into one of:

- check_metrics
- search_logs
- check_database
- invalidate_cache
- check_pod_health
- run_diagnostics
- restart_service
- scale_service
- flush_db_pool
- simulate_incident
- open_incident_ticket
- investigate_incident

Incident: "{text}"

Return ONLY the intent label.
                """
            }
        )

        intent = response.json().get("response", "").strip().lower()
        return intent or "investigate_incident"

    except Exception as e:
        print("LLAMA ERROR:", e)
        return "investigate_incident"



def intent_node(state):
    user_message = (state.input or "").lower()
    print("STATE RECEIVED BY INTENT:", state)

   

    if "flush" in user_message and ("db" in user_message or "database" in user_message):
        state.intent = "flush_db_pool"

    elif "ticket" in user_message or "open incident" in user_message:
        state.intent = "open_incident_ticket"

    elif "restart" in user_message:
        state.intent = "restart_service"

    elif "scale" in user_message:
        state.intent = "scale_service"

    elif "cache" in user_message:
        state.intent = "invalidate_cache"

    elif "diagnostic" in user_message or "diagnose" in user_message:
        state.intent = "run_diagnostics"

    elif "simulate" in user_message:
        state.intent = "simulate_incident"

    elif "metrics" in user_message:
        state.intent = "check_metrics"

    elif "logs" in user_message:
        state.intent = "search_logs"

    elif "database" in user_message or "db" in user_message:
        state.intent = "check_database"

    elif "health" in user_message or "pod" in user_message:
        state.intent = "check_pod_health"

    else:
        state.intent = llama_classify(state.input)

    state.observations.append(f"[INTENT] {state.intent}")

    return state
