import random
from flask import Flask, render_template, request, redirect, url_for, flash, session
import os
import copy
import datetime
import json
import re # Import re for currency detection
from flask_session import Session
from werkzeug.utils import secure_filename # For secure filenames, though we don't save permanently here

# Import agent simulation functions and operations
import research_agent
import budget_operations

app = Flask(__name__)

# --- Flask-Session Configuration ---
# Use environment variable for production, fallback to os.urandom for dev
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))
# Handle cases where the env var might be explicitly set to empty or the default placeholder
if isinstance(app.config['SECRET_KEY'], bytes) and app.config['SECRET_KEY'] == b'':
     app.config['SECRET_KEY'] = os.urandom(24)
     print("Warning: FLASK_SECRET_KEY env var was empty, using temporary key.")
elif isinstance(app.config['SECRET_KEY'], str) and app.config['SECRET_KEY'] == 'generate_a_strong_random_secret_key_here':
     app.config['SECRET_KEY'] = os.urandom(24)
     print("Warning: Default FLASK_SECRET_KEY used, using temporary key.")


app.config['SESSION_TYPE'] = 'filesystem'
# Ensure the session directory exists relative to the app's root path
session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_session')
os.makedirs(session_dir, exist_ok=True)
app.config['SESSION_FILE_DIR'] = session_dir

app.config['SESSION_PERMANENT'] = False # Make sessions non-permanent (browser session based)
app.config['SESSION_USE_SIGNER'] = True # Sign the session cookie ID
app.config['SESSION_COOKIE_HTTPONLY'] = True # Prevent client-side JS access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Basic CSRF protection
# Consider setting SESSION_COOKIE_SECURE=True if deploying with HTTPS
# app.config['SESSION_COOKIE_SECURE'] = not app.debug

# Initialize the Flask-Session extension
server_session = Session(app)

# --- Constants ---
MAX_UPLOAD_SIZE = 100 * 1024 # 100 KB limit for uploaded file content in session
ALLOWED_EXTENSIONS = {'csv', 'json', 'txt'}

def allowed_file(filename):
    """Checks if the uploaded file has an allowed extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Context Processor ---
@app.context_processor
def inject_now():
    """Injects the current UTC datetime into the template context."""
    return {'now': datetime.datetime.utcnow()}

# --- Helper Function for Currency Detection ---
def detect_currency_symbol(text):
    """Detects common currency symbols or keywords in text."""
    if not text: return '$' # Default if text is empty
    text_lower = text.lower()
    # Order matters - check specific symbols first
    if '£' in text: return '£'
    if '€' in text: return '€'
    if '¥' in text: return '¥'
    if '₹' in text: return '₹'
    if '$' in text:
        # Distinguish between USD, CAD, AUD etc. if needed by checking keywords
        if 'cad' in text_lower or 'canadian' in text_lower: return 'CAD $' # Example
        if 'aud' in text_lower or 'australian' in text_lower: return 'AUD $' # Example
        return '$' # Default $
    # Check keywords
    if 'pound' in text_lower or 'gbp' in text_lower: return '£'
    if 'euro' in text_lower or 'eur' in text_lower: return '€'
    if 'yen' in text_lower or 'jpy' in text_lower: return '¥'
    if 'rupee' in text_lower or 'inr' in text_lower: return '₹'
    if 'dollar' in text_lower: return '$' # Default $ if just "dollar"
    # Fallback: If user entered something like 'USD', return it directly
    if len(text.strip()) <= 4: # Assume short codes are intended symbols/codes
         return text.strip().upper()
    # Default
    return '$'


# --- Flask Routes ---

@app.route('/')
def index():
    """Step 1: Display the initial goal input form."""
    session.clear() # Start fresh for each new plan
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_planning():
    """Step 1 -> Step 2: Get goal, budget, currency, file, ask AI questions."""
    goal = request.form.get('goal', '').strip()
    budget_amount_str = request.form.get('budget_amount', '').strip()
    currency_input = request.form.get('currency', '').strip()
    budget_file = request.files.get('budget_file') # Get file object

    # --- Basic Input Validation ---
    if not goal or len(goal) < 5:
        flash("Please enter a meaningful project goal (at least 5 characters).", "warning")
        return redirect(url_for('index'))
    if not budget_amount_str:
        # Should be caught by 'required' attribute, but double-check
        flash("Please enter an estimated budget amount.", "warning")
        return redirect(url_for('index'))

    # --- Validate and Store Budget Amount ---
    try:
        budget_amount = float(budget_amount_str)
        if budget_amount < 0:
             flash("Budget amount cannot be negative.", "warning")
             return redirect(url_for('index'))
        session['estimated_budget_amount'] = budget_amount
    except ValueError:
        flash("Invalid budget amount entered. Please enter a number.", "warning")
        return redirect(url_for('index'))

    # --- Store Goal and Currency ---
    session['project_goal'] = goal
    session['currency_symbol'] = detect_currency_symbol(currency_input) if currency_input else '$' # Use helper or default

    # --- Handle File Upload ---
    historical_data_content = None
    session.pop('historical_data', None) # Clear previous data
    if budget_file and budget_file.filename != '':
        # Secure filename is less critical since we read content, not save the file with user input name
        # filename = secure_filename(budget_file.filename)
        filename = budget_file.filename
        if allowed_file(filename):
            try:
                # Read content up to the limit
                file_content_bytes = budget_file.read(MAX_UPLOAD_SIZE + 1) # Read slightly more to check if limit exceeded
                if len(file_content_bytes) > MAX_UPLOAD_SIZE:
                    flash(f"Warning: Uploaded file '{filename}' exceeded size limit ({MAX_UPLOAD_SIZE/1024:.0f}KB) and was truncated.", "warning")
                    historical_data_content = file_content_bytes[:MAX_UPLOAD_SIZE].decode('utf-8', errors='ignore')
                else:
                    historical_data_content = file_content_bytes.decode('utf-8', errors='ignore')

                if historical_data_content:
                    session['historical_data'] = historical_data_content
                    print(f"Orchestrator: Stored historical data from file '{filename}' ({len(historical_data_content)} bytes).")
                    flash(f"Successfully processed data from '{filename}'.", "success")
                else:
                    flash(f"Uploaded file '{filename}' appears to be empty or could not be read as text.", "warning")

            except Exception as e:
                print(f"Error reading uploaded file: {e}")
                flash(f"Error processing uploaded file '{filename}': {e}", "danger")
                # Continue without historical data
        else:
            flash(f"Invalid file type for '{filename}'. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}", "warning")

    print(f"Orchestrator: Received Goal: {goal}")
    print(f"Orchestrator: Received Budget: {session['currency_symbol']}{budget_amount:.2f}")


    # --- Task Planning Agent for Questions (excluding budget amount) ---
    print("Orchestrator: Tasking Planning Agent for clarifying questions...")
    questions = research_agent.get_clarifying_questions(goal) # AI should no longer ask for budget

    if questions and isinstance(questions, list) and not questions[0].startswith("Error"):
        session['questions'] = questions
        print(f"Orchestrator: Planning Agent returned questions: {questions}")
        return render_template('ask_questions.html',
                               goal=goal,
                               questions=questions,
                               budget_info=f"{session['currency_symbol']}{budget_amount:.2f}" # Pass for display if needed
                               )
    else:
        error_msg = questions[0] if (questions and isinstance(questions, list)) else "AI failed to generate questions."
        flash(f"Error during planning phase: {error_msg}", "danger")
        # Clear potentially stored session data if failing here? Optional.
        # session.pop('project_goal', None) ... etc
        return redirect(url_for('index'))

@app.route('/generate', methods=['POST'])
def generate_plan():
    """Orchestrator for Initial Plan Generation"""
    print("Orchestrator: Starting initial plan generation...")
    goal = session.get('project_goal')
    questions = session.get('questions')
    budget_amount = session.get('estimated_budget_amount')
    currency_symbol = session.get('currency_symbol', '$')
    historical_data = session.get('historical_data', None) # Get historical data

    if not goal or not questions or budget_amount is None:
        flash("Session expired or invalid request (missing goal, questions, or budget). Please start over.", "warning")
        return redirect(url_for('index'))

    # 1. Collect User Answers
    answers = {}
    valid_answers = True
    for i, q in enumerate(questions):
        answer = request.form.get(f'answer_{i}', '').strip()
        if not answer:
            flash(f"Please answer question {i+1}.", "warning")
            valid_answers = False
        answers[q] = answer
    if not valid_answers:
         submitted_answers = {f'answer_{i}': request.form.get(f'answer_{i}', '') for i in range(len(questions))}
         return render_template('ask_questions.html', goal=goal, questions=questions, answers=submitted_answers, budget_info=f"{currency_symbol}{budget_amount:.2f}")
    session['answers'] = answers
    print(f"Orchestrator: Collected answers.")


    # --- Agent Workflow ---
    # 2. Task Research Agent (Pass historical data)
    print("Orchestrator: Tasking Research Agent...")
    research_summary = research_agent.run_research(goal, answers, historical_data) # <-- Pass historical_data
    session['research_summary'] = research_summary # Store even if None or blocked
    if not research_summary or "Error during AI call" in research_summary:
        flash("AI research summary could not be generated or failed.", "warning")
        print(f"Orchestrator: Research Agent failed/error: {research_summary}")
        research_summary = "Research summary was not available."
    elif "blocked by safety filters" in research_summary:
         flash("AI research summary was blocked by safety filters.", "warning")
         print("Orchestrator: Research Agent blocked.")
         research_summary = "Research summary blocked by safety filters." # Store message


    # 3. Task Budget Allocation Agent (Pass historical data)
    print("Orchestrator: Tasking Budget Allocation Agent...")
    proposed_budget_raw = research_agent.generate_budget_proposal(
        goal, budget_amount, currency_symbol, answers, research_summary, historical_data # <-- Pass historical_data
    )
    parsed_budget, is_percentage, initial_total = budget_operations.parse_budget_proposal(proposed_budget_raw)
    session['initial_budget'] = parsed_budget # Store parsed (might be error dict)
    # We now force amount-based, so is_percentage should be False unless error
    session['is_percentage_based'] = is_percentage
    session['ai_conversation'] = []
    session['reallocation_log'] = ["Initial budget plan generation started."]
    session.pop('pending_modification', None) # Clear any pending mod

    # Handle Allocation/Parsing Errors
    if "Error" in parsed_budget:
        error_msg = parsed_budget['Error']
        flash(f"Failed to process budget proposal from AI: {error_msg}", "danger")
        print(f"Orchestrator: Budget Allocation/Parsing failed: {error_msg}")
        session['current_budget'] = {}
        session['budget_explanation'] = None
        session['is_percentage_based'] = True # Treat as error state
        session['ai_conversation'].append({'ai': f"Error processing initial budget: {error_msg}"})
        session['reallocation_log'].append(f"Budget generation failed: {error_msg}")
        return redirect(url_for('display_plan')) # Go to display page to show error

    # Validate returned total vs expected (optional sanity check)
    if abs(initial_total - budget_amount) > max(1.0, budget_amount * 0.01): # Allow 1% or $1 tolerance
        warn_msg = f"AI proposal total ({currency_symbol}{initial_total:,.2f}) differs significantly from provided estimate ({currency_symbol}{budget_amount:,.2f}). Using AI's calculated total."
        flash(warn_msg, "warning")
        print(f"Orchestrator Warning: {warn_msg}")
        log_msg = f"AI total ({currency_symbol}{initial_total:,.2f}) differs from estimate ({currency_symbol}{budget_amount:,.2f})."
        session['reallocation_log'].append(log_msg)
    session['initial_total'] = initial_total # Store AI's calculated total


    # 4. Task Reasoning & Explanation Agent (Pass historical data)
    print("Orchestrator: Tasking Reasoning & Explanation Agent...")
    explanation = research_agent.generate_explanation(
        parsed_budget, goal, answers, research_summary, historical_data # <-- Pass historical_data
    )
    # Handle explanation errors/blocks
    if not explanation or "Error during AI call" in (explanation or ""):
         flash("Could not generate an explanation for the budget.", "warning")
         print(f"Orchestrator: Explanation Agent failed/error: {explanation}")
         explanation = "Explanation not available."
    elif "blocked by safety filters" in explanation:
         flash("AI budget explanation was blocked by safety filters.", "warning")
         print("Orchestrator: Explanation Agent blocked.")
         explanation = "Budget explanation blocked by safety filters." # Store message
    session['budget_explanation'] = explanation


    # 5. Set Final State (Now always amount-based if no error)
    session['reallocation_log'].append("AI proposed initial budget.")
    session['current_budget'] = copy.deepcopy(parsed_budget) # Set current budget
    print(f"Orchestrator: Amount-based budget generated. Total: {currency_symbol}{initial_total:.2f}")
    session['ai_conversation'].append({'ai': f"Amount-based budget (in {currency_symbol}) generated. Explanation provided. Ready for interaction."})
    session['reallocation_log'].append("Budget plan generation complete.")


    print("Orchestrator: Initial plan generation complete.")
    return redirect(url_for('display_plan'))


@app.route('/plan')
def display_plan():
    """Step 3 & 4: Display the generated plan and controls."""
    goal = session.get('project_goal')
    if not goal:
        flash("Please start by defining your project goal.", "warning")
        return redirect(url_for('index'))

    # Retrieve data from session
    research = session.get('research_summary', "N/A")
    initial_budget = session.get('initial_budget', {})
    current_budget = session.get('current_budget', {})
    # is_percentage should reliably be False if generated successfully now
    is_percentage = session.get('is_percentage_based', False)
    log = session.get('reallocation_log', ["Log not available."])
    initial_total = session.get('initial_total', 0.0)
    ai_conversation = session.get('ai_conversation', [])
    pending_modification = session.get('pending_modification')
    explanation = session.get('budget_explanation')
    currency_symbol = session.get('currency_symbol', '$') # Get currency symbol


    current_total = sum(v for v in current_budget.values() if isinstance(v, (int, float))) if current_budget else 0.0

    # Prepare data for Chart.js
    chart_labels = None
    chart_values = None
    initial_budget_has_error = isinstance(initial_budget, dict) and "Error" in initial_budget
    current_budget_has_error = isinstance(current_budget, dict) and "Error" in current_budget

    # Ensure chart data is generated only for valid, numeric budgets
    if not is_percentage and current_budget and not current_budget_has_error and not initial_budget_has_error:
        chart_data = {k: v for k, v in current_budget.items() if isinstance(v, (int, float)) and v > 0.005}
        if chart_data:
            sorted_chart_data = dict(sorted(chart_data.items(), key=lambda item: item[1], reverse=True))
            chart_labels = list(sorted_chart_data.keys())
            chart_values = list(sorted_chart_data.values())


    return render_template('budget_plan.html',
                           goal=goal,
                           research_summary=research,
                           initial_budget=initial_budget,
                           initial_budget_has_error=initial_budget_has_error,
                           current_budget=current_budget,
                           current_budget_has_error=current_budget_has_error, # Pass current budget error status
                           is_percentage_based=is_percentage, # Should mostly be False now
                           log=log,
                           initial_total=initial_total,
                           current_total=current_total,
                           chart_labels=chart_labels,
                           chart_values=chart_values,
                           ai_conversation=ai_conversation,
                           pending_modification=pending_modification,
                           budget_explanation=explanation,
                           currency_symbol=currency_symbol # Pass symbol
                           )

# --- AI Interaction Route ---
@app.route('/interact_ai', methods=['POST'])
def interact_ai():
    """Orchestrator for AI Chat Interactions"""
    print("Orchestrator: Starting AI interaction...")
    user_request = request.form.get('ai_request', '').strip()
    # Load state
    current_budget = session.get('current_budget', {})
    initial_budget = session.get('initial_budget', {})
    is_percentage = session.get('is_percentage_based', False) # Assume False if generated correctly
    conversation = session.get('ai_conversation', [])
    log = session.get('reallocation_log', [])
    goal = session.get('project_goal')
    answers = session.get('answers', {})
    currency = session.get('currency_symbol', '$')
    current_budget_has_error = isinstance(current_budget, dict) and "Error" in current_budget

    session.pop('pending_modification', None) # Clear previous pending

    if not user_request:
        flash("Please enter a question or modification request.", "warning")
        return redirect(url_for('display_plan'))

    conversation.append({'user': user_request})
    print(f"Orchestrator: Received user request: {user_request}")

    modification_keywords = ['change', 'modify', 'update', 'set', 'increase', 'decrease', 'add', 'remove', 'allocate', 'adjust', 'revise']
    is_modification = any(keyword in user_request.lower() for keyword in modification_keywords)

    ai_context = {'goal': goal, 'answers': answers}
    ai_response = "Sorry, I encountered an unexpected issue processing your request."

    if is_modification:
        print("Orchestrator: Identified as modification request.")
        if is_percentage or not current_budget or current_budget_has_error:
             error_msg = "Budget is percentage-based." if is_percentage else current_budget.get("Error", "No numerical budget found.")
             ai_response = f"Cannot modify budget: {error_msg}"
             flash(ai_response, "danger" if current_budget_has_error else "warning")
             conversation.append({'ai': ai_response}) # Add AI response to conversation
        else:
            print("Orchestrator: Tasking Modification Agent...")
            modified_budget_raw = research_agent.modify_budget_proposal(user_request, current_budget, ai_context)
            new_parsed_budget, new_is_percentage, new_total = budget_operations.parse_budget_proposal(modified_budget_raw)

            if "Error" in new_parsed_budget:
                error_detail = new_parsed_budget['Error']
                ai_response = f"Sorry, I couldn't generate a valid modification proposal. Error: {error_detail}"
                flash(f"AI failed to generate modification: {error_detail}", "danger")
                print(f"Orchestrator: Modification Agent failed: {error_detail}")
                conversation.append({'ai': ai_response})
            elif new_is_percentage: # Should not happen now
                 ai_response = "Sorry, the modification unexpectedly resulted in a percentage budget."
                 flash("AI modification failed: Unexpected percentage budget.", "danger")
                 print("Orchestrator: Modification Agent Error: Returned % budget.")
                 conversation.append({'ai': ai_response})
            else:
                # VALID PROPOSAL: Store for user approval
                session['pending_modification'] = new_parsed_budget
                ai_response = f"OK, I have prepared a proposed modification (in {currency}). Please review the changes shown below. Do you want to apply them?"
                flash("AI has proposed changes. Review and Approve/Reject.", "info")
                print("Orchestrator: Modification Agent succeeded. Proposal pending.")
                conversation.append({'ai': ai_response})

    else:
        # --- Handle Question ---
        print("Orchestrator: Identified as question. Tasking Q&A Agent...")
        initial_budget_has_error = isinstance(initial_budget, dict) and "Error" in initial_budget
        # Use current if valid, else initial if valid, else none
        budget_for_qna = None
        if not is_percentage and current_budget and not current_budget_has_error:
             budget_for_qna = current_budget
        elif initial_budget and not initial_budget_has_error:
             budget_for_qna = initial_budget

        if not budget_for_qna:
             ai_response = "No valid budget data is available to answer questions about."
             conversation.append({'ai': ai_response})
        else:
            ai_qna_response = research_agent.answer_budget_question(user_request, budget_for_qna, ai_context)
            ai_response = ai_qna_response if ai_qna_response else "Sorry, I couldn't get an answer from the AI."
            if "blocked by safety filters" in ai_response or "Error during AI call" in ai_response :
                 flash(f"AI interaction failed: {ai_response}", "warning")
            conversation.append({'ai': ai_response})
        print("Orchestrator: Q&A Agent finished.")

    # Save updated conversation state
    session['ai_conversation'] = conversation
    print("Orchestrator: Interaction complete.")
    return redirect(url_for('display_plan'))


# --- Apply Modification Route ---
@app.route('/apply_modification/<action>', methods=['POST'])
def apply_modification(action):
    """Handles user approval/rejection of AI modification proposal."""
    pending_mod = session.get('pending_modification')
    log = session.get('reallocation_log', [])
    conversation = session.get('ai_conversation', [])
    currency = session.get('currency_symbol', '$')

    if not pending_mod:
        flash("No pending modification found.", "warning")
        return redirect(url_for('display_plan'))

    if action == 'approve':
        if isinstance(pending_mod.get("Error", None), str):
             flash(f"Cannot approve modification due to error: {pending_mod['Error']}", "danger")
        else:
             # Optional: Validate total hasn't drastically changed
             current_total_before = sum(v for v in session.get('current_budget', {}).values() if isinstance(v, (int, float)))
             new_total_proposed = sum(v for v in pending_mod.values() if isinstance(v, (int, float)))
             # Allow slightly more tolerance for complex reallocations by AI
             if abs(current_total_before - new_total_proposed) > max(0.05, current_total_before * 0.001): # 5 cents or 0.1%
                  log_msg = f"AI Mod Applied Note: Budget total changed from {currency}{current_total_before:,.2f} to {currency}{new_total_proposed:,.2f}."
                  log.append(log_msg)
                  print(f"Warning: {log_msg}")
                  flash(f"Note: Budget total changed to {currency}{new_total_proposed:,.2f}.", "info")

             session['current_budget'] = pending_mod # Apply change
             log.append("Budget modification proposed by AI was approved and applied.")
             conversation.append({'ai': "OK, I've applied the approved changes."})
             flash("Approved changes applied.", "success")
             print("Orchestrator: Modification approved.")
    elif action == 'reject':
        log.append("Budget modification proposed by AI was rejected.")
        conversation.append({'ai': "OK, the proposed changes were discarded."})
        flash("Proposed changes rejected.", "info")
        print("Orchestrator: Modification rejected.")
    else:
        flash("Invalid action.", "danger")
        log.append(f"Invalid modification action: {action}")


    # Clear the pending modification in all cases after action
    session.pop('pending_modification', None)

    # Save updated log and conversation
    session['reallocation_log'] = log
    session['ai_conversation'] = conversation

    return redirect(url_for('display_plan'))


# --- Reallocation Routes ---

@app.route('/trigger_event', methods=['POST'])
def trigger_event():
    """Handles dynamic reallocation based on user-defined event."""
    current_budget = session.get('current_budget')
    log = session.get('reallocation_log', [])
    is_percentage = session.get('is_percentage_based', False) # Should be false now
    currency = session.get('currency_symbol', '$')
    current_budget_has_error = isinstance(current_budget, dict) and "Error" in current_budget


    if session.get('pending_modification'):
         session.pop('pending_modification', None)
         log.append("Pending AI modification cancelled due to manual reallocation trigger.")
         flash("Pending AI modification cancelled.", "info")


    if is_percentage or not current_budget or current_budget_has_error:
        flash("Cannot reallocate: Budget invalid or percentage-based.", "error")
        session['reallocation_log'] = log # Save cancellation log if applicable
        return redirect(url_for('display_plan'))

    event_category = request.form.get('event_category', '').strip()
    event_amount_str = request.form.get('event_amount', '').strip()

    if not event_category or not event_amount_str:
        flash("Category and amount required for event.", "warning")
        session['reallocation_log'] = log
        return redirect(url_for('display_plan'))

    # Perform reallocation using the dedicated function
    new_budget, new_log, success = budget_operations.perform_reallocation(
        event_category, event_amount_str, current_budget, log # Pass amount as string initially
    )

    # Update session
    session['current_budget'] = new_budget
    session['reallocation_log'] = new_log

    if success:
        flash(f"Reallocation processed for '{event_category}'.", 'success')
    else:
        # Failure message should be in the log now
        flash("Reallocation failed. Check activity log for details.", "danger")

    return redirect(url_for('display_plan'))


@app.route('/trigger_random_event', methods=['POST'])
def trigger_random_event():
    """Handles dynamic reallocation based on a random simulation."""
    current_budget = session.get('current_budget')
    log = session.get('reallocation_log', [])
    is_percentage = session.get('is_percentage_based', False) # Should be false now
    currency = session.get('currency_symbol', '$')
    current_budget_has_error = isinstance(current_budget, dict) and "Error" in current_budget


    if session.get('pending_modification'):
         session.pop('pending_modification', None)
         log.append("Pending AI modification cancelled due to random event trigger.")
         flash("Pending AI modification cancelled.", "info")

    if is_percentage or not current_budget or current_budget_has_error:
        flash("Cannot reallocate random event: Budget invalid or percentage-based.", "error")
        session['reallocation_log'] = log
        return redirect(url_for('display_plan'))

    # Filter for categories with actual funds (numeric and > 0)
    valid_categories = [k for k, v in current_budget.items() if isinstance(v, (int, float)) and v > 0.005]
    if not valid_categories:
         flash("Cannot trigger random event: No categories with positive funds available.", "warning")
         session['reallocation_log'] = log
         return redirect(url_for('display_plan'))

    # Choose a target (could be existing or new)
    target_category = random.choice(valid_categories + ["Unforeseen Technical Issue", "Supplier Price Increase", "Emergency Repair"])

    # Determine amount (e.g., 1-10% of total, within bounds)
    current_total_budget = sum(v for v in current_budget.values() if isinstance(v, (int, float)))
    # Ensure max_possible_amount is reasonable even if total budget is small
    max_possible_amount = max(50.0, current_total_budget * 0.10) if current_total_budget > 0 else 100.0
    min_possible_amount = 25.0
    # Ensure max isn't less than min if total budget is very small
    max_amount = max(min_possible_amount, max_possible_amount)
    event_amount = round(random.uniform(min_possible_amount, max_amount), 2)


    # Perform reallocation
    new_budget, new_log, success = budget_operations.perform_reallocation(
        target_category, event_amount, current_budget, log
    )

    # Update session
    session['current_budget'] = new_budget
    session['reallocation_log'] = new_log

    if success:
        flash(f"Random event simulation: {currency}{event_amount:.2f} allocated to '{target_category}'.", 'success')
    else:
         flash("Random event reallocation failed. Check activity log.", "danger")

    return redirect(url_for('display_plan'))


# --- Main Execution ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    # Default to debug=True if FLASK_DEBUG is not set or not '0'
    is_debug = os.environ.get("FLASK_DEBUG", "1") != "0"
    print(f"Starting Flask app on port {port} with debug={'True' if is_debug else 'False'}")
    app.run(debug=is_debug, host='0.0.0.0', port=port)