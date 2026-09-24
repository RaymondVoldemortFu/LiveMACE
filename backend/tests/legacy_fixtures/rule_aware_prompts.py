"""Deprecated compatibility exports for Rule-Aware Prompt templates."""

from benchmark.builtin.prompts import read_builtin_template


RULE_AWARE_SYSTEM_PROMPT = read_builtin_template("rule-aware/system.txt")
RULE_AWARE_REMINDER_PROMPT = read_builtin_template("rule-aware/reminder.txt")


__all__ = ["RULE_AWARE_REMINDER_PROMPT", "RULE_AWARE_SYSTEM_PROMPT"]
