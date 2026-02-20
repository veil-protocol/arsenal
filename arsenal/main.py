#!/usr/bin/env python3
"""
arsenal - Native terminal cheat launcher
Simple, bulletproof curses TUI that works everywhere.
"""
import curses
import os
import re
import json
import subprocess
import sys
from pathlib import Path

# ============================================================================
# CONFIG
# ============================================================================

# Default cheat paths (combined into "default" vault)
DEFAULT_CHEAT_PATHS = [
    Path.home() / ".cheats",
    Path.home() / ".local/share/uv/tools/aliasr/lib/python3.14/site-packages/aliasr/data/cheats",
    Path("/opt/my-resources/setup/arsenal-cheats"),
]

GLOBALS_FILE = Path.home() / ".arsenal.json"
VAULTS_FILE = Path.home() / ".arsenal-vaults.json"

def load_vaults():
    """Load vault configurations. Returns dict of {name: [paths]}."""
    vaults = {"default": DEFAULT_CHEAT_PATHS}

    # Load custom vaults from config
    if VAULTS_FILE.exists():
        try:
            with open(VAULTS_FILE) as f:
                custom = json.load(f)
                for name, paths in custom.items():
                    vaults[name] = [Path(p) for p in paths]
        except:
            pass

    # Auto-discover playbook directories
    playbook_dirs = [
        Path.home() / ".arsenal-playbooks",
        Path("/opt/playbooks"),
    ]
    for pdir in playbook_dirs:
        if pdir.exists():
            for subdir in pdir.iterdir():
                if subdir.is_dir() and not subdir.name.startswith("."):
                    vaults[subdir.name] = [subdir]

    return vaults

def save_vaults(vaults):
    """Save custom vaults (excluding default and auto-discovered)."""
    # Only save custom vaults, not default or auto-discovered
    custom = {}
    for name, paths in vaults.items():
        if name != "default":
            custom[name] = [str(p) for p in paths]
    with open(VAULTS_FILE, "w") as f:
        json.dump(custom, f, indent=2)

# ============================================================================
# CHEAT PARSING
# ============================================================================

def get_tool_name(cmd):
    """Extract tool name from command (first word, ignoring env vars and sudo)."""
    # Split on newlines, take first line
    first_line = cmd.strip().split("\n")[0].strip()

    # Skip common prefixes
    words = first_line.split()
    skip = {"sudo", "env", "time", "nice", "nohup", "strace", "ltrace"}

    for word in words:
        # Skip env var assignments (FOO=bar)
        if "=" in word:
            continue
        # Skip common prefixes
        if word.lower() in skip:
            continue
        # Found the tool - clean it up
        tool = word.split("/")[-1]  # Remove path
        return tool.lower()

    return "other"

def build_tool_tree(cheats):
    """Group cheats by tool name, returns {tool: [cheats]} and sorted tool list."""
    tree = {}
    for c in cheats:
        tool = get_tool_name(c["cmd"])
        if tool not in tree:
            tree[tool] = []
        tree[tool].append(c)

    # Sort tools alphabetically, but put "other" last
    tools = sorted([t for t in tree.keys() if t != "other"])
    if "other" in tree:
        tools.append("other")

    return tree, tools

def load_cheats(paths=None):
    """Load all cheats from markdown files and build tag index."""
    if paths is None:
        paths = DEFAULT_CHEAT_PATHS

    cheats = []

    for base in paths:
        if not base.exists():
            continue
        for md in base.rglob("*.md"):
            if md.name.lower() == "readme.md":
                continue
            try:
                cheats.extend(parse_md(md))
            except:
                pass

    # Build tag index
    tag_to_cheats = {"all": cheats}
    for c in cheats:
        for tag in c["tags"]:
            if tag not in tag_to_cheats:
                tag_to_cheats[tag] = []
            tag_to_cheats[tag].append(c)

    # Sort tags: "all" first, then alphabetically
    tags = ["all"] + sorted([t for t in tag_to_cheats.keys() if t != "all"])

    return cheats, tag_to_cheats, tags

def parse_md(path):
    """Parse markdown file for cheats."""
    cheats = []
    text = path.read_text(errors="ignore")

    title = None
    tags = []
    in_code = False
    code_lines = []

    for line in text.split("\n"):
        stripped = line.strip()

        # Code fence toggle
        if stripped.startswith("```") or stripped.startswith("~~~"):
            if in_code:
                # End of code block - save cheat
                if title and code_lines:
                    cmd = "\n".join(code_lines).strip()
                    if cmd:
                        cheats.append({
                            "title": title,
                            "cmd": cmd,
                            "tags": tuple(tags),
                            "path": str(path),
                        })
                code_lines = []
                in_code = False
                title = None
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        # H2 = command title
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            continue

        # H1 = reset
        if stripped.startswith("# ") and not stripped.startswith("## "):
            tags = []
            title = None
            continue

        # Tags
        if stripped.startswith("#") and "/" in stripped:
            for match in re.findall(r"#([\w/-]+)", stripped):
                tags.append(match.lower())

    return cheats

# ============================================================================
# GLOBALS
# ============================================================================

def extract_params_from_cheats(cheats):
    """Extract all unique parameters from loaded cheats."""
    params = set()
    for c in cheats:
        for m in re.findall(r"<([^>|]+)", c["cmd"]):
            # Skip things that look like paths or garbage
            if "/" not in m and len(m) < 30 and m.replace("_", "").isalnum():
                params.add(m)
    return sorted(params)

def load_globals(cheats=None):
    """Load globals from file, auto-adding any new params from cheats."""
    globals_dict = {}

    # Load saved globals
    if GLOBALS_FILE.exists():
        try:
            globals_dict = json.loads(GLOBALS_FILE.read_text())
        except:
            pass

    # Add any params from cheats that aren't already globals
    if cheats:
        for param in extract_params_from_cheats(cheats):
            if param not in globals_dict:
                globals_dict[param] = ""

    return globals_dict

def load_globals_simple():
    """Load globals without cheat scanning (for scan command)."""
    if GLOBALS_FILE.exists():
        try:
            return json.loads(GLOBALS_FILE.read_text())
        except:
            pass
    return {}

def save_globals(g):
    """Save globals to file."""
    GLOBALS_FILE.write_text(json.dumps(g, indent=2))

# ============================================================================
# HELPERS
# ============================================================================

def fill_params(cmd, globals_dict):
    """Fill in parameters from globals."""
    def replace(m):
        key = m.group(1).split("|")[0]
        return globals_dict.get(key, m.group(0))
    return re.sub(r"<([^>]+)>", replace, cmd)

def get_params(cmd):
    """Extract parameter names from command."""
    params = []
    for m in re.finditer(r"<([^>|]+)", cmd):
        p = m.group(1)
        if p not in params:
            params.append(p)
    return params

def wrap_text(text, width):
    """Wrap text to fit within width, preserving existing newlines."""
    result = []
    for line in text.split("\n"):
        if len(line) <= width:
            result.append(line)
        else:
            # Wrap long lines
            while len(line) > width:
                # Try to break at space
                break_at = line.rfind(" ", 0, width)
                if break_at <= 0:
                    break_at = width
                result.append(line[:break_at])
                line = line[break_at:].lstrip()
            if line:
                result.append(line)
    return result

def copy_cmd(text):
    """Copy to clipboard."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        else:
            subprocess.run(["xclip", "-sel", "clip"], input=text.encode(), check=True)
        return True
    except:
        return False

def in_tmux():
    """Check if we're running inside tmux."""
    # Method 1: TMUX env var
    if os.environ.get("TMUX"):
        return True
    # Method 2: TERM contains tmux/screen
    term = os.environ.get("TERM", "")
    if "tmux" in term or "screen" in term:
        return True
    # Method 3: Check if tmux server is running and we have a pane
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            capture_output=True, timeout=1
        )
        return result.returncode == 0 and result.stdout.strip()
    except:
        return False

def get_tmux_target():
    """Get the best tmux pane to send commands to (not the current one).

    Uses tmux relative targeting:
    - {last} = the previously active pane
    - :.+ = next pane in current window (wraps around)
    """
    # Check if there are multiple panes
    try:
        result = subprocess.run(
            ["tmux", "list-panes"],
            capture_output=True, text=True, timeout=1
        )
        pane_count = len(result.stdout.strip().split("\n"))
        if pane_count > 1:
            # Use "next pane" - more reliable than tracking pane IDs
            return ":.+"
    except:
        pass
    # Fallback to last pane
    return "{last}"

def send_tmux(text, execute=False):
    """Send to tmux pane (targets another pane, not current)."""
    if not in_tmux():
        return False
    try:
        target = get_tmux_target()

        # For multi-line commands, send each line separately
        lines = text.split("\n")
        for i, line in enumerate(lines):
            # Use -l for literal, -t for target pane
            subprocess.run(["tmux", "send-keys", "-t", target, "-l", line], check=True)
            # Add newline between lines (but not after last if not executing)
            if i < len(lines) - 1:
                subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=True)

        if execute:
            subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=True)
        return True
    except Exception:
        return False

# ============================================================================
# TUI
# ============================================================================

def safe_addstr(win, y, x, text, attr=0):
    """Safely add string, handling boundaries."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    text = str(text)[:w - x - 1]
    if text:
        try:
            win.addstr(y, x, text, attr)
        except curses.error:
            pass

def run_tui(stdscr):
    """Main TUI loop."""
    # Setup - CRITICAL for input handling
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.noecho()
    curses.raw()  # Raw mode so Ctrl+C comes through as char 3
    curses.use_default_colors()

    try:
        curses.init_pair(1, curses.COLOR_RED, -1)      # Red: titles, labels
        curses.init_pair(2, curses.COLOR_WHITE, -1)    # White: commands, values
        curses.init_pair(3, curses.COLOR_RED, -1)      # Red: search input
        curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_RED)  # White on Red: header/status
    except:
        pass

    # Load vaults and cheats
    vaults = load_vaults()
    current_vault = "default"
    cheats, tag_to_cheats, tags = load_cheats(vaults.get(current_vault, DEFAULT_CHEAT_PATHS))
    if not cheats:
        safe_addstr(stdscr, 0, 0, "No cheats found! Check ~/.cheats or aliasr installation")
        stdscr.getch()
        return

    # Load globals dynamically based on cheats
    globals_dict = load_globals(cheats)

    # State
    query = ""
    selected = 0
    scroll = 0
    current_tag_idx = 0  # Index into tags list, 0 = "all"
    current_tag = tags[0]  # "all"
    pool = cheats[:]  # Cheats in current tag
    filtered = cheats[:]  # Filtered by search
    focus = "search"  # "search" or "list"
    message = f"[{current_vault}] {len(cheats)} cheats"

    # Tree view state
    view_mode = "flat"  # "flat" or "tree"
    expanded = set()  # Set of expanded tool names
    tree_items = []  # List of (type, data) where type is "tool" or "cmd"

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # Filter tags by search query
        if query:
            q = query.lower()
            filtered_tags = [t for t in tags if q in t.lower() or t == "all"]
            if not filtered_tags:
                filtered_tags = ["all"]
        else:
            filtered_tags = tags

        # Ensure current tag is in filtered list, or switch to first match
        if current_tag not in filtered_tags:
            current_tag = filtered_tags[0]
            current_tag_idx = tags.index(current_tag)

        # Get cheats for current tag
        pool = tag_to_cheats.get(current_tag, cheats)

        # Filter cheats by search query
        if query:
            q = query.lower()
            filtered = [c for c in pool if q in c["title"].lower() or q in c["cmd"].lower()]
        else:
            filtered = pool[:]

        # Build tree view items if in tree mode
        if view_mode == "tree":
            tree, tools = build_tool_tree(filtered)
            tree_items = []
            for tool in tools:
                # Filter tools by query too
                if query and query.lower() not in tool.lower():
                    # Check if any commands match
                    if not tree.get(tool):
                        continue
                tree_items.append(("tool", tool, len(tree.get(tool, []))))
                if tool in expanded:
                    for c in tree.get(tool, []):
                        tree_items.append(("cmd", c, None))
            display_items = tree_items
        else:
            display_items = [("cmd", c, None) for c in filtered]

        # Clamp selection
        if display_items:
            selected = max(0, min(selected, len(display_items) - 1))
        else:
            selected = 0

        # Layout (header + tagbar + search + list + divider + preview*4 + status = 10)
        list_h = max(1, h - 10)

        # Scroll
        if selected < scroll:
            scroll = selected
        if selected >= scroll + list_h:
            scroll = selected - list_h + 1
        scroll = max(0, scroll)

        # Draw header with current tag (show tag count when filtering)
        tag_display = current_tag.split("/")[-1] if "/" in current_tag else current_tag
        mode_indicator = "TREE" if view_mode == "tree" else "FLAT"
        if query and len(filtered_tags) < len(tags):
            header = f" ARSENAL [{tag_display}] [{len(filtered)}/{len(pool)}] ({len(filtered_tags)} tags) [{mode_indicator}] "
        else:
            header = f" ARSENAL [{tag_display}] [{len(filtered)}/{len(pool)}] [{mode_indicator}] "
        safe_addstr(stdscr, 0, 0, header.center(w), curses.color_pair(4) | curses.A_BOLD)

        # Draw tag bar (sliding window centered on current, filtered by search)
        filtered_tag_idx = filtered_tags.index(current_tag) if current_tag in filtered_tags else 0
        max_visible = 12
        start = max(0, filtered_tag_idx - max_visible // 2)
        end = min(len(filtered_tags), start + max_visible)
        if end - start < max_visible:
            start = max(0, end - max_visible)

        tag_line = "◀ " if start > 0 else "  "
        for i in range(start, end):
            tag = filtered_tags[i]
            display = tag.split("/")[-1] if "/" in tag else tag
            if tag == current_tag:
                tag_line += f"[{display}] "
            else:
                tag_line += f" {display}  "
        if end < len(filtered_tags):
            tag_line += "▶"
        safe_addstr(stdscr, 1, 0, tag_line[:w-1], curses.color_pair(1))

        # Draw search (show cursor only when focused)
        if focus == "search":
            safe_addstr(stdscr, 2, 0, "> " + query + "█", curses.color_pair(3))
        else:
            safe_addstr(stdscr, 2, 0, "> " + query, curses.color_pair(2))

        # Draw list
        for i in range(list_h):
            y = 4 + i
            idx = scroll + i

            if idx >= len(display_items):
                # Clear empty rows
                safe_addstr(stdscr, y, 0, " " * (w - 1), 0)
                continue

            item_type, item_data, item_extra = display_items[idx]

            # Highlight selected item (brighter when list is focused)
            if idx == selected:
                if focus == "list":
                    attr = curses.A_REVERSE
                else:
                    attr = curses.A_UNDERLINE
            else:
                attr = 0

            if item_type == "tool":
                # Tool header row
                tool_name = item_data
                count = item_extra
                is_expanded = tool_name in expanded
                prefix = "▼ " if is_expanded else "▶ "
                display = f"{prefix}{tool_name} ({count})"
                safe_addstr(stdscr, y, 0, display.ljust(w - 1), curses.color_pair(1) | curses.A_BOLD | attr)
            else:
                # Command row
                c = item_data
                if view_mode == "tree":
                    # Indented for tree view
                    title = "  " + c["title"][:w//3-3]
                    cmd_preview = c["cmd"].replace("\n", " ")[:w*2//3-2]
                else:
                    title = c["title"][:w//3-1]
                    cmd_preview = c["cmd"].replace("\n", " ")[:w*2//3-2]

                safe_addstr(stdscr, y, 0, title.ljust(w//3), curses.color_pair(1) | attr)
                safe_addstr(stdscr, y, w//3, cmd_preview.ljust(w - w//3 - 1), curses.color_pair(2) | attr)

        # Draw preview
        preview_y = 4 + list_h
        safe_addstr(stdscr, preview_y, 0, "─" * w, curses.color_pair(1))

        # Calculate available preview lines (screen height - preview_y - title line - status line)
        preview_lines_avail = max(1, h - preview_y - 3)

        if display_items and selected < len(display_items):
            item_type, item_data, item_extra = display_items[selected]

            if item_type == "tool":
                # Show tool summary
                tool_name = item_data
                count = item_extra
                safe_addstr(stdscr, preview_y + 1, 0, f"{tool_name} - {count} commands", curses.color_pair(1) | curses.A_BOLD)
                safe_addstr(stdscr, preview_y + 2, 0, "Press Enter to expand/collapse", curses.color_pair(2))
            else:
                c = item_data
                safe_addstr(stdscr, preview_y + 1, 0, c["title"][:w-1], curses.color_pair(1) | curses.A_BOLD)

                # Show command with globals filled, wrapped to fit
                cmd = fill_params(c["cmd"], globals_dict)
                wrapped = wrap_text(cmd, w - 1)
                for i, line in enumerate(wrapped[:preview_lines_avail]):
                    safe_addstr(stdscr, preview_y + 2 + i, 0, line, curses.color_pair(2))

                # Show overflow indicator if command is too long
                if len(wrapped) > preview_lines_avail:
                    safe_addstr(stdscr, preview_y + 2 + preview_lines_avail, 0,
                               f"... ({len(wrapped) - preview_lines_avail} more lines)", curses.color_pair(1))

        # Draw status
        status = f" {message} | Enter:run  ^O:copy  ^V:view  ^P:vault  ^G:globals  q:quit "
        safe_addstr(stdscr, h - 1, 0, status[:w].ljust(w), curses.color_pair(4))

        stdscr.refresh()

        # Input
        try:
            ch = stdscr.getch()
        except:
            continue

        # Global keys (work in any focus mode)
        if ch == 17 or ch == 3 or ch == -1:  # Ctrl+Q or Ctrl+C or error = quit
            break

        elif ch == ord('q') and not query:  # 'q' quits when search empty
            break

        elif ch == ord('\t'):  # Tab = switch focus
            focus = "list" if focus == "search" else "search"

        elif ch == 27:  # Escape = clear search or switch to search
            if query:
                query = ""
                selected = 0
                scroll = 0
            else:
                focus = "search"

        elif ch == ord('\n'):  # Enter = expand/collapse tool OR run command
            if display_items and selected < len(display_items):
                item_type, item_data, item_extra = display_items[selected]
                if item_type == "tool":
                    # Toggle expand/collapse
                    tool_name = item_data
                    if tool_name in expanded:
                        expanded.remove(tool_name)
                    else:
                        expanded.add(tool_name)
                else:
                    # Run command
                    c = item_data
                    cmd = interactive_params(stdscr, c["cmd"], globals_dict)
                    if cmd is None:
                        message = "Cancelled"
                    elif send_tmux(cmd, execute=True):
                        message = "Sent to tmux!"
                    elif copy_cmd(cmd):
                        message = "Copied!"
                    else:
                        message = "Failed"

        elif ch == 15:  # Ctrl+O = copy with params (always clipboard, never tmux)
            if display_items and selected < len(display_items):
                item_type, item_data, _ = display_items[selected]
                if item_type == "cmd":
                    c = item_data
                    cmd = interactive_params(stdscr, c["cmd"], globals_dict)
                    if cmd is None:
                        message = "Cancelled"
                    elif copy_cmd(cmd):
                        message = "Copied!"
                    else:
                        message = "Copy failed"

        elif ch == 22:  # Ctrl+V = toggle view mode
            view_mode = "flat" if view_mode == "tree" else "tree"
            selected = 0
            scroll = 0
            expanded.clear()
            message = f"View: {view_mode}"

        elif ch == 16:  # Ctrl+P = pick vault/playbook
            new_vault = pick_vault(stdscr, current_vault)
            if new_vault and new_vault != current_vault:
                current_vault = new_vault
                vaults = load_vaults()  # Refresh vaults
                cheats, tag_to_cheats, tags = load_cheats(vaults.get(current_vault, DEFAULT_CHEAT_PATHS))
                globals_dict = load_globals(cheats)
                current_tag = "all"
                current_tag_idx = 0
                selected = 0
                scroll = 0
                query = ""
                expanded.clear()
                message = f"[{current_vault}] {len(cheats)} cheats"
            elif new_vault is None:
                message = "Cancelled"

        elif ch == 25:  # Ctrl+Y = yank raw (no param editing)
            if display_items and selected < len(display_items):
                item_type, item_data, _ = display_items[selected]
                if item_type == "cmd":
                    if copy_cmd(item_data["cmd"]):
                        message = "Copied raw!"
                    else:
                        message = "Copy failed"

        elif ch == 7:  # Ctrl+G = globals editor
            edit_globals(stdscr, globals_dict)
            message = "Globals updated"

        elif ch == 1:  # Ctrl+A = add new cheat
            if add_cheat(stdscr, globals_dict):
                # Reload cheats
                cheats, tag_to_cheats, tags = load_cheats()
                globals_dict = load_globals(cheats)
                save_globals(globals_dict)
                current_tag = "all"
                current_tag_idx = 0
                selected = 0
                scroll = 0
                query = ""
                message = "Cheat added!"
            else:
                message = "Cancelled"

        elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
            query = query[:-1]
            selected = 0
            scroll = 0

        # Typing ALWAYS goes to search (global)
        elif 32 <= ch <= 126:
            query += chr(ch)
            selected = 0
            scroll = 0
            focus = "search"

        # Arrow keys - category navigation (works in both modes)
        elif ch == curses.KEY_RIGHT:
            if current_tag in filtered_tags:
                idx = filtered_tags.index(current_tag)
                idx = (idx + 1) % len(filtered_tags)
                current_tag = filtered_tags[idx]
                current_tag_idx = tags.index(current_tag)
            selected = 0
            scroll = 0

        elif ch == curses.KEY_LEFT:
            if current_tag in filtered_tags:
                idx = filtered_tags.index(current_tag)
                idx = (idx - 1) % len(filtered_tags)
                current_tag = filtered_tags[idx]
                current_tag_idx = tags.index(current_tag)
            selected = 0
            scroll = 0

        # Up/Down - list navigation (use display_items for both flat and tree view)
        elif ch == curses.KEY_DOWN:
            if focus == "search":
                focus = "list"
            else:
                selected = min(selected + 1, len(display_items) - 1) if display_items else 0

        elif ch == curses.KEY_UP:
            if focus == "list" and selected > 0:
                selected -= 1
            else:
                focus = "search"

        elif ch == curses.KEY_NPAGE or ch == 4:  # Page Down
            focus = "list"
            selected = min(selected + list_h, len(display_items) - 1) if display_items else 0

        elif ch == curses.KEY_PPAGE or ch == 21:  # Page Up
            selected = max(selected - list_h, 0)

def pick_vault(stdscr, current_vault):
    """Vault/playbook picker. Returns selected vault name or None if cancelled."""
    stdscr.keypad(True)
    vaults = load_vaults()
    vault_names = list(vaults.keys())

    # Put current vault first, then sort the rest
    if current_vault in vault_names:
        vault_names.remove(current_vault)
        vault_names = [current_vault] + sorted(vault_names)
    else:
        vault_names = sorted(vault_names)

    selected = 0
    scroll = 0
    query = ""

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # Filter vaults by query
        if query:
            q = query.lower()
            filtered = [v for v in vault_names if q in v.lower()]
        else:
            filtered = vault_names[:]

        # Clamp selection
        if filtered:
            selected = max(0, min(selected, len(filtered) - 1))
        else:
            selected = 0

        list_h = max(1, h - 6)

        # Scroll
        if selected < scroll:
            scroll = selected
        if selected >= scroll + list_h:
            scroll = selected - list_h + 1
        scroll = max(0, scroll)

        # Header
        safe_addstr(stdscr, 0, 0, " SELECT VAULT/PLAYBOOK ".center(w), curses.color_pair(4) | curses.A_BOLD)

        # Search
        safe_addstr(stdscr, 2, 0, "> " + query + "█", curses.color_pair(3))

        # List
        for i in range(list_h):
            y = 4 + i
            idx = scroll + i

            if idx >= len(filtered):
                safe_addstr(stdscr, y, 0, " " * (w - 1), 0)
                continue

            vault_name = filtered[idx]
            paths = vaults.get(vault_name, [])
            path_str = str(paths[0]) if paths else "?"

            # Count cheats in this vault (expensive, but cached display)
            is_current = vault_name == current_vault

            if idx == selected:
                attr = curses.A_REVERSE
            else:
                attr = 0

            prefix = "● " if is_current else "  "
            display = f"{prefix}{vault_name}"
            safe_addstr(stdscr, y, 0, display[:w//3].ljust(w//3), curses.color_pair(1) | curses.A_BOLD | attr)
            safe_addstr(stdscr, y, w//3, path_str[:w*2//3-1], curses.color_pair(2) | attr)

        # Status
        status = " Enter:select  Esc:cancel "
        safe_addstr(stdscr, h - 1, 0, status.center(w), curses.color_pair(4))

        stdscr.refresh()
        ch = stdscr.getch()

        if ch == 27:  # Esc
            return None
        elif ch == ord('\n'):
            if filtered:
                return filtered[selected]
            return None
        elif ch == curses.KEY_DOWN:
            selected = min(selected + 1, len(filtered) - 1) if filtered else 0
        elif ch == curses.KEY_UP:
            selected = max(selected - 1, 0)
        elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
            query = query[:-1]
            selected = 0
            scroll = 0
        elif 32 <= ch <= 126:
            query += chr(ch)
            selected = 0
            scroll = 0

def edit_globals(stdscr, globals_dict):
    """Globals editor with search and scroll."""
    stdscr.keypad(True)
    all_keys = list(globals_dict.keys())
    filtered_keys = all_keys[:]
    selected = 0
    scroll = 0
    query = ""
    editing = False
    edit_buf = ""

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # Filter keys by search query
        if query:
            q = query.lower()
            filtered_keys = [k for k in all_keys if q in k.lower() or q in globals_dict[k].lower()]
        else:
            filtered_keys = all_keys[:]

        # Clamp selection
        if filtered_keys:
            selected = max(0, min(selected, len(filtered_keys) - 1))
        else:
            selected = 0

        list_h = h - 5  # Room for header, search, footer

        # Handle scroll
        if selected < scroll:
            scroll = selected
        if selected >= scroll + list_h:
            scroll = selected - list_h + 1
        scroll = max(0, scroll)

        # Header
        safe_addstr(stdscr, 0, 0, f" GLOBALS [{len(filtered_keys)}/{len(all_keys)}] ".center(w), curses.color_pair(4) | curses.A_BOLD)

        # Search bar (only show when not editing a value)
        if not editing:
            safe_addstr(stdscr, 1, 0, "> " + query + "█", curses.color_pair(3))
        else:
            safe_addstr(stdscr, 1, 0, f"  (editing {filtered_keys[selected]})", curses.color_pair(1))

        # List
        for i in range(list_h):
            idx = scroll + i
            if idx >= len(filtered_keys):
                break

            y = 3 + i
            key = filtered_keys[idx]
            val = globals_dict[key]
            is_sel = idx == selected

            label = f"{key}: ".rjust(22)

            if is_sel and editing:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1))
                safe_addstr(stdscr, y, 22, edit_buf + "█", curses.color_pair(3))
            elif is_sel:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1) | curses.A_REVERSE)
                safe_addstr(stdscr, y, 22, val or "<empty>", curses.A_REVERSE)
            else:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1))
                safe_addstr(stdscr, y, 22, val or "<empty>", curses.color_pair(2) if val else 0)

        # Status
        if editing:
            status = " typing... Enter:done  Esc:cancel "
        else:
            status = " type:search  Enter:edit  ^S:save  Esc:back "
        safe_addstr(stdscr, h - 1, 0, status.center(w), curses.color_pair(4))

        stdscr.refresh()

        ch = stdscr.getch()

        if editing:
            if ch == ord('\n'):
                globals_dict[filtered_keys[selected]] = edit_buf
                editing = False
            elif ch == 27:
                editing = False
            elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
                edit_buf = edit_buf[:-1]
            elif 32 <= ch <= 126:
                edit_buf += chr(ch)
        else:
            if ch == 27 or ch == 17:  # Esc or Ctrl+Q = exit
                break
            elif ch == curses.KEY_DOWN:
                selected = min(selected + 1, len(filtered_keys) - 1) if filtered_keys else 0
            elif ch == curses.KEY_UP:
                selected = max(selected - 1, 0)
            elif ch == curses.KEY_NPAGE or ch == 4:  # Page Down or Ctrl+D
                selected = min(selected + list_h, len(filtered_keys) - 1) if filtered_keys else 0
            elif ch == curses.KEY_PPAGE or ch == 21:  # Page Up or Ctrl+U
                selected = max(selected - list_h, 0)
            elif ch == ord('\n'):  # Enter = edit selected
                if filtered_keys:
                    editing = True
                    edit_buf = globals_dict[filtered_keys[selected]]
            elif ch == 19:  # Ctrl+S = save and exit
                save_globals(globals_dict)
                break
            elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
                query = query[:-1]
                selected = 0
                scroll = 0
            elif 32 <= ch <= 126:  # Printable = search
                query += chr(ch)
                selected = 0
                scroll = 0

def interactive_params(stdscr, cmd, globals_dict):
    """Interactive parameter editor. Returns filled command or None if cancelled."""
    params = get_params(cmd)
    if not params:
        return fill_params(cmd, globals_dict)

    # Build local overrides starting with global values
    overrides = {p: globals_dict.get(p, "") for p in params}

    selected = 0
    editing = False
    edit_buf = ""

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # Header
        safe_addstr(stdscr, 0, 0, " PARAMETERS (e:edit, Enter:run, Esc:cancel) ".center(w), curses.color_pair(4) | curses.A_BOLD)

        # Show command preview (wrapped)
        preview_cmd = fill_params(cmd, {**globals_dict, **overrides})
        wrapped = wrap_text(preview_cmd, w - 2)
        for i, line in enumerate(wrapped[:4]):
            safe_addstr(stdscr, 2 + i, 1, line, curses.color_pair(2))

        # Divider
        param_start_y = 7
        safe_addstr(stdscr, param_start_y - 1, 0, "─" * w, curses.color_pair(1))

        # Parameter list
        for i, param in enumerate(params):
            y = param_start_y + i
            if y >= h - 2:
                break

            val = overrides[param]
            is_sel = i == selected
            label = f"{param}: ".rjust(20)

            if is_sel and editing:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1))
                safe_addstr(stdscr, y, 20, edit_buf + "█", curses.color_pair(3))
            elif is_sel:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1) | curses.A_REVERSE)
                safe_addstr(stdscr, y, 20, val or "<empty>", curses.A_REVERSE)
            else:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1))
                safe_addstr(stdscr, y, 20, val or "<empty>", curses.color_pair(2) if val else 0)

        # Status
        if editing:
            status = " Enter:done  Esc:cancel "
        else:
            status = " ↑↓:nav  e:edit  Enter:run  Esc:cancel "
        safe_addstr(stdscr, h - 1, 0, status.center(w), curses.color_pair(4))

        stdscr.refresh()
        ch = stdscr.getch()

        if editing:
            if ch == ord('\n'):
                overrides[params[selected]] = edit_buf
                editing = False
            elif ch == 27:
                editing = False
            elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
                edit_buf = edit_buf[:-1]
            elif 32 <= ch <= 126:
                edit_buf += chr(ch)
        else:
            if ch == 27:  # Esc = cancel
                return None
            elif ch == ord('\n'):  # Enter = execute with current values
                return fill_params(cmd, {**globals_dict, **overrides})
            elif ch == curses.KEY_DOWN:
                selected = min(selected + 1, len(params) - 1)
            elif ch == curses.KEY_UP:
                selected = max(selected - 1, 0)
            elif ch == ord('e'):  # e = edit selected param
                editing = True
                edit_buf = overrides[params[selected]]

def add_cheat(stdscr, globals_dict):
    """Add a new cheat command. Returns True if cheat was added."""
    CUSTOM_FILE = Path.home() / ".cheats" / "custom.md"

    fields = ["title", "command", "tags"]
    values = {"title": "", "command": "", "tags": ""}
    selected = 0
    editing = False
    edit_buf = ""

    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()

        # Header
        safe_addstr(stdscr, 0, 0, " ADD NEW CHEAT (e:edit, Enter:save, Esc:cancel) ".center(w), curses.color_pair(4) | curses.A_BOLD)

        # Instructions
        safe_addstr(stdscr, 2, 2, "Title: Name for the command", curses.color_pair(1))
        safe_addstr(stdscr, 3, 2, "Command: Use <param> for variables (e.g., nmap <ip>)", curses.color_pair(1))
        safe_addstr(stdscr, 4, 2, "Tags: Space-separated (e.g., cat/recon cat/nmap)", curses.color_pair(1))

        # Divider
        safe_addstr(stdscr, 6, 0, "─" * w, curses.color_pair(1))

        # Fields
        for i, field in enumerate(fields):
            y = 8 + i * 2
            val = values[field]
            is_sel = i == selected
            label = f"{field}: ".rjust(12)

            if is_sel and editing:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1))
                # Show wrapped edit buffer for command
                if field == "command":
                    lines = edit_buf.split("\\n")
                    safe_addstr(stdscr, y, 12, lines[0] + "█", curses.color_pair(3))
                    for j, line in enumerate(lines[1:], 1):
                        if y + j < h - 2:
                            safe_addstr(stdscr, y + j, 12, line, curses.color_pair(3))
                else:
                    safe_addstr(stdscr, y, 12, edit_buf + "█", curses.color_pair(3))
            elif is_sel:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1) | curses.A_REVERSE)
                display = val.replace("\n", "\\n") if val else "<empty>"
                safe_addstr(stdscr, y, 12, display[:w-14], curses.A_REVERSE)
            else:
                safe_addstr(stdscr, y, 0, label, curses.color_pair(1))
                display = val.replace("\n", "\\n") if val else "<empty>"
                safe_addstr(stdscr, y, 12, display[:w-14], curses.color_pair(2) if val else 0)

        # Preview
        if values["title"] and values["command"]:
            safe_addstr(stdscr, 16, 0, "─" * w, curses.color_pair(1))
            safe_addstr(stdscr, 17, 2, "Preview:", curses.color_pair(1) | curses.A_BOLD)
            safe_addstr(stdscr, 18, 2, f"## {values['title']}", curses.color_pair(2))
            cmd_lines = values["command"].split("\n")
            for i, line in enumerate(cmd_lines[:3]):
                safe_addstr(stdscr, 19 + i, 2, line[:w-4], curses.color_pair(2))

        # Status
        if editing:
            if selected == 1:  # command field
                status = " Enter:newline  ^D:done  Esc:cancel "
            else:
                status = " Enter:done  Esc:cancel "
        else:
            status = " ↑↓:nav  e:edit  Enter:save  Esc:cancel "
        safe_addstr(stdscr, h - 1, 0, status.center(w), curses.color_pair(4))

        stdscr.refresh()
        ch = stdscr.getch()

        if editing:
            if selected == 1:  # command field - special handling
                if ch == 4:  # Ctrl+D = done editing command
                    values["command"] = edit_buf.replace("\\n", "\n")
                    editing = False
                elif ch == ord('\n'):  # Enter = newline in command
                    edit_buf += "\\n"
                elif ch == 27:
                    editing = False
                elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
                    edit_buf = edit_buf[:-1]
                elif 32 <= ch <= 126:
                    edit_buf += chr(ch)
            else:
                if ch == ord('\n'):
                    values[fields[selected]] = edit_buf
                    editing = False
                elif ch == 27:
                    editing = False
                elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
                    edit_buf = edit_buf[:-1]
                elif 32 <= ch <= 126:
                    edit_buf += chr(ch)
        else:
            if ch == 27:  # Esc = cancel
                return False
            elif ch == ord('\n'):  # Enter = save
                if not values["title"] or not values["command"]:
                    continue  # Need at least title and command

                # Ensure ~/.cheats exists
                CUSTOM_FILE.parent.mkdir(parents=True, exist_ok=True)

                # Build markdown entry
                entry = f"\n## {values['title']}\n"
                if values["tags"]:
                    for tag in values["tags"].split():
                        entry += f"#{tag} "
                    entry += "\n"
                entry += "```\n"
                entry += values["command"]
                entry += "\n```\n"

                # Append to custom file
                with open(CUSTOM_FILE, "a") as f:
                    f.write(entry)

                # Extract new params and add to globals
                for param in get_params(values["command"]):
                    if param not in globals_dict:
                        globals_dict[param] = ""

                return True

            elif ch == curses.KEY_DOWN:
                selected = min(selected + 1, len(fields) - 1)
            elif ch == curses.KEY_UP:
                selected = max(selected - 1, 0)
            elif ch == ord('e'):
                editing = True
                edit_buf = values[fields[selected]].replace("\n", "\\n")

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "scan" and len(sys.argv) > 2:
            g = load_globals_simple()
            g["ip"] = sys.argv[2]
            save_globals(g)
            print(f"Set ip={sys.argv[2]}")
            return 0
        elif sys.argv[1] == "--help" or sys.argv[1] == "-h":
            print("arsenal - Native terminal cheat launcher")
            print("")
            print("Usage:")
            print("  arsenal              Launch TUI")
            print("  arsenal scan <ip>    Set target IP")
            print("")
            print("Keys:")
            print("  ←/→         Switch category")
            print("  ↑/↓         Navigate commands")
            print("  Enter       Run command (tmux or copy)")
            print("  Ctrl+O      Copy to clipboard (always, even in tmux)")
            print("  Ctrl+V      Toggle flat/tree view")
            print("  Ctrl+P      Switch vault/playbook")
            print("  Ctrl+Y      Yank raw command (no param editing)")
            print("  Ctrl+A      Add new cheat command")
            print("  Ctrl+G      Edit global variables")
            print("  Ctrl+D/U    Page down/up")
            print("  Esc         Clear search")
            print("  q/Ctrl+C    Quit")
            return 0

    try:
        curses.wrapper(run_tui)
    except KeyboardInterrupt:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
