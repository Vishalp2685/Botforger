import copy

def parse_budget_proposal(proposal_dict):
    """
    Parses the raw budget dictionary from Gemini.
    Determines if it's percentage or dollar based.
    Converts dollar amounts to floats.
    Returns a tuple: (parsed_budget_dict, is_percentage_based, total_amount | None)
    Returns ({'Error': msg}, True, None) on failure.
    """
    if not isinstance(proposal_dict, dict):
         # Handle case where Gemini response wasn't even a dict
         return ({'Error': f'AI response was not a valid dictionary structure. Response: {str(proposal_dict)[:100]}...'}, True, None)

    if "Error" in proposal_dict: # Propagate errors from Gemini step
        return (proposal_dict, True, None)

    parsed_budget = {}
    is_percentage = False
    total = 0.0
    has_numeric_values = False
    has_percentage_values = False

    if not proposal_dict: # Handle empty dictionary case
        return ({'Error': 'AI returned an empty budget proposal.'}, True, None)

    for category, value in proposal_dict.items():
        # Clean up category names slightly
        clean_category = category.strip() if isinstance(category, str) else str(category)

        if isinstance(value, str) and '%' in value:
            try:
                percent_val = float(value.strip().strip('%'))
                parsed_budget[clean_category] = f"{percent_val}%" # Store consistent format
                has_percentage_values = True
                total += percent_val # Summing percentages for validation
            except ValueError:
                 print(f"Warning: Could not parse percentage value: {value} for category '{clean_category}'")
                 # Treat as error if mixing types, otherwise problematic string
                 return ({'Error': f"Invalid percentage format '{value}' for category '{clean_category}'."}, True, None)

        elif isinstance(value, (int, float)):
            parsed_budget[clean_category] = float(value)
            total += float(value)
            has_numeric_values = True
        elif isinstance(value, str): # Try converting string numbers
             try:
                 # Handle potential currency symbols and commas
                 cleaned_value_str = re.sub(r'[$,£€]', '', value.strip())
                 numeric_value = float(cleaned_value_str.replace(',', ''))
                 parsed_budget[clean_category] = numeric_value
                 total += numeric_value
                 has_numeric_values = True
             except ValueError:
                  print(f"Warning: Budget category '{clean_category}' has unparsable string value '{value}'.")
                  return ({'Error': f"Category '{clean_category}' has non-numeric/non-percentage value '{value}'. Cannot mix types."}, True, None)
        else:
            # Handle other unexpected types like lists or nested dicts if they occur
            return ({'Error': f"Invalid data type '{type(value)}' for category '{clean_category}'."}, True, None)

    # Decision: If *any* percentage values are present, treat the whole budget as percentage-based.
    is_percentage = has_percentage_values

    if is_percentage and has_numeric_values:
         print("Error: Mixed percentage and numeric values found in AI proposal. Cannot process.")
         return ({'Error': "AI returned a mix of percentage and dollar values, which is ambiguous."}, True, None)

    if is_percentage:
         # Optional: Validate percentage sum is close to 100
         if abs(total - 100.0) > 1.5: # Allow slightly larger tolerance
             print(f"Warning: Percentages sum to {total:.2f}%, not 100%.")
             # Could return error or just warning:
             # return ({'Error': f"Percentages sum to {total:.2f}%, not 100%."}, True, None)
         return parsed_budget, True, None # No total dollar amount for percentage budgets
    elif has_numeric_values:
         # Ensure 'Contingency' exists if dollar based
         contingency_key = next((k for k in parsed_budget if 'contingency' in k.lower()), None)
         if not contingency_key and parsed_budget: # Check if budget is not empty
              print("Warning: 'Contingency' category missing in dollar-based budget. Adding with 0.0.")
              parsed_budget['Contingency'] = 0.0
         return parsed_budget, False, round(total, 2) # Return total dollar amount, rounded
    else:
        # Case where parsing resulted in neither % nor numbers (e.g., only errors or empty dict originally)
        return ({'Error': 'No valid budget entries found after parsing.'}, True, None)


def find_source_funds(amount_needed, current_budget_state):
    """
    Finds where to pull funds from based on rules. Enhanced logic.
    Returns list of tuples [(source_category, amount_to_pull), ...] or None.
    """
    sources = []
    remaining_needed = round(amount_needed, 2)

    if remaining_needed <= 0:
        return []

    # Work on a numeric copy, rounded, excluding potential non-numeric entries if any slipped through
    budget_copy = {k: round(v, 2) for k, v in current_budget_state.items() if isinstance(v, (int, float))}

    if not budget_copy: # No numeric categories to pull from
        print("Error finding source funds: No numeric categories in current budget.")
        return None

    # Rule 1: Contingency Fund
    contingency_key = next((k for k in budget_copy if 'contingency' in k.lower()), None)
    if contingency_key:
        contingency_available = budget_copy.get(contingency_key, 0.0)
        if contingency_available > 0.005: # Check if > 0 after rounding
            pull_from_contingency = min(remaining_needed, contingency_available)
            if pull_from_contingency >= 0.01: # Only pull if it's at least 1 cent
                sources.append((contingency_key, pull_from_contingency))
                remaining_needed = round(remaining_needed - pull_from_contingency, 2)
                budget_copy[contingency_key] -= pull_from_contingency # Reduce available amount in copy

    # Rule 2: Categories explicitly marked low priority (e.g., containing '(Low Priority)')
    low_priority_keys = [k for k in budget_copy if '(low priority)' in k.lower() and k != contingency_key]
    # Sort by amount available, ascending - take from smallest low-prio first
    for low_key in sorted(low_priority_keys, key=lambda k: budget_copy.get(k, 0.0)):
         if remaining_needed < 0.01: break # Stop if requirement met
         available = budget_copy.get(low_key, 0.0)
         if available >= 0.01:
              pull_amount = min(remaining_needed, available)
              sources.append((low_key, pull_amount))
              remaining_needed = round(remaining_needed - pull_amount, 2)
              budget_copy[low_key] -= pull_amount


    # Rule 3: Fallback - Smallest non-zero, non-contingency, non-low-prio categories
    if remaining_needed >= 0.01:
        potential_sources = {
            k: v for k, v in budget_copy.items()
            if v >= 0.01 and k != contingency_key and k not in low_priority_keys
        }
        if potential_sources:
             # Sort by amount, ascending
             sorted_sources = sorted(potential_sources.items(), key=lambda item: item[1])
             for category, available in sorted_sources:
                 if remaining_needed < 0.01: break
                 pull_amount = min(remaining_needed, available)
                 sources.append((category, pull_amount))
                 remaining_needed = round(remaining_needed - pull_amount, 2)
                 # Don't need to update copy here, just identifying sources

    # Final Check
    if remaining_needed >= 0.01:
        print(f"Insufficient funds. Still need: {remaining_needed:.2f}")
        return None # Could not find enough funds
    else:
        # Clean up source list: combine multiple pulls from the same category if any happened
        final_sources = {}
        for cat, amt in sources:
            final_sources[cat] = round(final_sources.get(cat, 0.0) + amt, 2)
        # Filter out any sources where the amount became zero due to rounding
        return [(cat, amt) for cat, amt in final_sources.items() if amt >= 0.01]


def perform_reallocation(event_category, amount_needed, current_budget_state, log_list):
    """
    Performs reallocation. Takes state & log, returns updated state & log.
    Returns (new_budget_state, new_log_list, success_boolean)
    """
    new_budget = copy.deepcopy(current_budget_state)
    new_log = list(log_list)
    try:
        amount_needed_float = round(float(amount_needed), 2)
    except (ValueError, TypeError):
        new_log.append(f"Reallocation failed: Invalid amount '{amount_needed}'.")
        return new_budget, new_log, False

    if amount_needed_float <= 0:
         new_log.append(f"Reallocation request ignored: Amount needed (${amount_needed_float:.2f}) must be positive.")
         return new_budget, new_log, False

    clean_event_category = event_category.strip() if isinstance(event_category, str) else str(event_category)
    if not clean_event_category:
        new_log.append("Reallocation failed: Target category cannot be empty.")
        return new_budget, new_log, False


    new_log.append(f"--- Event Triggered: Requesting ${amount_needed_float:.2f} for '{clean_event_category}' ---")
    print(f"Attempting reallocation: ${amount_needed_float:.2f} for {clean_event_category}")

    # Ensure target category exists numerically, create/reset if needed
    current_target_value = new_budget.get(clean_event_category)
    if not isinstance(current_target_value, (int, float)):
         if clean_event_category not in new_budget:
             new_log.append(f"Note: Target category '{clean_event_category}' created.")
         else:
              new_log.append(f"Note: Target category '{clean_event_category}' existed but wasn't numeric. Resetting to 0.")
         new_budget[clean_event_category] = 0.0


    # Find sources
    source_details = find_source_funds(amount_needed_float, new_budget)

    if source_details is None:
        message = f"FAILED Reallocation: Insufficient funds in available sources to cover ${amount_needed_float:.2f} for '{clean_event_category}'."
        print(message)
        new_log.append(message)
        new_log.append(f"--- Reallocation Attempt Failed ---")
        return current_budget_state, new_log, False # Return ORIGINAL budget state on failure

    # Execute reallocation
    total_pulled = 0.0
    for source_category, pull_amount in source_details:
        pull_amount_rounded = round(pull_amount, 2)
        if source_category in new_budget and isinstance(new_budget[source_category], (int, float)):
            current_source_val = round(new_budget[source_category], 2)
            new_budget[source_category] = round(current_source_val - pull_amount_rounded, 2)
            new_log.append(f"Reallocating: Took ${pull_amount_rounded:.2f} from '{source_category}'. New balance: ${new_budget[source_category]:.2f}")
            total_pulled = round(total_pulled + pull_amount_rounded, 2)
        else:
            print(f"Warning: Source category '{source_category}' not found or not numeric during reallocation execution.")
            new_log.append(f"Warning: Issue pulling ${pull_amount_rounded:.2f} from '{source_category}'.")


    # Add funds to target - use the actual total pulled which might be slightly different due to rounding
    actual_amount_to_add = round(total_pulled, 2)
    # If total pulled doesn't quite match amount needed due to rounding/source limits, log it
    if abs(actual_amount_to_add - amount_needed_float) > 0.005:
         new_log.append(f"Note: Actual amount reallocated (${actual_amount_to_add:.2f}) differs slightly from requested amount (${amount_needed_float:.2f}) due to available funds/rounding.")

    current_target_val = round(new_budget.get(clean_event_category, 0.0), 2)
    new_budget[clean_event_category] = round(current_target_val + actual_amount_to_add, 2)

    new_log.append(f"Reallocating: Added ${actual_amount_to_add:.2f} to '{clean_event_category}'. New balance: ${new_budget[clean_event_category]:.2f}")
    new_log.append(f"--- Reallocation Complete for '{clean_event_category}' ---")
    print("Reallocation successful.")

    return new_budget, new_log, True