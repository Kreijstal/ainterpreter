import argparse
import os
import getpass
from datetime import datetime
from litellm import completion
import llm_config # Import the configuration module

def get_api_key(config, required_env_var):
    """Gets the API key from config, environment, or prompts the user."""
    api_key = config.get(required_env_var)

    if not api_key:
        api_key = os.getenv(required_env_var)

    if not api_key:
        print(f"\nAPI key '{required_env_var}' not found in config or environment.")
        try:
            api_key = getpass.getpass(f"Please enter your {required_env_var}: ")
        except EOFError:
            print("\nOperation cancelled by user.")
            return None # Indicate cancellation
        except Exception as e:
            print(f"Error getting API key: {e}. Falling back to standard input.")
            try:
                api_key = input(f"Please enter your {required_env_var}: ")
            except EOFError:
                print("\nAPI key entry cancelled.")
                return None # Indicate cancellation

        if not api_key:
            print("API key not provided. Cannot proceed.")
            return None
        else:
            # Save the newly entered key back to the config file
            config[required_env_var] = api_key
            llm_config.save_config(config) # Use the save function from the module

    # Set the API key in the environment for LiteLLM for this session
    os.environ[required_env_var] = api_key
    return api_key


def start_chat_repl(model_name, timestamp_mode, timestamp_format):
    """Starts the interactive Read-Eval-Print Loop for chatting with the LLM."""
    print(f"\nStarting chat with {model_name}. Type 'quit' or 'exit' to end.")
    print("-" * 30)

    messages = [] # Start with empty history

    while True:
        # --- Prepare Prefix (Timestamp + Role) ---
        user_prefix = "You: "
        if timestamp_mode in ['user', 'both']:
            user_prefix = f"[{datetime.now().strftime(timestamp_format)}] {user_prefix}"

        # --- Get User Input ---
        try:
            user_input = input(user_prefix)
        except EOFError: # Handle Ctrl+D
            print("\nExiting.")
            break
        except KeyboardInterrupt: # Handle Ctrl+C
             print("\nExiting.")
             break


        if user_input.lower() in ['quit', 'exit']:
            print("Exiting chat.")
            break

        if not user_input: # Skip empty input
            continue

        # --- Add User Message to History ---
        messages.append({"role": "user", "content": user_input})

        # --- Call LiteLLM ---
        try:
            print("LLM: Thinking...", end='\r') # Indicate activity
            response = completion(model=model_name, messages=messages)

            # --- Process Response ---
            if response and response.choices and response.choices[0].message and response.choices[0].message.content:
                assistant_response = response.choices[0].message.content.strip()
                # Add assistant response to history
                messages.append({"role": "assistant", "content": assistant_response})

                # --- Prepare Prefix (Timestamp + Role) ---
                llm_prefix = "LLM: "
                if timestamp_mode in ['llm', 'both']:
                     llm_prefix = f"[{datetime.now().strftime(timestamp_format)}] {llm_prefix}"

                print(f"{llm_prefix}{assistant_response}      ") # Print response (overwrite thinking indicator)

            else:
                print("LLM: Received an empty or unexpected response.")
                print(f"Raw response: {response}")
                # Optionally remove the last user message if the call failed significantly
                # messages.pop()

        except Exception as e:
            print(f"\nError during API call: {e}")
            print("Please check your API key, model name, and connection.")
            # Remove the last user message as the call failed
            if messages and messages[-1]["role"] == "user":
                messages.pop()


def main():
    """Parses arguments, loads config, sets up API key, and starts the chat REPL."""
    parser = argparse.ArgumentParser(description="Chat with an LLM via LiteLLM.")
    parser.add_argument(
        "--timestamp",
        choices=['none', 'user', 'llm', 'both'],
        default='none',
        help="Show timestamps inline with messages (default: none)."
    )
    parser.add_argument(
        "--timestamp-format",
        default="%H:%M:%S", # Default to simpler time format for inline
        help="Format string for timestamps (default: '%%H:%%M:%%S'). See Python's strftime."
    )
    args = parser.parse_args()

    # --- Configuration Handling ---
    config = llm_config.load_config()
    if config is None:
        config = llm_config.get_user_config()
        if config is None: # Handle cancellation during initial config
             print("Configuration setup cancelled. Exiting.")
             return

    model_name = config.get("model")
    if not model_name:
        print("Error: Model name not found in configuration.")
        # Optionally prompt again or exit
        return

    print(f"\nUsing model: {model_name}")

    # --- API Key Handling ---
    required_env_var = None
    if model_name.startswith("openrouter/"):
        required_env_var = "OPENROUTER_API_KEY"
    elif model_name.startswith("openai/") or model_name in ("gpt-4", "gpt-3.5-turbo"):
        required_env_var = "OPENAI_API_KEY"
    # Add elif blocks for other providers if needed

    if required_env_var:
        api_key = get_api_key(config, required_env_var)
        if not api_key:
             # Error message printed within get_api_key
             return
    # --- End API Key Handling ---

    # Start the REPL
    start_chat_repl(model_name, args.timestamp, args.timestamp_format)


if __name__ == "__main__":
    main()