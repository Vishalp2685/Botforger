import google.generativeai as genai
import os
import json
import re
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

# --- Configuration & Helpers (_clean_json_response, _call_gemini - Keep as before) ---
# ... (ensure these are present) ...
try:
    # configure_gemini()
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    print("Gemini model initialized successfully.")
except Exception as e:
    print(f"Error initializing Gemini model: {e}")
    gemini_model = None
def _clean_json_response(text):
    if not text: return text
    start_brace = text.find('{'); start_bracket = text.find('['); end_brace = text.rfind('}'); end_bracket = text.rfind(']')
    start_index = -1; end_index = -1
    if start_brace != -1 and end_brace != -1:
        if start_bracket == -1 or start_brace < start_bracket: start_index = start_brace; end_index = end_brace + 1
    elif start_bracket != -1 and end_bracket != -1: start_index = start_bracket; end_index = end_bracket + 1
    if start_index != -1 and end_index != -1:
        potential_json = text[start_index:end_index]
        if (potential_json.startswith('{') and potential_json.endswith('}')) or (potential_json.startswith('[') and potential_json.endswith(']')): return potential_json
    cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.IGNORECASE); cleaned = re.sub(r'\s*```$', '', cleaned)
    return cleaned.strip()
def _call_gemini(prompt):
    if not gemini_model: print("Error: Gemini model not initialized."); return None
    try:
        print(f"\n--- Sending Prompt to Gemini ({len(prompt)} chars) ---\n{prompt[:500]}...\n--------------------")
        response = gemini_model.generate_content(prompt); print("--- Gemini Response Received ---")
        if not response.candidates:
             reason = "Unknown"
             try: reason = response.prompt_feedback.block_reason.name
             except Exception: pass; print(f"Warning: Response blocked. Reason: {reason}"); return f"Response blocked by safety filters: {reason}"
        if hasattr(response, 'parts') and response.parts: return "".join(part.text for part in response.parts if hasattr(part, 'text'))
        if hasattr(response, 'text'): return response.text
        if hasattr(response, 'candidates') and response.candidates:
             candidate = response.candidates[0]
             if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'): return "".join(part.text for part in candidate.content.parts if hasattr(part, 'text'))
        print(f"Warning: Unexpected response structure: {response}"); return None
    except Exception as e: print(f"Error calling Gemini API: {e}"); return f"Error during AI call: {e}"
# --- End Helpers ---


# --- Agent Simulation Functions ---

# Role: Planning & Clarification Agent (Prompt updated NOT to ask for budget)
def get_clarifying_questions(goal):
    """Uses Gemini to generate clarifying questions (excluding budget amount)."""
    prompt = f"""
    Act as a budget planning assistant tasked with clarifying a user's goal. The user has already provided their project goal and an estimated total budget amount separately.
    User Goal: '{goal}'
    Your task: Generate 4-6 concise, critical questions to understand scope, constraints, resources, deadlines, priorities, and any existing data relevant to creating a detailed budget breakdown. **DO NOT ask for the total estimated budget amount or overall funding level**, as this is already known. Focus on *other* details needed for allocation.
    Output Format: MUST be a JSON list of strings. No extra text.
    Example: ["What are the key deadlines or milestones?", "Are there existing resources (personnel, equipment) available?", "What are the top 3 priorities for this project?", "Are there known regulatory hurdles?"]
    """
    response_text = _call_gemini(prompt)
    # ... (rest of parsing/error handling unchanged) ...
    if not response_text or "blocked" in response_text or "Error" in response_text: return ["Error: Failed to get questions." + (f" ({response_text})" if response_text else "")]
    cleaned = _clean_json_response(response_text)
    try: questions = json.loads(cleaned); return questions if isinstance(questions, list) and all(isinstance(q, str) for q in questions) else ["Error: AI response not list."]
    except Exception as e: print(f"JSON Error: {e}\nCleaned: {cleaned}"); return ["Error: Could not parse questions."]


# Role: Research Agent (Accepts historical_data)
def run_research(goal, answers_dict, historical_data=None):
    """Uses Gemini to perform deeper research based on goal, answers, and historical data."""
    if not isinstance(answers_dict, dict): answers_dict = {}
    answers_formatted = "\n".join([f"- {q}: {a}" for q, a in answers_dict.items() if a])
    if not answers_formatted: answers_formatted = "No specific context provided beyond the goal."

    # Truncate historical data for prompt to avoid excessive length
    HISTORICAL_DATA_PROMPT_LIMIT = 2000
    historical_data_summary = "No previous data provided."
    if historical_data:
        if len(historical_data) > HISTORICAL_DATA_PROMPT_LIMIT:
             historical_data_summary = historical_data[:HISTORICAL_DATA_PROMPT_LIMIT] + "\n... (data truncated)"
        else:
             historical_data_summary = historical_data

    prompt = f"""
    Act as an expert research analyst providing in-depth background for budgeting a specific project.
    Your goal is to produce detailed, actionable insights relevant to the user's request.

    **Project Goal:** '{goal}'

    **User Provided Context & Constraints:**
    {answers_formatted}

    **Previous Budget Data (if provided by user):**
    ```
    {historical_data_summary}
    ```
    (Use this historical context, if relevant, to identify past cost drivers, successful allocations, or areas of over/under spending.)

    **Your Task:** Perform detailed research and provide a structured analysis covering the following key areas **specifically tailored to the project goal, user context, AND historical data (if applicable)**:

    1.  **Detailed Cost Categories/Phases:** (Break down typical lifecycle & specific costs...)
    2.  **Essential Resources:** (Personnel roles, materials, equipment...)
    3.  **Significant Risks & Challenges:** (Financial risks, delays, common underestimations...)
    4.  **Key Cost Influencing Factors:** (Scale, location, quality, features, regulations...)
    5.  **Benchmarks & Typical Ranges (Use Caution):** (Mention indicative ranges only if widely recognized public data exists, state variability...)

    **Output Format:** Use Markdown with clear headings for each section and bullet points for detail. Ensure analysis is relevant and avoids generic info.
    """
    response_text = _call_gemini(prompt)
    if response_text and ("blocked" in response_text or "Error" in response_text):
        return f"Research summary generation failed: {response_text}"
    return response_text


# Role: Budget Allocation Agent (Accepts historical_data, expects amount)
def generate_budget_proposal(goal, budget_amount, currency_symbol, answers_dict, research_summary, historical_data=None):
    """Uses Gemini to propose a budget allocation dictionary based on a GIVEN amount and historical data."""
    answers_formatted = "\n".join([f"- {q}: {a}" for q, a in answers_dict.items() if a])

    # Truncate historical data for prompt
    HISTORICAL_DATA_PROMPT_LIMIT = 2000
    historical_data_summary = "No previous data provided."
    if historical_data:
        if len(historical_data) > HISTORICAL_DATA_PROMPT_LIMIT:
             historical_data_summary = historical_data[:HISTORICAL_DATA_PROMPT_LIMIT] + "\n... (data truncated)"
        else:
             historical_data_summary = historical_data

    prompt = f"""
    Act as an expert budget planner. You are given the project goal, user context, research, a **specific total budget amount**, and potentially historical data.
    Your Task: Propose a detailed budget allocation based on all this information.

    Project Goal: '{goal}'
    Total Estimated Budget: {currency_symbol}{budget_amount:,.2f} # Provided for context, DO NOT include symbol/commas in output values
    User Provided Context/Answers: {answers_formatted}
    AI Research Summary: {research_summary if research_summary and 'blocked' not in research_summary else "N/A"}
    Previous Budget Data (if provided):
    ```
    {historical_data_summary}
    ```
    (Use historical data to inform category choices, allocation ratios, or identify potential adjustments compared to the past, if relevant.)

    Instructions:
    1. Identify 5-10 key, distinct budget categories relevant to THIS goal (e.g., 'Site Preparation').
    2. Include a 'Contingency' category (allocate appropriate amount, e.g., 5-20% of total).
    3. Allocate the **entire provided Total Estimated Budget** ({budget_amount:.2f}) across your chosen categories using **numerical amounts** only. The sum MUST equal the total budget.
    4. Format output *strictly* as a JSON object. Keys=category names (strings), values=allocated **numerical amounts** (numbers only, no symbols, no commas, no percentages).
    5. Ensure all values are non-negative numbers.

    Example Output (if total budget was 50000):
    {{"Planning": 5000, "Materials": 15000, "Labor": 18000, "Contingency": 5000, ...}}

    Output ONLY the JSON object representing the complete, amount-based budget breakdown.
    """
    response_text = _call_gemini(prompt)
    # ... (rest of parsing/error handling unchanged) ...
    if not response_text or "blocked" in response_text or "Error" in response_text: return {"Error": "Failed to get budget proposal." + (f" ({response_text})" if response_text else "")}
    cleaned = _clean_json_response(response_text)
    try: budget_dict = json.loads(cleaned); return budget_dict if isinstance(budget_dict, dict) and budget_dict else {"Error": "AI returned empty proposal."}
    except Exception as e: print(f"JSON Error: {e}\nCleaned: {cleaned}"); return {"Error": f"Could not parse proposal. Raw: {cleaned[:200]}"}


# Role: Reasoning & Explanation Agent (Accepts historical_data)
def generate_explanation(proposed_budget, goal, answers_dict, research_summary, historical_data=None):
    """Generates a user-friendly explanation for the proposed budget, considering historical data."""
    if not isinstance(proposed_budget, dict) or "Error" in proposed_budget:
        return "Cannot generate explanation: Budget proposal has an error or is missing."

    budget_string = json.dumps(proposed_budget, indent=2)
    answers_formatted = "\n".join([f"- {q}: {a}" for q, a in answers_dict.items() if a])

    # Truncate historical data for prompt
    HISTORICAL_DATA_PROMPT_LIMIT = 1500 # Shorter limit for explanation context
    historical_data_summary = "No previous data provided."
    if historical_data:
        if len(historical_data) > HISTORICAL_DATA_PROMPT_LIMIT:
             historical_data_summary = historical_data[:HISTORICAL_DATA_PROMPT_LIMIT] + "\n... (data truncated)"
        else:
             historical_data_summary = historical_data

    prompt = f"""
    Act as a budget communicator. Explain the *reasoning* behind the proposed budget allocation in 2-4 clear paragraphs. Connect allocations to the user's goal, context, research, and **any relevant insights from the historical data provided**. Highlight key categories like 'Contingency'. Focus on the 'why', not just repeating numbers.

    Project Goal: '{goal}'
    User Context: {answers_formatted}
    AI Research Summary: {research_summary if research_summary and 'blocked' not in research_summary else "N/A"}
    Previous Budget Data Context: {historical_data_summary}
    Proposed Budget: ```json\n{budget_string}\n```

    Your Explanation (rationale-focused):
    """
    response_text = _call_gemini(prompt)
    if response_text and ("blocked" in response_text or "Error" in response_text):
        return f"Budget explanation generation failed: {response_text}"
    return response_text if response_text else "Explanation could not be generated."


# --- Q&A and Modification Functions (No historical data needed directly, uses current context) ---

def answer_budget_question(question, current_budget_dict, context_dict):
    # ... (keep existing code) ...
    if not isinstance(current_budget_dict, dict) or not current_budget_dict or "Error" in current_budget_dict: return "Cannot answer question: No valid budget data."
    budget_string = json.dumps(current_budget_dict, indent=2); context_string = "\n".join([f"- {q}: {a}" for q, a in context_dict.get('answers', {}).items()]); goal = context_dict.get('goal', 'N/A')
    prompt = f"""Act as budget assistant answering question. Goal: {goal}. Context: {context_string if context_string else "N/A"}. Budget: ```json\n{budget_string}\n``` User Question: "{question}". Task: Answer concisely based ONLY on provided info. If unsure, say so. Answer:"""
    response_text = _call_gemini(prompt); return response_text if response_text and "blocked" not in response_text and "Error" not in response_text else ("Answer generation failed: " + response_text if response_text else "Issue answering.")

def modify_budget_proposal(modification_request, current_budget_dict, context_dict):
     # ... (keep existing code - ensures amount based, asks only for JSON) ...
    if not isinstance(current_budget_dict, dict) or not current_budget_dict or "Error" in current_budget_dict: return {"Error": "No valid current budget."}
    is_percentage = any(isinstance(v, str) and '%' in v for v in current_budget_dict.values())
    if is_percentage: return {"Error": "Modifying percentage budgets not supported."}
    budget_string = json.dumps(current_budget_dict, indent=2); context_string = "\n".join([f"- {q}: {a}" for q, a in context_dict.get('answers', {}).items()]); goal = context_dict.get('goal', 'N/A')
    prompt = f"""Act as budget modification assistant (amount-based). Goal: {goal}. Context: {context_string if context_string else "N/A"}. Current Budget: ```json\n{budget_string}\n``` User Request: "{modification_request}". Task: Generate JSON for new budget reflecting request. Keep total identical via reallocation (use Contingency first). Values must be numbers. Output ONLY the JSON object."""
    response_text = _call_gemini(prompt)
    if not response_text or "blocked" in response_text or "Error" in response_text: return {"Error": "Failed to get modification proposal." + (f" ({response_text})" if response_text else "")}
    cleaned = _clean_json_response(response_text)
    try: modified_budget = json.loads(cleaned); return modified_budget if isinstance(modified_budget, dict) and modified_budget else {"Error": "AI returned empty proposal."}
    except Exception as e: print(f"JSON Error: {e}\nCleaned: {cleaned}"); return {"Error": f"Could not parse modified budget. Raw: {cleaned[:200]}"}