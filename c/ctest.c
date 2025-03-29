#include <ncurses.h>
#include <string.h>

int main() {
    // Initialize ncurses
    initscr();
    cbreak();
    noecho();
    keypad(stdscr, TRUE);

    // Get screen dimensions
    int max_y, max_x;
    getmaxyx(stdscr, max_y, max_x);

    // Debug: Print dimensions to stdscr on row 0
    mvprintw(0, 0, "Screen: %d rows, %d cols", max_y, max_x);

    // Create two windows: left and right, starting at row 1 to leave room for debug
    WINDOW *left = newwin(max_y - 1, max_x / 2, 1, 0);         // Left half, height reduced by 1
    WINDOW *right = newwin(max_y - 1, max_x / 2, 1, max_x / 2); // Right half, height reduced by 1

    // Check if windows were created
    if (left == NULL || right == NULL) {
        endwin();
        printf("Failed to create windows\n");
        return 1;
    }

    // Debug: Add test text to each window
    mvwprintw(left, 0, 0, "LEFT TEST");   // Row 0 in subwindow (row 1 on screen)
    mvwprintw(right, 0, 0, "RIGHT TEST");

    // Lorem Ipsum text
    const char *lorem = "Lorem ipsum dolor sit amet.";

    // Fill left pane
    int left_y = 1; // Start below test text
    while (left_y < max_y - 1) {
        mvwprintw(left, left_y, 0, "%s", lorem);
        left_y++;
    }

    // Fill right pane
    int right_y = 1; // Start below test text
    while (right_y < max_y - 1) {
        mvwprintw(right, right_y, 0, "%s", lorem);
        right_y++;
    }

    // Draw vertical line between panes on stdscr, starting at row 1
    for (int y = 1; y < max_y; y++) {
        mvaddch(y, max_x / 2 - 1, ACS_VLINE);
    }

    // Refresh everything: stdscr first, then subwindows
    refresh();       // Show debug text and vertical line on stdscr
    wrefresh(left);  // Update left pane
    wrefresh(right); // Update right pane

    // Wait for input
    getch();

    // Clean up
    delwin(left);
    delwin(right);
    endwin();

    return 0;
}