# core/colors.py
# ANSI color helpers for console/log output. Convention used across this bot:
#   red    = danger / failure (rejected orders, errors, SL hit)
#   green  = good / success (order placed, TP hit, connected)
#   yellow = processing / waiting (starting up, monitoring, in-progress)

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def red(text):
    return f"{RED}{text}{RESET}"


def green(text):
    return f"{GREEN}{text}{RESET}"


def yellow(text):
    return f"{YELLOW}{text}{RESET}"
