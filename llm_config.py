# llm_config.py
import json
import os
import getpass

CONFIG_FILE = "llm_config.json"

# Note: These models generally require the OPENROUTER_API_KEY environment variable.
# See https://openrouter.ai/docs#api-keys
# Added more free/low-cost options for variety
PRESELECTED_MODELS = [
    "openrouter/google/gemini-2.5-pro-exp-03-25:free",
    "openrouter/deepseek/deepseek-chat-v3-0324:free",
    "openrouter/qwen/qwq-32b:free",
    "openrouter/google/gemini-flash-1.5:free",
    "openrouter/mistralai/mistral-7b-instruct:free",
    "openrouter/meta-llama/llama-3-8b-instruct:free",
    "openrouter/qwen/qwen-7b-chat:free",
    # Add other models users might want easy access to
    "openai/gpt-3.5-turbo", # Requires OPENAI_API_KEY
    "deepseek/deepseek-chat", # Requires DEEPSEEK_API_KEY
    ]

def load_config():
    """Loads configuration from the JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
                # Basic validation (optional but good)
                if isinstance(config_data, dict) and config_data.get("model"):
                    return config_data
                else:
                    print(f"Warning: Configuration file {CONFIG_FILE} seems invalid. Ignoring.")
                    return None
        except json.JSONDecodeError:
            print(f"Error reading configuration file {CONFIG_FILE}. Starting fresh.")
            return None
        except Exception as e:
            print(f"Error loading config file {CONFIG_FILE}: {e}. Starting fresh.")
            return None
    return None

def save_config(config):
    """Saves configuration to the JSON file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        print(f"Error saving configuration to {CONFIG_FILE}: {e}")


def get_user_config():
    """
    Prompts the user for LLM configuration details using standard input/output
    and saves it. Designed to be called when the TUI is suspended.
    Returns the new configuration dictionary or None if cancelled.
    """
    print("\n--- LLM Configuration ---")
    print("Please select an LLM model:")

    # Dynamically create the list including the "Other" option
    display_models = PRESELECTED_MODELS + ["Other (Specify custom model name)"]

    for i, model_option in enumerate(display_models):
        print(f"{i + 1}. {model_option}")

    model_name = None
    # Default assumption, can be changed for "Other"
    # is_openai_compatible = True # This isn't used elsewhere anymore, can remove if truly unused

    while True:
        try:
            choice_str = input(f"Enter choice (1-{len(display_models)}): ")
            if not choice_str: # Handle empty input
                print("Invalid choice. Please enter a number.")
                continue
            choice = int(choice_str)

            if 1 <= choice <= len(PRESELECTED_MODELS):
                model_name = PRESELECTED_MODELS[choice - 1]
                break
            elif choice == len(display_models): # Corresponds to "Other"
                while True: # Loop for custom model name input
                     model_name_input = input("Enter the custom model name (e.g., 'provider/model-name'): ").strip()
                     if model_name_input:
                         model_name = model_name_input
                         break
                     else:
                         print("Model name cannot be empty.")
                # We don't need to ask about OpenAI compatibility here anymore,
                # as the main app determines the key based on prefix.
                break # Exit the outer loop once custom name is entered
            else:
                print(f"Invalid choice. Please enter a number between 1 and {len(display_models)}.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except EOFError:
            print("\nConfiguration cancelled.")
            return None # Indicate cancellation

    # --- API Key Handling ---
    api_key_value = None
    required_env_var = None
    prompt_for_key = False

    # Determine if a specific key is generally needed based on prefix
    if model_name:
        if model_name.startswith("openrouter/"):
            required_env_var = "OPENROUTER_API_KEY"
        elif model_name.startswith("openai/") or model_name in ("gpt-4", "gpt-3.5-turbo"): # Handle common non-prefixed names
            required_env_var = "OPENAI_API_KEY"
        elif model_name.startswith("deepseek/"):
            required_env_var = "DEEPSEEK_API_KEY"
        elif model_name.startswith("anthropic/"):
             required_env_var = "ANTHROPIC_API_KEY"
        elif model_name.startswith("groq/"):
             required_env_var = "GROQ_API_KEY"
        # Add elif for other providers here if necessary

        # Check if the key is already set in the environment or current config
        current_config = load_config() or {}
        if required_env_var and not (os.getenv(required_env_var) or current_config.get(required_env_var)):
            prompt_for_key = True
            print(f"\nModel '{model_name}' typically requires the '{required_env_var}'.")
            print("It's not found in your environment or saved configuration.")

    # Prompt for the key only if determined necessary
    if prompt_for_key:
        try:
            # Use getpass for better security if possible
            api_key_value = getpass.getpass(f"Please enter your {required_env_var} (or press Enter to skip): ")
        except EOFError:
             print("\nAPI key entry cancelled.")
             # Don't cancel the whole config, just skip the key entry
             api_key_value = None
        except Exception as e: # Catch potential getpass issues (e.g., in non-terminal envs)
             print(f"Warning: Secure input failed ({e}). Falling back to standard input (API key will be visible).")
             try:
                 api_key_value = input(f"Please enter your {required_env_var} (or press Enter to skip): ")
             except EOFError:
                 print("\nAPI key entry cancelled.")
                 api_key_value = None

        if not api_key_value:
             print("API key entry skipped. API calls for this model might fail if the key is required.")
             api_key_value = None # Ensure it's None if skipped

    # --- Prepare and Save Configuration ---
    # Load existing config to preserve all keys
    new_config = load_config() or {}
    new_config["model"] = model_name

    # Add the API key to the config if it was entered in this session
    if required_env_var and api_key_value:
        new_config[required_env_var] = api_key_value

    save_config(new_config)

    print("--- Configuration Complete ---")
    return new_config # Return the config that was just saved/entered