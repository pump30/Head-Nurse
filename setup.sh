#!/bin/bash
set -euo pipefail

echo "=== GitHub Kanban Agent Setup ==="
echo ""

# Configurable
DEFAULT_REPO_NAME="kanban-tasks"
PROJECT_TITLE="Kanban Agent"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Get GitHub username
GH_USER=$(gh api user --jq '.login')
echo "GitHub user: ${GH_USER}"

REPO="${GH_USER}/${DEFAULT_REPO_NAME}"
read -p "Repository name [${REPO}]: " INPUT_REPO
REPO="${INPUT_REPO:-$REPO}"

# Step 1: Ensure gh has project scope
echo ""
echo "Step 1: Ensuring 'project' scope on gh token..."
if ! gh auth status 2>&1 | grep -q "project"; then
    gh auth refresh -s project,read:project
    echo "  ✓ Added project scope"
else
    echo "  ✓ Already has project scope"
fi

# Step 2: Create repo
echo ""
echo "Step 2: Creating repository ${REPO} (private)..."
if gh repo view "$REPO" &>/dev/null; then
    echo "  ✓ Repository already exists"
else
    gh repo create "$REPO" --private --description "Task queue for Kanban Agent"
    echo "  ✓ Created"
fi

# Step 3: Create Project V2
echo ""
echo "Step 3: Creating Project V2 board..."
PROJECT_NUM=$(gh project list --owner "@me" --format json | \
    python3 -c "import sys,json; projects=json.load(sys.stdin).get('projects',[]); matches=[p for p in projects if p['title']=='${PROJECT_TITLE}']; print(matches[0]['number'] if matches else '')" 2>/dev/null || echo "")

if [ -z "$PROJECT_NUM" ]; then
    PROJECT_URL=$(gh project create --owner "@me" --title "$PROJECT_TITLE" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['url'])")
    PROJECT_NUM=$(echo "$PROJECT_URL" | grep -oE '[0-9]+$')
    echo "  ✓ Created project #${PROJECT_NUM}"
else
    echo "  ✓ Project already exists (#${PROJECT_NUM})"
fi

# Step 4: Link repo to project
echo ""
echo "Step 4: Linking repo to project..."
gh project link "$PROJECT_NUM" --owner "@me" --repo "$REPO" 2>/dev/null || echo "  (already linked or link not needed)"
echo "  ✓ Done"

# Step 5: Configure Status field
echo ""
echo "Step 5: Status field configuration"
echo "  ⚠️  Please manually set the Status field options in GitHub UI to:"
echo "     Inbox | In Progress | Completed | Failed"
echo "  Project URL: https://github.com/users/${GH_USER}/projects/${PROJECT_NUM}"
echo ""
read -p "  Press Enter when done (or skip for now)..."

# Step 6: Create config
echo ""
echo "Step 6: Creating configuration..."
CONFIG_DIR="$HOME/.config/kanban-agent"
mkdir -p "$CONFIG_DIR"

if [ -f "$CONFIG_DIR/config.yaml" ]; then
    echo "  ⚠️  Config already exists at $CONFIG_DIR/config.yaml"
    read -p "  Overwrite? [y/N]: " OVERWRITE
    if [ "${OVERWRITE,,}" != "y" ]; then
        echo "  Skipped"
    fi
fi

if [ ! -f "$CONFIG_DIR/config.yaml" ] || [ "${OVERWRITE,,}" = "y" ]; then
    cat > "$CONFIG_DIR/config.yaml" <<YAML
repo: "${REPO}"
project_number: ${PROJECT_NUM}
poll_interval_seconds: 30
claude_working_dir: "${HOME}/Projects"
claude_permission_mode: "acceptEdits"
task_timeout_seconds: 600
max_budget_per_task_usd: 1.0
log_file: "${HOME}/Library/Logs/kanban-agent.log"
log_level: "INFO"
state_file: "${HOME}/.local/state/kanban-agent/state.json"
YAML
    chmod 600 "$CONFIG_DIR/config.yaml"
    echo "  ✓ Config written to $CONFIG_DIR/config.yaml"
fi

# Step 7: Install pip dependencies
echo ""
echo "Step 7: Installing dependencies..."
pip3 install pyyaml --quiet 2>/dev/null || pip install pyyaml --quiet
echo "  ✓ Done"

# Step 8: Install Launchd plist
echo ""
echo "Step 8: Installing Launchd daemon..."
PLIST_SRC="${INSTALL_DIR}/launchd/com.kanban-agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.kanban-agent.plist"

sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Unload if already loaded
launchctl bootout "gui/$(id -u)/com.kanban-agent" 2>/dev/null || true

launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "  ✓ Launchd daemon installed and started"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "The agent is now running. To test:"
echo "  1. Go to: https://github.com/${REPO}/issues/new"
echo "  2. Create an issue with a prompt as the body"
echo "  3. Watch the Project board update"
echo ""
echo "Useful commands:"
echo "  Logs:    tail -f ~/Library/Logs/kanban-agent.log"
echo "  Stop:    launchctl bootout gui/$(id -u)/com.kanban-agent"
echo "  Restart: launchctl kickstart -k gui/$(id -u)/com.kanban-agent"
