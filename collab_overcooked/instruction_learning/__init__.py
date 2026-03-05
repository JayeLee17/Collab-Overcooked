"""
Instruction Learning Module
指令学习模块：支持 LLM 和规则模板两种方式生成指令
"""

from .llm_instructor import LLMInstructor
from .rule_template import RuleTemplateEngine

__all__ = ["LLMInstructor", "RuleTemplateEngine"]
