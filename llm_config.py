import json
import os
import getpass

CONFIG_FILE = "llm_config.json"

# Note: These models generally require the OPENROUTER_API_KEY environment variable.
# See https://openrouter.ai/docs#api-keys
PRESELECTED_MODELS = [
    "openrouter/google/gemini-2.5-pro-exp-03-25:free",
    "openrouter/deepseek/deepseek-chat-v3-0324:free",
    "openrouter/qwen/qwq-32b:free",
]

def load_config():
    """Loads configuration from the JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error reading configuration file {CONFIG_FILE}. Starting fresh.")
            return None
    return None

def save_config(config):
    """Saves configuration to the JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
    print(f"Configuration saved to {CONFIG_FILE}")

def get_user_config():
    """Prompts the user for LLM configuration details and saves it."""
    print("LLM configuration not found or invalid.")
    print("Please select an LLM model:")

    for i, model in enumerate(PRESELECTED_MODELS):
        print(f"{i + 1}. {model}")
    print(f"{len(PRESELECTED_MODELS) + 1}. Other (Specify custom model name)")

    model_name = None
    is_openai_compatible = True # Default assumption

    while True:
        try:
            choice = int(input(f"Enter choice (1-{len(PRESELECTED_MODELS) + 1}): "))
            if 1 <= choice <= len(PRESELECTED_MODELS):
                model_name = PRESELECTED_MODELS[choice - 1]
                is_openai_compatible = True # OpenRouter models are compatible
                break
            elif choice == len(PRESELECTED_MODELS) + 1:
                model_name = input("Enter the custom model name: ").strip()
                if not model_name:
                    print("Model name cannot be empty.")
                    continue

                # Basic check for compatibility assumption
                if not model_name.lower().startswith(("openrouter/", "openai/")):
                     # Ask about compatibility only if it's not obviously OpenRouter/OpenAI
                    while True:
                        compat_choice = input(f"Is '{model_name}' OpenAI API compatible? (yes/no): ").lower()
                        if compat_choice in ['yes', 'y']:
                            is_openai_compatible = True
                            break
                        elif compat_choice in ['no', 'n']:
                            is_openai_compatible = False
                            print("Note: Non-OpenAI compatible models might need specific setup.")
                            break
                        else:
                            print("Invalid input. Please enter 'yes' or 'no'.")
                else:
                    is_openai_compatible = True # Assume compatibility for OpenRouter/OpenAI
                break
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except EOFError:
            print("\nConfiguration cancelled.")
            return None # Indicate cancellation

    # Determine required API key based on model and prompt if needed
    api_key_value = None
    required_env_var = None
    if model_name:
        if model_name.startswith("openrouter/"):
            required_env_var = "OPENROUTER_API_KEY"
        elif model_name.startswith("openai/") or model_name in ("gpt-4", "gpt-3.5-turbo"):
            required_env_var = "OPENAI_API_KEY"
        # Add elif for other providers here if necessary

        if required_env_var:
            print(f"\nThis model requires the '{required_env_var}'.")
            try:
                api_key_value = getpass.getpass(f"Please enter your {required_env_var}: ")
            except EOFError:
                 print("\nAPI key entry cancelled.")
                 return None # Indicate cancellation
            except Exception as e: # Catch potential getpass issues
                 print(f"Error getting API key: {e}. Falling back to standard input.")
                 try:
                     api_key_value = input(f"Please enter your {required_env_var}: ")
                 except EOFError:
                     print("\nAPI key entry cancelled.")
                     return None # Indicate cancellation

            if not api_key_value:
                 print("Warning: API key not provided. API calls will likely fail.")


    # Store the configuration
    config = {
        "model": model_name,
        "is_openai_compatible": is_openai_compatible,
    }
    if required_env_var and api_key_value:
        config[required_env_var] = api_key_value

    save_config(config)
    return config