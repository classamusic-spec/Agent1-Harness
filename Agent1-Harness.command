#!/usr/bin/env bash
# Double-click this file in Finder to start Agent1-Harness.
# First run sets everything up; later runs just launch the console + browser.
cd "$(dirname "$0")" || exit 1

clear
printf "\033[1;35m✦ Agent1-Harness\033[0m — starting up…\n\n"

# First-time setup if the virtualenv isn't there yet.
if [ ! -d .venv ]; then
  printf "First run — setting up (Python venv + harness). This takes a minute…\n\n"
  if ! ./scripts/install-mac.sh; then
    printf "\n\033[1;31mSetup failed.\033[0m See the messages above. Press any key to close.\n"
    read -r -n 1; exit 1
  fi
fi

# Launch (start-mac.sh activates the venv, opens the browser, and serves).
./scripts/start-mac.sh

# Keep the Terminal window readable if the server exits.
printf "\nServer stopped. Press any key to close this window.\n"
read -r -n 1
