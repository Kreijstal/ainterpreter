import asyncio
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import tty
import pyte
from typing import Tuple, Optional, Callable, List

# --- Prompt Toolkit Imports ---
# Assuming AppState might be defined elsewhere or passed in type-hint only
# If AppState is defined in chat_repl.py, we might need to adjust imports later
# For now, let's assume it's available or we'll handle it.
# from chat_repl import AppState # Example if AppState is in chat_repl.py
from prompt_toolkit.application import get_app # Needed for _get_terminal_size
from prompt_toolkit.formatted_text import FormattedText

# Forward declaration for type hinting if AppState is in chat_repl.py
# This avoids a circular import if AppState is defined there.
if sys.version_info >= (3, 7):
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from chat_repl import AppState # Adjust if AppState is defined elsewhere
else:
    AppState = None # Placeholder for older Python versions


class TerminalManager:
    """Manages the embedded terminal session."""
    def __init__(self, state: 'AppState', loop: asyncio.AbstractEventLoop, update_ui_callback: Callable, add_system_message_callback: Callable, session_stopped_callback: Callable):
         self.state = state
         self.loop = loop
         self.update_ui_callback = update_ui_callback
         self.add_system_message_callback = add_system_message_callback
         self.session_stopped_callback = session_stopped_callback # Callback for when session fully stops
         self.master_fd: Optional[int] = None
         self.child_pid: Optional[int] = None
         
         # Terminal output tracking
         self.last_output_position = 0  # Tracks position of last read output
         self.full_output_buffer = bytearray()  # Stores all terminal output

         # Pyte terminal emulation components
         # Initialize with current terminal size
         initial_rows, initial_cols = self._get_terminal_size() # Get initial size #WRONG do not get the initizl size of the terminal, get the size of the right pane!!!
         #print(f"Original pyte screen with size: {initial_rows}x{initial_cols}") # Debug print
         initial_cols = max(initial_cols, 120)
         initial_rows = max(initial_rows, 24)
         self.pyte_screen = pyte.Screen(initial_cols, initial_rows)
         self.pyte_stream = pyte.ByteStream(self.pyte_screen)
         #do not print, do a system, debug message
         print(f"Initialized pyte screen with size: {initial_rows}x{initial_cols}") # Debug print

    def start_session(self):
        """Starts the bash session in a pty."""
        if self.child_pid is not None:
            print("Terminal session already running.") # Should not happen with proper state mgmt
            return

        try:
            pid, fd = pty.fork()
            if pid == 0:  # Child process
                # Set SHELL environment variable if needed, or let bash figure it out
                # os.environ['SHELL'] = '/bin/bash'
                # Set TERM so programs like vim/htop work correctly
                os.environ['TERM'] = 'xterm-256color'
                # Execute bash, replacing the child process
                # Use -i for interactive mode if desired, but be careful with init files
                argv = ['/bin/bash']
                os.execv(argv[0], argv)
            else:  # Parent process
                self.child_pid = pid
                self.master_fd = fd
                print(f"Started terminal session with PID: {self.child_pid}, FD: {self.master_fd}")

                # Set master_fd to non-blocking
                fl = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
                fcntl.fcntl(self.master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

                # Add reader to the event loop to read output asynchronously
                self.loop.add_reader(self.master_fd, self._read_terminal_output)

                # Handle terminal resizing
                self._set_initial_pty_size()
                signal.signal(signal.SIGWINCH, self._handle_resize)

                # Use the callback to add message to chat history
                self.add_system_message_callback("system", "Terminal session started.")

        except Exception as e:
             # Use the callback here too
            self.add_system_message_callback("system", f"Failed to start terminal session: {e}")
            self.stop_session() # Clean up if failed

    def stop_session(self):
        """Stops the bash session."""
        if self.master_fd is not None:
            self.loop.remove_reader(self.master_fd)
            try:
                os.close(self.master_fd)
            except OSError:
                pass # Ignore errors on close
            self.master_fd = None
            print("Closed master FD.")

        if self.child_pid is not None:
            try:
                # Try terminating gracefully first
                os.kill(self.child_pid, signal.SIGTERM)
                # Optionally wait with a timeout and then SIGKILL
                # _, status = os.waitpid(self.child_pid, os.WNOHANG) # Check if exited quickly
                # if not os.WIFEXITED(status) and not os.WIFSIGNALED(status):
                #    await asyncio.sleep(0.1) # Give it a moment
                #    os.kill(self.child_pid, signal.SIGKILL)
                print(f"Sent SIGTERM to PID: {self.child_pid}")
            except ProcessLookupError:
                print(f"Process {self.child_pid} already exited.")
            except Exception as e:
                print(f"Error stopping process {self.child_pid}: {e}")
            self.child_pid = None

        # Reset terminal state in UI
        self.state.terminal_output = FormattedText([])
        # Use the callback
        self.add_system_message_callback("system", "Terminal session stopped.")
        self.update_ui_callback()
        signal.signal(signal.SIGWINCH, signal.SIG_DFL) # Restore default handler
        # Call the final callback *after* all cleanup
        if self.session_stopped_callback:
            self.session_stopped_callback()


    def write_input(self, data: str):
        """Writes data (user input) to the terminal session."""
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, data.encode())
            except OSError as e:
                 self._add_system_message(f"Error writing to terminal: {e}") # Note: _add_system_message doesn't exist, should use callback

    def _read_terminal_output(self):
        """Callback function to read raw bytes from the pty and feed pyte."""
        if self.master_fd is None:
            return
        try:
            # Read raw bytes
            data = os.read(self.master_fd, 1024)
            if data:
                # Store all output in buffer
                self.full_output_buffer.extend(data)
                # Feed the raw bytes directly to the pyte stream processor
                self.pyte_stream.feed(data)
                # Trigger UI update
                self.update_ui_callback()
            else:
                # EOF means the child process likely exited
                print("EOF received from terminal FD. Stopping session.")
                self.stop_session()
        except OSError as e:
            # EIO often means the process exited
            print(f"Error reading from terminal FD (process likely exited): {e}. Stopping session.")
            self.stop_session()
        except Exception as e:
            print(f"Unexpected error reading terminal bytes: {e}")
            self.stop_session()


    def _get_terminal_size(self) -> Tuple[int, int]:
        """Gets the size of the controlling terminal, prioritizing prompt_toolkit."""
        rows, cols = 24, 100 # Default fallback
        try:
            # Try prompt_toolkit first - usually most accurate for the UI
            app = get_app()
            if app and app.output: # Check if app and output exist
                size = app.output.get_size()
                if size and size.rows > 0 and size.columns > 0:
                    return size.rows, size.columns
        except Exception:
            # Fallback if PTK app not ready or fails
            pass

        # Fall back to standard OS terminal size detection
        for fd in (sys.stdout.fileno(), sys.stdin.fileno(), sys.stderr.fileno()):
            try:
                # Note: os.get_terminal_size returns (columns, rows)
                os_cols, os_rows = os.get_terminal_size(fd)
                if os_rows > 0 and os_cols > 0:
                    return os_rows, os_cols # Return the first valid one found
            except OSError:
                continue
            except Exception: # Catch any other potential errors
                pass

        # Return default if all methods fail
        return rows, cols

    def _set_pty_size(self):
        """Sets the window size of the PTY."""
        if self.master_fd is None:
            return
        try:
            rows, cols = self._get_terminal_size()
            # Pack height, width, height_pixels, width_pixels (pixels often 0)
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            print(f"Set PTY size to {rows}x{cols}")
        except Exception as e:
            print(f"Error setting PTY size: {e}")

    def _set_initial_pty_size(self):
        # Call immediately after fork
        self._set_pty_size()

    def _handle_resize(self, signum, frame):
        """Signal handler for SIGWINCH."""
        print("SIGWINCH received.")
        self._set_pty_size()
        # Also need to tell prompt_toolkit to redraw
        try:
            app = get_app()
            if app:
                app.invalidate()
        except Exception:
            pass # App might not be running

    def get_formatted_terminal_output(self) -> FormattedText:
        """Renders the current state of the pyte screen as FormattedText."""
        lines = []
        cursor_pos = (self.pyte_screen.cursor.y, self.pyte_screen.cursor.x)
        for y, line in enumerate(self.pyte_screen.display):
            line_fragments = []
            current_style = ""
            # Ensure we iterate over the correct buffer structure
            if y in self.pyte_screen.buffer:
                for x, char_obj in enumerate(self.pyte_screen.buffer[y].values()):
                    # Style mapping with color conversion
                    style = ""
                    if char_obj.fg != 'default':
                        # Convert pyte color names to prompt_toolkit compatible names
                        fg_color = {
                            'brightblack': 'ansiblack', 'black': 'ansiblack',
                            'brightred': 'ansired', 'red': 'ansired',
                            'brightgreen': 'ansigreen', 'green': 'ansigreen',
                            'brightyellow': 'ansiyellow', 'yellow': 'ansiyellow',
                            'brightblue': 'ansiblue', 'blue': 'ansiblue',
                            'brightmagenta': 'ansimagenta', 'magenta': 'ansimagenta',
                            'brightcyan': 'ansicyan', 'cyan': 'ansicyan',
                            'brightwhite': 'ansiwhite', 'white': 'ansiwhite',
                            'default': 'default'
                        }.get(str(char_obj.fg), 'default') # Ensure fg is string
                        style += f"fg:{fg_color} "
                    if char_obj.bg != 'default':
                        # Same conversion for background colors
                        bg_color = {
                            'brightblack': 'ansiblack', 'black': 'ansiblack',
                            'brightred': 'ansired', 'red': 'ansired',
                            'brightgreen': 'ansigreen', 'green': 'ansigreen',
                            'brightyellow': 'ansiyellow', 'yellow': 'ansiyellow',
                            'brightblue': 'ansiblue', 'blue': 'ansiblue',
                            'brightmagenta': 'ansimagenta', 'magenta': 'ansimagenta',
                            'brightcyan': 'ansicyan', 'cyan': 'ansicyan',
                            'brightwhite': 'ansiwhite', 'white': 'ansiwhite',
                            'default': 'default'
                        }.get(str(char_obj.bg), 'default') # Ensure bg is string
                        style += f"bg:{bg_color} "
                    if char_obj.bold: style += "bold "
                    if char_obj.italics: style += "italic "
                    if char_obj.underscore: style += "underline "
                    if char_obj.reverse: style += "reverse "

                    # Handle cursor position
                    is_cursor = (not self.pyte_screen.cursor.hidden and
                               (y, x) == cursor_pos)
                    if is_cursor:
                        style += "reverse "  # Simple cursor representation

                    style = style.strip()

                    # Add fragment if style changed
                    if style != current_style and line_fragments:
                        lines.append((current_style, "".join(line_fragments)))
                        line_fragments = []
                        current_style = style

                    line_fragments.append(char_obj.data)

                # Add the remaining fragments for the line
                if line_fragments:
                    lines.append((current_style, "".join(line_fragments)))

            # Add newline between lines from pyte's display representation
            lines.append(("", "\n"))

        # Remove trailing newline if present
        if lines and lines[-1] == ("", "\n"):
            lines.pop()

        return FormattedText(lines)

    def get_full_terminal_output(self) -> str:
        """Returns the entire terminal output as a string."""
        return self.full_output_buffer.decode('utf-8', errors='replace')
    
    def get_new_terminal_output(self) -> str:
        """Returns only new terminal output since last call."""
        current_position = len(self.full_output_buffer)
        if current_position <= self.last_output_position:
            return ""
        
        new_data = self.full_output_buffer[self.last_output_position:current_position]
        self.last_output_position = current_position
        return new_data.decode('utf-8', errors='replace')
    
    def write_keystrokes(self, keystrokes: str):
        """Writes individual keystrokes to the terminal session."""
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, keystrokes.encode())
            except OSError as e:
                self.add_system_message_callback("system", f"Error writing keystrokes to terminal: {e}")

    # Removed the old _add_system_message method as we now use the callback