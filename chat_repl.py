import argparse
import asyncio
# Removed fcntl, pty, select, signal, struct, termios, tty, pyte (moved to terminal_manager)
import getpass
import llm_config
import litellm
import os
import subprocess # Keep subprocess if needed elsewhere, otherwise remove
import sys
from datetime import datetime
from typing import List, Tuple, Optional, Callable

# --- Prompt Toolkit Imports ---
from prompt_toolkit.application import Application # Removed get_app (moved to terminal_manager)
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import FormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    VSplit, HSplit, Window, ConditionalContainer, FormattedTextControl
)
# Removed GetTextProxy import as it's likely unused or unavailable
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import TextArea, Frame

# --- Custom Module Imports ---
from terminal_manager import TerminalManager # Import the refactored class

# Suppress LiteLLM informational messages
litellm.suppress_debug_info = True

# --- Constants ---
DEFAULT_TIMESTAMP_FORMAT = "%H:%M:%S"

# --- State Class ---
class AppState:
    """Holds the application's shared state."""
    def __init__(self):
        self.is_split: bool = False
        self.model_name: Optional[str] = None
        self.api_key: Optional[str] = None
        self.timestamp_mode: str = 'none'
        self.timestamp_format: str = DEFAULT_TIMESTAMP_FORMAT
        self.chat_history: FormattedText = FormattedText([])
        # Removed self.terminal_output as TerminalManager now manages its display
        self.chat_messages_for_api: List[dict] = [] # History for LiteLLM

# --- Manager Classes ---

class ChatManager:
    """Handles interactions with the LLM API."""
    def __init__(self, state: AppState, loop: asyncio.AbstractEventLoop, update_ui_callback: Callable):
        self.state = state
        self.loop = loop
        self.update_ui_callback = update_ui_callback # To update UI after response

    async def send_message(self, text: str):
        """Adds user message and triggers API call."""
        if not self.state.model_name:
            self._add_error_to_history("Model name not configured.")
            return

        user_message = {"role": "user", "content": text}
        self.state.chat_messages_for_api.append(user_message)
        self._add_message_to_history("user", text)

        # Add thinking indicator
        thinking_message = "[Thinking...]"
        self._add_message_to_history("assistant", thinking_message, temporary=True)

        try:
            # Run blocking API call in executor
            response = await self.loop.run_in_executor(
                None, # Use default executor
                litellm.completion, # Function to run
                self.state.model_name, # Arguments
                self.state.chat_messages_for_api
            )

            # Remove "Thinking..." before adding final response
            self._remove_last_message_from_history()

            if response and response.choices and response.choices[0].message and response.choices[0].message.content:
                assistant_response = response.choices[0].message.content.strip()
                self.state.chat_messages_for_api.append({"role": "assistant", "content": assistant_response})
                self._add_message_to_history("assistant", assistant_response)
            else:
                error_msg = f"Received empty/unexpected response: {response}"
                self._add_error_to_history(error_msg)
                # Optionally remove the last user message if the call failed significantly
                if self.state.chat_messages_for_api and self.state.chat_messages_for_api[-1]["role"] == "user":
                     self.state.chat_messages_for_api.pop()


        except Exception as e:
            # Remove "Thinking..." if an error occurred
            self._remove_last_message_from_history()
            error_msg = f"API call error: {e}"
            self._add_error_to_history(error_msg)
            # Remove the last user message as the call failed
            if self.state.chat_messages_for_api and self.state.chat_messages_for_api[-1]["role"] == "user":
                self.state.chat_messages_for_api.pop()

    def _add_message_to_history(self, role: str, text: str, temporary: bool = False):
        """Internal helper to update chat history state."""
        prefix = ""
        ts_format = self.state.timestamp_format
        if self.state.timestamp_mode in ['user', 'both'] and role == "user":
            prefix = f"[{datetime.now().strftime(ts_format)}] "
        elif self.state.timestamp_mode in ['llm', 'both'] and role == "assistant":
             prefix = f"[{datetime.now().strftime(ts_format)}] "
        elif role == "system":
             prefix = "[System] "


        role_display = "You: " if role == "user" else "LLM: " if role == "assistant" else ""
        new_line = ("", f"{prefix}{role_display}{text}\n")

        current_history = list(self.state.chat_history)
        current_history.append(new_line)
        self.state.chat_history = FormattedText(current_history)
        self.update_ui_callback() # Trigger UI refresh

    def _remove_last_message_from_history(self):
        """Removes the last message added to the chat history state."""
        current_history = list(self.state.chat_history)
        if current_history:
            current_history.pop()
            self.state.chat_history = FormattedText(current_history)
            # No UI update needed here, as it's usually followed by adding the real message

    def _add_error_to_history(self, error_msg: str):
        """Adds an error message to the chat history."""
        self._add_message_to_history("system", f"[Error: {error_msg}]")


# --- UIManager Class --- (TerminalManager class removed from here)
class UIManager:
    """Manages the prompt_toolkit UI."""
    def __init__(self, app: 'ChatApp', state: AppState, handle_input_callback: Callable, handle_key_callback: Callable):
        self.app = app
        self.state = state
        self.handle_input_callback = handle_input_callback # Called when Enter pressed in input
        self.handle_key_callback = handle_key_callback # Called for other key presses (for terminal)
        self._build_layout()
        self._build_key_bindings()
        self.pt_app: Optional[Application] = None # Initialized later

    def _build_layout(self):
        # --- Widgets ---
        # Chat History
        # Use lambda to get latest state value
        chat_history_control = FormattedTextControl(lambda: self.state.chat_history, focusable=True, show_cursor=False)
        self.chat_history_window = Window(content=chat_history_control, wrap_lines=True, always_hide_cursor=True)

        # Input Area
        self.repl_input_area = TextArea(
            accept_handler=self._on_input_accept, # Internal handler calls callback
            multiline=False, wrap_lines=False, prompt=">>> ", height=1,
            style="class:repl-input",
        )

        # Left Panel (Chat)
        left_panel = Frame(HSplit([
            self.chat_history_window,
            Window(height=1, char='-', style='class:separator'),
            self.repl_input_area,
        ], padding=0), title="Chat")

        # Right Panel (Terminal) - Conditional
        # Get formatted text from TerminalManager's pyte screen
        terminal_control = FormattedTextControl(
            # Reference the imported terminal_manager instance
            lambda: self.app.terminal_manager.get_formatted_terminal_output(),
            focusable=True,
            show_cursor=False  # We handle cursor via pyte
        )
        self.terminal_window = Window(
            content=terminal_control,
            wrap_lines=False,
            dont_extend_width=False,
            width=None,
            style='class:terminal',
            get_line_prefix=lambda line_no, wrap_count: [('', '')]
        )

        conditional_right_panel = ConditionalContainer(
            content=Frame(self.terminal_window, title="Terminal"),
            filter=Condition(lambda: self.state.is_split)
        )

        # --- Main Layout ---
        body = VSplit([
            left_panel,
            conditional_right_panel,
        ], padding=1, padding_char="|", padding_style="fg:#888888")

        self.layout = Layout(body, focused_element=self.repl_input_area)

    def _build_key_bindings(self):
        self.key_bindings = KeyBindings()
        kb = self.key_bindings

        @kb.add("c-c", eager=True)
        @kb.add("c-d", eager=True)
        def _(event):
            event.app.exit()

        @kb.add('up', filter=has_focus(self.chat_history_window))
        def _(event): event.app.layout.current_window.scroll_backward()
        @kb.add('down', filter=has_focus(self.chat_history_window))
        def _(event): event.app.layout.current_window.scroll_forward()
        @kb.add('pageup', filter=has_focus(self.chat_history_window))
        def _(event): event.app.layout.current_window.scroll_page_up()
        @kb.add('pagedown', filter=has_focus(self.chat_history_window))
        def _(event): event.app.layout.current_window.scroll_page_down()

        # Focus switching
        @kb.add('tab')
        def _(event): event.app.layout.focus_next()
        @kb.add('s-tab')
        def _(event): event.app.layout.focus_previous()

        # Redraw
        @kb.add('c-l', eager=True)
        def _(event):
            """
            Handle Ctrl+L: Clear the screen and redraw.
            """
            #event.app.output.erase_screen()
            event.app.renderer.reset() # More drastic reset
            #event.app.invalidate()

        # --- Terminal Input Handling ---
        # Capture *any* key press when the terminal window has focus
        @kb.add('<any>', filter=has_focus(self.terminal_window))
        def _(event):
            # Forward the raw key press data to the main app handler
            self.handle_key_callback(event.key_sequence[0].data)
            event.cli.current_buffer.reset() # Prevent key from being inserted in UI buffer


    def _on_input_accept(self, buffer: Buffer):
        """Internal handler for REPL input submission."""
        text = buffer.text
        buffer.reset()
        # Pass the submitted text to the main application logic
        self.handle_input_callback(text)

    def get_application(self) -> Application:
        """Creates and returns the prompt_toolkit Application instance."""
        if not self.pt_app:
             self.pt_app = Application(
                layout=self.layout,
                key_bindings=self.key_bindings,
                full_screen=True,
                mouse_support=True,
                style=None # Add custom styling later if needed
            )
        return self.pt_app

    def force_ui_update(self):
        """Forces a redraw of the UI."""
        if self.pt_app and self.pt_app.is_running:
            self.pt_app.invalidate()


# --- Main Application Class ---

class ChatApp:
    """Orchestrates the chat application components."""
    def __init__(self):
        self.state = AppState()
        self.loop = asyncio.get_event_loop()
        # Pass UI update callback and system message callback to managers
        self.chat_manager = ChatManager(self.state, self.loop, self.force_ui_update)
        # Pass the ChatManager's method as the callback for system messages
        # Pass own method for session stopped callback
        # Initialize the imported TerminalManager
        self.terminal_manager = TerminalManager(
            self.state,
            self.loop,
            self.force_ui_update,
            self.chat_manager._add_message_to_history,
            self._on_terminal_session_stopped # Pass the new callback handler
        )
        # Pass input/key handlers to UI manager along with app reference
        self.ui_manager = UIManager(self, self.state, self.handle_input, self.handle_key_press)
        self.pt_app = self.ui_manager.get_application()

    def force_ui_update(self):
        """Callback for managers to request UI redraw."""
        self.ui_manager.force_ui_update()

    def handle_input(self, text: str):
        """Handles text submitted from the REPL input."""
        command = text.strip()
        if not command:
            return

        if command.lower() == "/split":
            if not self.state.is_split:
                self.state.is_split = True
                self.terminal_manager.start_session()
                self.force_ui_update()
        elif command.lower() == "/unsplit":
            if self.state.is_split:
                self.terminal_manager.stop_session()
        elif command.lower() == "/get_output":
            if self.state.is_split:
                output = self.terminal_manager.get_new_terminal_output()
                if output:
                    self.chat_manager._add_message_to_history("system", f"Terminal output:\n{output}")
                else:
                    self.chat_manager._add_message_to_history("system", "No new terminal output")
            else:
                self.chat_manager._add_message_to_history("system", "No active terminal session")
        elif command.lower() == "/get_full_output":
            if self.state.is_split:
                output = self.terminal_manager.get_full_terminal_output()
                self.chat_manager._add_message_to_history("system", f"Full terminal output:\n{output}")
            else:
                self.chat_manager._add_message_to_history("system", "No active terminal session")
        elif command.lower().startswith("/type "):
            if self.state.is_split:
                keystrokes = command[6:]  # Get text after "/type "
                # Handle special key sequences
                keystrokes = keystrokes.replace("\\n", "\n").replace("\\t", "\t").replace("\\e", "\x1b")
                keystrokes = keystrokes.replace("\\[200~", "\x1b[200~").replace("\\[201~", "\x1b[201~")
                self.terminal_manager.write_keystrokes(keystrokes)
                display_text = (keystrokes
                    .replace("\n", "\\n")
                    .replace("\t", "\\t")
                    .replace("\x1b", "\\e")
                    .replace("\x1b[200~", "\\[200~")
                    .replace("\x1b[201~", "\\[201~"))
                self.chat_manager._add_message_to_history("system", f"Sent keystrokes: {display_text}")
            else:
                self.chat_manager._add_message_to_history("system", "No active terminal session")
        elif command.lower() in ['quit', 'exit']:
            self.pt_app.exit()
        elif command.startswith('/'):
             self.chat_manager._add_message_to_history("system", f"Unknown command: {command}")
        else:
            # Process as chat input
            asyncio.create_task(self.chat_manager.send_message(command))

    def handle_key_press(self, key_data: str):
        """Handles raw key presses, intended for the terminal."""
        if self.state.is_split:
            self.terminal_manager.write_input(key_data)

    def _on_terminal_session_stopped(self):
        """Callback executed by TerminalManager when the session ends."""
        if self.state.is_split: # Only act if we thought we were split
            self.state.is_split = False
            # Ensure focus returns to the input area
            self.loop.call_soon(lambda: self.pt_app.layout.focus(self.ui_manager.repl_input_area))

            # Schedule the UI update slightly later to allow layout recalculation
            self.loop.call_later(0.01, self.force_ui_update)
            self.ui_manager.pt_app.renderer.reset() # Otherwise it looks ugly

    async def run_async(self):
        """Runs the prompt_toolkit application asynchronously."""
        # Perform initial setup (config, API key) before starting UI
        if not self._initial_setup():
            return # Exit if setup fails

        # Add initial message after setup
        self.chat_manager._add_message_to_history("system", f"Chatting with {self.state.model_name}. /split, /unsplit, quit, exit. Ctrl+C/D to exit.")

        await self.pt_app.run_async()

    def run(self):
        """Synchronous entry point."""
        try:
            self.loop.run_until_complete(self.run_async())
        except Exception as e:
            print(f"\nApplication exited with error: {e}")
        finally:
            print("\nCleaning up...")
            # Ensure terminal session is stopped on exit
            if self.state.is_split:
                self.terminal_manager.stop_session()
            print("Application has exited.")

    def _initial_setup(self) -> bool:
        """Loads config, parses args, gets API key. Returns True on success."""
        parser = argparse.ArgumentParser(description="Chat with an LLM (Refactored UI).")
        parser.add_argument("--timestamp", choices=['none', 'user', 'llm', 'both'], default='none', help="Show timestamps.")
        parser.add_argument("--timestamp-format", default=DEFAULT_TIMESTAMP_FORMAT, help="Timestamp format.")
        args = parser.parse_args()
        self.state.timestamp_mode = args.timestamp
        self.state.timestamp_format = args.timestamp_format

        print("Loading configuration...")
        config = llm_config.load_config()
        if config is None:
            config = llm_config.get_user_config()
            if config is None:
                return False
            print("Please ensure 'llm_config.json' is present and configured.")
            return False

        model_name_local = config.get("model")
        if not model_name_local:
            print("Error: Model name not found in configuration.")
            return False
        self.state.model_name = model_name_local
        print(f"Using model: {self.state.model_name}")

        required_env_var = None
        if self.state.model_name.startswith("openrouter/"): required_env_var = "OPENROUTER_API_KEY"
        elif self.state.model_name.startswith("openai/") or self.state.model_name in ("gpt-4", "gpt-3.5-turbo"): required_env_var = "OPENAI_API_KEY"

        if required_env_var:
            print(f"Checking/getting API key for {required_env_var}...")
            api_key = self._get_api_key_interactive(config, required_env_var)
            if not api_key:
                 print("API key acquisition failed. Exiting.")
                 return False
            self.state.api_key = api_key
            print("API key ready.")
        else:
            print("No specific API key needed based on model name prefix.")

        return True

    def _get_api_key_interactive(self, config, required_env_var):
        """Gets API key interactively (used during initial setup)."""
        # This is kept separate as it uses print/input before PTK takes over
        api_key = config.get(required_env_var) or os.getenv(required_env_var)
        if not api_key:
            print(f"\nAPI key '{required_env_var}' not found in config or environment.")
            try:
                api_key = getpass.getpass(f"Please enter your {required_env_var}: ")
            except EOFError:
                print("\nOperation cancelled.")
                return None
            except Exception as e:
                print(f"Error getting API key: {e}. Falling back to standard input.")
                try:
                    api_key = input(f"Please enter your {required_env_var}: ")
                except EOFError:
                    print("\nAPI key entry cancelled.")
                    return None
            if not api_key:
                print("API key not provided.")
                return None
            else:
                # Save back to config if entered interactively
                config[required_env_var] = api_key
                llm_config.save_config(config)

        # Set in environment for litellm for this session
        os.environ[required_env_var] = api_key
        return api_key


# --- Main Execution ---
if __name__ == "__main__":
    app = ChatApp()
    app.run()